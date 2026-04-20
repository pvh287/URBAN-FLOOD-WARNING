"""
AI Predictor - Multi-station flood prediction & MQTT
Per-station deques; publish ai/prediction/{station_id}
"""

import logging
import json
import os
import time
import numpy as np
import requests
import joblib
import paho.mqtt.client as mqtt
from collections import deque, defaultdict
from datetime import datetime
from threading import Lock
from tensorflow import keras
from config import (
    MQTT_BROKER, MQTT_PORT, MQTT_KEEPALIVE,
    TOPIC_DATA, TOPIC_FLOOD_MONITOR_WILDCARD, TOPIC_BUZZER_CMD,
    TOPIC_AI_PREDICTION_PREFIX, TOPIC_SENSOR_COMMAND,
    OPEN_METEO_API_URL, API_TIMEOUT, API_CALL_INTERVAL,
    LAT, LON, TOPO_INDEX,
    LSTM_TIME_STEPS, LSTM_FEATURES, OUTPUT_CLASSES,
    MODEL_FILE,
    CLASS_SAFE, CLASS_WARNING, CLASS_FLOOD, FLOOD_ALERT_THRESHOLD,
    DEVICE_ID
)

logger = logging.getLogger(__name__)

SCALER_PATH = os.path.join(os.path.dirname(__file__), '..', 'models', 'scaler.pkl')


def _station_topic_from_id(station_id: str) -> str:
    return f"{TOPIC_AI_PREDICTION_PREFIX}/{station_id}"


def _parse_station_from_flood_topic(topic: str):
    """flood/monitor/{station_id}/data -> station_id or None."""
    parts = topic.split('/')
    if len(parts) == 4 and parts[0] == 'flood' and parts[1] == 'monitor' and parts[3] == 'data':
        return parts[2]
    return None


class AIPredictor:
    """Multi-station real-time flood prediction."""

    def __init__(self):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        self.lock = Lock()
        self.station_deques = {}
        self.prediction_count = defaultdict(int)

        self.weather_data = {
            'rain_forecast': 0,
            'time_decay': 0,
            'topo_index': TOPO_INDEX,
            'last_api_call': None,
            'api_success': False
        }

        self.model = None
        self.scaler = None
        self.load_model()
        self.load_scaler()

        self.last_prediction_global = None
        self.last_command = None

        logger.info("AIPredictor initialized (multi-station)")

    def _get_deque(self, station_id: str) -> deque:
        if station_id not in self.station_deques:
            self.station_deques[station_id] = deque(maxlen=LSTM_TIME_STEPS)
        return self.station_deques[station_id]

    def load_model(self):
        try:
            logger.info(f"Loading model from: {MODEL_FILE}")
            self.model = keras.models.load_model(MODEL_FILE)
            logger.info("[OK] Model loaded successfully")
        except FileNotFoundError:
            logger.warning(f"Model file not found: {MODEL_FILE}")
            self.model = None
        except Exception as e:
            logger.error(f"Error loading model: {e}")
            self.model = None

    def load_scaler(self):
        base_dir = os.path.dirname(__file__)
        primary = os.path.normpath(SCALER_PATH)
        fallback = os.path.normpath(os.path.join(base_dir, 'models', 'scaler.pkl'))
        scaler_path = primary if os.path.isfile(primary) else fallback
        try:
            logger.info(f"Loading scaler from: {scaler_path}")
            self.scaler = joblib.load(scaler_path)
            logger.info(f"[OK] Scaler loaded from: {os.path.abspath(scaler_path)}")
        except FileNotFoundError:
            logger.warning(f"Scaler file not found: {scaler_path}")
            self.scaler = None
        except Exception as e:
            logger.error(f"Error loading scaler: {e}")
            self.scaler = None

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"[OK] Connected to MQTT broker ({MQTT_BROKER}:{MQTT_PORT})")
            try:
                client.subscribe([
                    (TOPIC_FLOOD_MONITOR_WILDCARD, 0),
                    (TOPIC_DATA, 0),
                    (TOPIC_SENSOR_COMMAND, 0),
                ])
                logger.info(
                    f"[OK] Subscribed: {TOPIC_FLOOD_MONITOR_WILDCARD}, {TOPIC_DATA}, {TOPIC_SENSOR_COMMAND}"
                )
            except Exception as e:
                logger.error(f"Error subscribing: {e}")
        else:
            logger.error(f"[FAILED] MQTT connection failed. RC: {rc}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode('utf-8')
            if msg.topic == TOPIC_SENSOR_COMMAND:
                self._handle_sensor_command(payload)
                return
            sid = _parse_station_from_flood_topic(msg.topic)
            if sid:
                self._ingest_sensor_sample(sid, payload)
                return
            if msg.topic == TOPIC_DATA:
                self._ingest_sensor_sample('LEGACY', payload)
        except UnicodeDecodeError as e:
            logger.error(f"MQTT payload decode error: {e}")
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    def _handle_sensor_command(self, payload):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as e:
            logger.error(f"[CMD] JSON decode error: {e}")
            return

        action = data.get('action')
        if action == 'toggle_buzzer':
            val = str(data.get('value', '')).strip().upper()
            if val in ('ON', 'OFF'):
                logger.info(f"[CMD] toggle_buzzer: {val}")
                self.send_buzzer_command(val)
            else:
                logger.warning(f"[CMD] toggle_buzzer bad value: {data!r}")
        elif action == 'reset_ai':
            sid = data.get('station_id')
            logger.info(f"[CMD] reset_ai station_id={sid!r}")
            self.reset_ai_memory(station_id=sid if sid else None)
        else:
            logger.warning(f"[CMD] Unknown action: {action}")

    def reset_ai_memory(self, station_id=None):
        with self.lock:
            if station_id:
                if station_id in self.station_deques:
                    self.station_deques[station_id].clear()
                self.prediction_count[station_id] = 0
                logger.info(f"[RESET] Buffer cleared for station {station_id}")
            else:
                self.station_deques.clear()
                self.prediction_count.clear()
                logger.info("[RESET] All station buffers cleared")

    def _ingest_sensor_sample(self, station_id: str, payload: str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as e:
            logger.error(f"[DATA:{station_id}] JSON error: {e}")
            return

        if station_id == 'LEGACY':
            station_id = str(data.get('station_id', 'LEGACY'))

        level = float(data.get('level', 0.0))
        flow = float(data.get('flow', 0.0))
        rain_local = float(data.get('rain', 0.0))

        dq = self._get_deque(station_id)
        with self.lock:
            if len(dq) > 0:
                prev_level = dq[-1]['level']
                delta_level = level - prev_level
            else:
                delta_level = 0
            flow_efficiency = flow / (level + 1)
            dq.append({
                'timestamp': datetime.now(),
                'level': level,
                'flow': flow,
                'flow_efficiency': flow_efficiency,
                'delta_level': delta_level,
                'rain_local': rain_local
            })

        logger.debug(
            f"[{station_id}] level={level:.1f} flow={flow:.3f} q={len(dq)}/{LSTM_TIME_STEPS}"
        )

    def build_prediction_mqtt_json(self, result, station_id: str):
        probs = result['probabilities']
        ts = result['timestamp']
        ts_iso = ts.isoformat() if hasattr(ts, 'isoformat') else datetime.now().isoformat()

        status_map = {
            CLASS_SAFE: 'AN TOAN',
            CLASS_WARNING: 'CANH BAO',
            CLASS_FLOOD: 'NGAP LUT',
        }
        predicted_class = int(result['class'])
        status = status_map.get(predicted_class, 'AN TOAN')
        conf_pct = round(float(result['confidence']) * 100, 1)

        level = float(result.get('level', 0.0))
        flow = float(result.get('flow', 0.0))
        rain = float(result.get('rain', 0.0))

        return {
            'station_id': station_id,
            'status': status,
            'confidence': conf_pct,
            'probs': {
                'safe': round(float(probs[CLASS_SAFE]) * 100, 1),
                'warning': round(float(probs[CLASS_WARNING]) * 100, 1),
                'flood': round(float(probs[CLASS_FLOOD]) * 100, 1),
            },
            'timestamp': ts_iso,
            'level': round(level, 1),
            'flow': round(flow, 3),
            'rain': round(rain, 2),
        }

    def publish_prediction_mqtt(self, result, station_id: str):
        try:
            payload = self.build_prediction_mqtt_json(result, station_id)
            body = json.dumps(payload, ensure_ascii=False)
            topic = _station_topic_from_id(station_id)
            self.client.publish(topic, body, qos=1, retain=False)
            logger.info(f"[MQTT] Published → {topic}")
        except Exception as e:
            logger.error(f"[MQTT] Publish failed: {e}")

    def fetch_weather(self):
        try:
            current_time = datetime.now()
            if (self.weather_data['last_api_call'] is not None and
                    (current_time - self.weather_data['last_api_call']).total_seconds() < API_CALL_INTERVAL):
                return

            logger.info("[API] Fetching weather from Open-Meteo...")
            params = {
                'latitude': LAT,
                'longitude': LON,
                'hourly': 'precipitation',
                'forecast_hours': 2,
                'timezone': 'auto'
            }
            response = requests.get(OPEN_METEO_API_URL, params=params, timeout=API_TIMEOUT)
            response.raise_for_status()
            data = response.json()

            if 'hourly' in data and 'precipitation' in data['hourly']:
                precipitation = data['hourly']['precipitation']
                rain_forecast = precipitation[1] if len(precipitation) > 1 else precipitation[0]
            else:
                rain_forecast = 0

            self.weather_data['rain_forecast'] = rain_forecast
            self.weather_data['last_api_call'] = current_time
            self.weather_data['api_success'] = True
            logger.info(f"[OK] Open-Meteo rain_forecast={rain_forecast:.2f} mm/h")
        except Exception as e:
            logger.error(f"[API] Weather fetch error: {e}")
            self.weather_data['api_success'] = False

    def _calculate_time_decay(self):
        if self.weather_data['last_api_call'] is None:
            return 1
        elapsed = (datetime.now() - self.weather_data['last_api_call']).total_seconds() / 60
        return min(int(elapsed) + 1, 14)

    def mock_predict(self):
        return np.random.dirichlet(np.ones(OUTPUT_CLASSES))

    def predict_flood_risk(self, station_id: str):
        try:
            with self.lock:
                dq = self.station_deques.get(station_id)
                n = len(dq) if dq else 0
                if not dq or n < LSTM_TIME_STEPS:
                    logger.info(f"[{station_id}] Thu thap du lieu... {n}/{LSTM_TIME_STEPS}")
                    return None
                data_list = list(dq)

            X_iot = np.array([[
                d['level'], d['flow'], d['flow_efficiency'],
                d['delta_level'], d['rain_local']
            ] for d in data_list])

            if self.scaler is not None:
                X_iot = self.scaler.transform(X_iot)
            else:
                logger.warning("[WARNING] Scaler not loaded")

            time_decay = self._calculate_time_decay()
            X_weather = np.array([[
                self.weather_data['rain_forecast'],
                time_decay,
                TOPO_INDEX
            ]])

            X_iot = X_iot.reshape(1, LSTM_TIME_STEPS, LSTM_FEATURES)

            if self.model is not None:
                probabilities = self.model.predict([X_iot, X_weather], verbose=0)[0]
            else:
                probabilities = self.mock_predict()

            predicted_class = int(np.argmax(probabilities))
            confidence = float(probabilities[predicted_class])

            class_names = {
                CLASS_SAFE: "AN TOAN (Safe)",
                CLASS_WARNING: "CANH BAO (Warning)",
                CLASS_FLOOD: "NGAP LUT (Flood)",
            }

            latest = data_list[-1]
            result = {
                'station_id': station_id,
                'class': predicted_class,
                'confidence': confidence,
                'description': class_names.get(predicted_class, "Unknown"),
                'probabilities': probabilities.tolist(),
                'timestamp': datetime.now(),
                'data_points': len(data_list),
                'level': float(latest['level']),
                'flow': float(latest['flow']),
                'rain': float(latest['rain_local']),
                'weather': {
                    'rain_forecast': self.weather_data['rain_forecast'],
                    'time_decay': time_decay,
                    'topo_index': TOPO_INDEX,
                    'api_success': self.weather_data['api_success']
                }
            }

            self.publish_prediction_mqtt(result, station_id)
            return result

        except Exception as e:
            logger.error(f"[{station_id}] Prediction error: {e}")
            return None

    def send_buzzer_command(self, command):
        if command not in ['ON', 'OFF']:
            return False
        try:
            payload = json.dumps({'command': command, 'timestamp': datetime.now().isoformat()})
            result = self.client.publish(TOPIC_BUZZER_CMD, payload, qos=1)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.info(f"[OK] Buzzer: {command}")
                self.last_command = command
                return True
        except Exception as e:
            logger.error(f"Buzzer publish error: {e}")
        return False

    def run_prediction_loop(self):
        logger.info("Starting multi-station prediction loop...")
        while True:
            try:
                self.fetch_weather()

                with self.lock:
                    station_ids = list(self.station_deques.keys())

                round_results = []
                for sid in station_ids:
                    if len(self.station_deques[sid]) < LSTM_TIME_STEPS:
                        continue
                    result = self.predict_flood_risk(sid)
                    if result:
                        self.prediction_count[sid] += 1
                        self.last_prediction_global = result
                        round_results.append((sid, result))
                        ts = result['timestamp'].strftime('%H:%M:%S')
                        logger.info(
                            f"\n{'='*56}\n[{ts}] {sid} PRED #{self.prediction_count[sid]}\n"
                            f"  {result['description']} conf={result['confidence']*100:.1f}%\n"
                            f"{'='*56}"
                        )

                if round_results:
                    flood_hit = any(
                        r['class'] == CLASS_FLOOD and r['confidence'] >= FLOOD_ALERT_THRESHOLD
                        for _, r in round_results
                    )
                    all_safe = all(r['class'] == CLASS_SAFE for _, r in round_results)
                    if flood_hit:
                        logger.critical("[ALERT] FLOOD on at least one station → buzzer ON")
                        self.send_buzzer_command('ON')
                    elif all_safe:
                        logger.info("[OK] All reported stations SAFE → buzzer OFF")
                        self.send_buzzer_command('OFF')
                    else:
                        for sid, r in round_results:
                            if r['class'] == CLASS_WARNING:
                                logger.warning(f"[WARN] {sid} warning conf={r['confidence']*100:.1f}%")

                time.sleep(5)

            except KeyboardInterrupt:
                logger.info("Prediction loop interrupted")
                break
            except Exception as e:
                logger.error(f"Loop error: {e}")
                time.sleep(5)

    def connect(self):
        logger.info(f"Connecting {MQTT_BROKER}:{MQTT_PORT}...")
        self.client.connect(MQTT_BROKER, MQTT_PORT, MQTT_KEEPALIVE)
        self.client.loop_start()
        logger.info("MQTT client started")

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("Disconnected")

    def run(self):
        try:
            self.connect()
            self.run_prediction_loop()
        except KeyboardInterrupt:
            logger.info("Shutdown...")
            self.disconnect()
        except Exception as e:
            logger.error(f"Fatal: {e}")
            self.disconnect()


def main():
    try:
        logger.info("=" * 80)
        logger.info("SMART WATER — MULTI-STATION AI PREDICTOR")
        logger.info("=" * 80)
        logger.info(f"Device: {DEVICE_ID} | Weather ref LAT={LAT}, LON={LON}")
        logger.info(f"Ingest: {TOPIC_FLOOD_MONITOR_WILDCARD} (+ legacy {TOPIC_DATA})")
        logger.info(f"Publish: {TOPIC_AI_PREDICTION_PREFIX}/<station_id>")
        logger.info("=" * 80)
        AIPredictor().run()
    except Exception as e:
        logger.error(f"Fatal in main: {e}")


if __name__ == "__main__":
    main()
