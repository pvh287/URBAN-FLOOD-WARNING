"""
=========================================================================
 FloodMind-AIoT DSS — AI Predictor (Main Engine)
=========================================================================
 Multi-station real-time flood prediction with:
   Tầng 1: Data Quality & Sensor Fusion   (sensor_normalize.py)
   Tầng 2: 60-sample Sliding Window
   Tầng 3: Hydrological Feature Engine     (hydro_features.py)
   Tầng 4: Forecast Model (LSTM / rule)
   Tầng 5-8: Risk + Rule Guard + XAI      (risk_engine.py)
=========================================================================
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
from threading import Lock, Thread

from config import (
    MQTT_BROKER, MQTT_PORT, MQTT_KEEPALIVE,
    TOPIC_DATA, TOPIC_FLOOD_MONITOR_WILDCARD, TOPIC_BUZZER_CMD,
    TOPIC_AI_PREDICTION_PREFIX, TOPIC_SENSOR_COMMAND,
    OPEN_METEO_API_URL, API_TIMEOUT, API_CALL_INTERVAL,
    LAT, LON, TOPO_INDEX,
    LSTM_TIME_STEPS, LSTM_FEATURES,
    MODEL_FILE_V2, MODEL_FILE_LEGACY, SCALER_LEGACY_FILE,
    SCALER_SENSOR_FILE, MODEL_METADATA_FILE,
    CLASS_SAFE, CLASS_WATCH, CLASS_WARNING, CLASS_FLOOD, CLASS_NAMES,
    FLOOD_ALERT_THRESHOLD, WARNING_ALERT_THRESHOLD,
    DEVICE_ID, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    VERSION, H_DANGER,
)
from sensor_normalize import normalize_sensor_payload, validate_sensor_sample
from hydro_features import extract_hydro_features
from risk_engine import (
    calculate_risk_score, rule_guard, generate_explanation,
    generate_recommended_action, map_legacy_class_to_v2, risk_score_to_class,
)

logger = logging.getLogger(__name__)

EXPECTED_STATIONS = ["THAI_HA", "PHAM_NGOC_THACH", "TRUONG_CHINH"]


def _station_topic(station_id: str) -> str:
    return f"{TOPIC_AI_PREDICTION_PREFIX}/{station_id}"


def _parse_station_from_flood_topic(topic: str):
    parts = topic.split('/')
    if len(parts) == 4 and parts[0] == 'flood' and parts[1] == 'monitor' and parts[3] == 'data':
        return parts[2]
    return None


class AIPredictor:
    """FloodMind-AIoT DSS — Multi-station real-time flood prediction."""

    def __init__(self):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        self.lock = Lock()
        self.station_deques = {}
        self.station_quality = {}          # per-station data quality
        self.prediction_count = defaultdict(int)
        self.last_station_predictions = {}

        self.weather_data = {
            'rain_forecast': 0, 'time_decay': 0,
            'topo_index': TOPO_INDEX,
            'last_api_call': None, 'api_success': False,
        }

        self.model = None
        self.model_version = 'none'
        self.scaler = None
        self.is_v2_model = False
        self._load_model()
        self._load_scaler()

        self.last_prediction_global = None
        self.last_command = None

        logger.info("FloodMind-AIoT DSS Engine initialized (multi-station)")

    # ── Model loading ───────────────────────────────────────────
    def _load_model(self):
        """Tầng 4: Load model v2 → legacy → rule fallback."""
        try:
            from tensorflow import keras
        except ImportError:
            logger.error("TensorFlow not installed — using rule-based fallback only")
            self.model = None
            self.model_version = 'rule-only'
            return

        # Try v2
        if os.path.isfile(MODEL_FILE_V2):
            try:
                self.model = keras.models.load_model(MODEL_FILE_V2)
                self.model_version = 'v2.0'
                self.is_v2_model = True
                logger.info(f"[OK] Model v2 loaded: {MODEL_FILE_V2}")
                return
            except Exception as e:
                logger.warning(f"Model v2 load failed: {e}")

        # Try legacy
        if os.path.isfile(MODEL_FILE_LEGACY):
            try:
                self.model = keras.models.load_model(MODEL_FILE_LEGACY)
                self.model_version = 'v1.0-legacy'
                self.is_v2_model = False
                logger.info(f"[OK] Legacy model loaded: {MODEL_FILE_LEGACY}")
                return
            except Exception as e:
                logger.warning(f"Legacy model load failed: {e}")

        logger.warning("No model found — using deterministic rule-based fallback")
        self.model = None
        self.model_version = 'rule-only'

    def _load_scaler(self):
        for path in [SCALER_SENSOR_FILE, SCALER_LEGACY_FILE]:
            if os.path.isfile(path):
                try:
                    self.scaler = joblib.load(path)
                    logger.info(f"[OK] Scaler loaded: {path}")
                    return
                except Exception as e:
                    logger.warning(f"Scaler load failed ({path}): {e}")
        logger.warning("No scaler found — prediction without scaling")
        self.scaler = None

    # ── MQTT callbacks ──────────────────────────────────────────
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"[OK] MQTT connected ({MQTT_BROKER}:{MQTT_PORT})")
            client.subscribe([
                (TOPIC_FLOOD_MONITOR_WILDCARD, 0),
                (TOPIC_DATA, 0),
                (TOPIC_SENSOR_COMMAND, 0),
            ])
        else:
            logger.error(f"MQTT connect failed RC={rc}")

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
        except Exception as e:
            logger.error(f"MQTT msg error: {e}")

    def _handle_sensor_command(self, payload):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return
        action = data.get('action')
        if action == 'toggle_buzzer':
            val = str(data.get('value', '')).strip().upper()
            if val in ('ON', 'OFF'):
                self.send_buzzer_command(val)
        elif action == 'reset_ai':
            self.reset_ai_memory(station_id=data.get('station_id'))

    # ── Station buffer management ───────────────────────────────
    def _get_deque(self, station_id: str) -> deque:
        if station_id not in self.station_deques:
            self.station_deques[station_id] = deque(maxlen=LSTM_TIME_STEPS)
        return self.station_deques[station_id]

    def reset_ai_memory(self, station_id=None):
        with self.lock:
            if station_id:
                if station_id in self.station_deques:
                    self.station_deques[station_id].clear()
                self.prediction_count[station_id] = 0
                self.last_station_predictions.pop(station_id, None)
            else:
                self.station_deques.clear()
                self.prediction_count.clear()
                self.last_station_predictions.clear()
        logger.info(f"[RESET] {'station ' + station_id if station_id else 'ALL'}")

    # ── Tầng 1+2: Ingest & normalize ───────────────────────────
    def _ingest_sensor_sample(self, station_id: str, payload: str):
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as e:
            logger.error(f"[{station_id}] JSON error: {e}")
            return

        if station_id == 'LEGACY':
            station_id = str(raw.get('station_id', 'LEGACY'))

        # Tầng 1: Normalize + validate
        normalized = normalize_sensor_payload(raw, fallback_station_id=station_id)
        quality = validate_sensor_sample(normalized)
        normalized['data_quality'] = quality

        level = normalized['level_cm']
        flow = normalized['flow_lpm']
        rain = normalized['rain_mm']

        dq = self._get_deque(station_id)
        with self.lock:
            prev_level = dq[-1]['level'] if len(dq) > 0 else level
            delta_level = level - prev_level
            flow_efficiency = flow / (level + 1.0)
            dq.append({
                'timestamp': normalized['timestamp'],
                'level': level,
                'flow': flow,
                'flow_efficiency': flow_efficiency,
                'delta_level': delta_level,
                'rain_local': rain,
                'data_quality': quality,
            })
            self.station_quality[station_id] = quality

        logger.debug(f"[{station_id}] l={level:.1f} f={flow:.3f} r={rain:.2f} "
                     f"q={len(dq)}/{LSTM_TIME_STEPS} dq={quality}")

    # ── Tầng 4: Prediction ──────────────────────────────────────
    def _rule_based_predict(self, hydro):
        """Deterministic rule-based fallback (NO random)."""
        h = hydro.get('h_now', 0)
        slope = hydro.get('slope_h', 0)
        r_sum = hydro.get('r_sum', 0)

        if h >= 60 or (h >= 40 and slope >= 6):
            return {'safe': 0.05, 'watch': 0.05, 'warning': 0.15, 'flood': 0.75}
        if h >= 40 or (h >= 20 and slope >= 3) or r_sum >= 10:
            return {'safe': 0.1, 'watch': 0.15, 'warning': 0.6, 'flood': 0.15}
        if h >= 20 or slope >= 2 or r_sum >= 5:
            return {'safe': 0.2, 'watch': 0.5, 'warning': 0.25, 'flood': 0.05}
        return {'safe': 0.8, 'watch': 0.15, 'warning': 0.04, 'flood': 0.01}

    def _predict_level_5min(self, hydro):
        """Simple linear extrapolation for 5-min forecast."""
        h_now = hydro.get('h_now', 0)
        slope_h = hydro.get('slope_h', 0)
        return round(max(0, h_now + slope_h * 5), 1)

    def is_sample_fresh(self, sample, max_age_seconds=30):
        try:
            ts = sample.get('timestamp')
            if not ts: return False
            if isinstance(ts, str):
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            else:
                dt = ts
            if getattr(dt, 'tzinfo', None) is not None:
                dt = dt.replace(tzinfo=None)
            return (datetime.now() - dt).total_seconds() <= max_age_seconds
        except Exception:
            return False

    def predict_flood_risk(self, station_id: str):
        try:
            with self.lock:
                dq = self.station_deques.get(station_id)
                n = len(dq) if dq else 0
                if not dq or n < LSTM_TIME_STEPS:
                    logger.info(f"[{station_id}] Collecting... {n}/{LSTM_TIME_STEPS}")
                    return None
                data_list = list(dq)

            latest_sample = data_list[-1]
            is_stale = not self.is_sample_fresh(latest_sample, max_age_seconds=30)

            # Tầng 1: Aggregate quality
            qualities = [d.get('data_quality', 'GOOD') for d in data_list]
            if is_stale:
                agg_quality = 'STALE'
            elif 'BAD' in qualities:
                agg_quality = 'BAD'
            elif 'DEGRADED' in qualities:
                agg_quality = 'DEGRADED'
            else:
                agg_quality = 'GOOD'

            # Tầng 3: Hydro features
            hydro = extract_hydro_features(data_list)

            # Tầng 4: Model prediction
            predicted_level_5min = self._predict_level_5min(hydro)
            decision_source = 'Rule Guard'
            probs_dict = None

            if not is_stale and self.model is not None:
                try:
                    X_iot = np.array([[
                        d['level'], d['flow'], d['flow_efficiency'],
                        d['delta_level'], d['rain_local']
                    ] for d in data_list])

                    if self.scaler is not None:
                        X_iot = self.scaler.transform(X_iot)

                    time_decay = self._calculate_time_decay()
                    X_weather = np.array([[
                        self.weather_data['rain_forecast'],
                        time_decay, TOPO_INDEX,
                    ]])
                    X_iot_3d = X_iot.reshape(1, LSTM_TIME_STEPS, LSTM_FEATURES)

                    if self.is_v2_model:
                        preds = self.model.predict([X_iot_3d, X_weather], verbose=0)
                        if isinstance(preds, list) and len(preds) >= 2:
                            raw_probs = preds[0][0]
                            predicted_level_5min = float(preds[1][0][0])
                        else:
                            raw_probs = preds[0] if isinstance(preds, list) else preds[0]
                        probs_dict = {
                            'safe': float(raw_probs[0]),
                            'watch': float(raw_probs[1]) if len(raw_probs) > 3 else 0.0,
                            'warning': float(raw_probs[2]) if len(raw_probs) > 3 else float(raw_probs[1]),
                            'flood': float(raw_probs[3]) if len(raw_probs) > 3 else float(raw_probs[2]),
                        }
                    else:
                        raw_probs = self.model.predict([X_iot_3d, X_weather], verbose=0)[0]
                        probs_dict = {
                            'safe': float(raw_probs[0]),
                            'watch': 0.0,
                            'warning': float(raw_probs[1]),
                            'flood': float(raw_probs[2]),
                        }
                    decision_source = 'AI + Rule Guard'
                except Exception as e:
                    logger.error(f"[{station_id}] Model predict error: {e} — fallback to rules")
                    probs_dict = None

            if probs_dict is None:
                probs_dict = self._rule_based_predict(hydro)
                decision_source = 'Rule Guard (no model)'

            # Tầng 5: Classify
            max_key = max(probs_dict, key=probs_dict.get)
            key_to_class = {'safe': CLASS_SAFE, 'watch': CLASS_WATCH,
                            'warning': CLASS_WARNING, 'flood': CLASS_FLOOD}
            predicted_class = key_to_class.get(max_key, CLASS_SAFE)
            confidence = probs_dict[max_key]

            # Tầng 6: Risk score
            risk_score = calculate_risk_score(probs_dict, hydro, agg_quality)

            # Tầng 7: Rule guard
            final_class, risk_score, rule_class = rule_guard(
                hydro, predicted_class, risk_score, agg_quality, predicted_level_5min)
            confidence = max(confidence, probs_dict.get(
                {CLASS_SAFE: 'safe', CLASS_WATCH: 'watch',
                 CLASS_WARNING: 'warning', CLASS_FLOOD: 'flood'}.get(final_class, 'safe'), 0))

            ai_class_name = CLASS_NAMES.get(predicted_class, 'SAFE')
            rule_class_name = CLASS_NAMES.get(rule_class, 'SAFE')
            final_class_name = CLASS_NAMES.get(final_class, 'SAFE')
            logger.info(f"[AI] {station_id} level={hydro['h_now']:.1f}cm flow={hydro['q_now']:.1f}lpm rain={hydro['r_now']:.1f}mm ai={ai_class_name} rule={rule_class_name} final={final_class_name} risk={risk_score} data_quality={agg_quality}")

            # Tầng 8: Explain
            explanation = generate_explanation(
                hydro, final_class, risk_score, agg_quality, predicted_class,
                predicted_level_5min)
            action = generate_recommended_action(final_class, agg_quality)

            result = {
                'station_id': station_id,
                'class': final_class,
                'confidence': confidence,
                'description': CLASS_NAMES.get(final_class, 'AN_TOAN'),
                'probabilities': probs_dict,
                'risk_score': risk_score,
                'predicted_level_5min': predicted_level_5min,
                'timestamp': datetime.now(),
                'data_points': len(data_list),
                'data_quality': agg_quality,
                'level': hydro['h_now'],
                'flow': hydro['q_now'],
                'rain': hydro['r_now'],
                'hydro': hydro,
                'explanation': explanation,
                'recommended_action': action,
                'decision_source': decision_source,
                'weather': {
                    'rain_forecast': self.weather_data['rain_forecast'],
                    'time_decay': self._calculate_time_decay(),
                    'topo_index': TOPO_INDEX,
                    'api_success': self.weather_data['api_success'],
                },
            }

            self.publish_prediction_mqtt(result, station_id)
            self.last_station_predictions[station_id] = result
            overall = self.build_overall_prediction()
            self.publish_overall_prediction_mqtt(overall)
            return result

        except Exception as e:
            logger.error(f"[{station_id}] Prediction error: {e}")
            import traceback; traceback.print_exc()
            return None

    # ── MQTT publish ────────────────────────────────────────────
    def build_prediction_mqtt_json(self, result, station_id: str):
        """Build schema v2.0 MQTT payload (backward compatible)."""
        probs = result['probabilities']
        ts = result['timestamp']
        ts_iso = ts.isoformat() if hasattr(ts, 'isoformat') else datetime.now().isoformat()

        status_map = {
            CLASS_SAFE: 'AN_TOAN', CLASS_WATCH: 'THEO_DOI',
            CLASS_WARNING: 'CANH_BAO', CLASS_FLOOD: 'NGAP_LUT',
        }
        # Legacy status names for backward compat
        status_legacy = {
            CLASS_SAFE: 'AN TOAN', CLASS_WATCH: 'THEO DOI',
            CLASS_WARNING: 'CANH BAO', CLASS_FLOOD: 'NGAP LUT',
        }
        fc = int(result['class'])
        conf_pct = round(float(result['confidence']) * 100, 1)
        hydro = result.get('hydro', {})

        payload = {
            # v2 schema
            'schema_version': '2.0',
            'model_name': 'FloodMind-AIoT-DSS',
            'model_version': self.model_version,
            'station_id': station_id,
            'timestamp': ts_iso,
            'data_points': result.get('data_points', 0),
            'data_quality': result.get('data_quality', 'GOOD'),

            'current': {
                'level_cm': round(float(result.get('level', 0)), 1),
                'flow_lpm': round(float(result.get('flow', 0)), 3),
                'rain_mm': round(float(result.get('rain', 0)), 2),
            },

            'forecast': {
                'predicted_level_5min': result.get('predicted_level_5min'),
                'predicted_level_10min': None,
                'time_to_warning_min': None,
            },

            'ai': {
                'class': fc,
                'status': status_map.get(fc, 'AN_TOAN'),
                'confidence': conf_pct,
                'risk_score': result.get('risk_score', 0),
                'probs': {
                    'safe': round(float(probs.get('safe', 0)) * 100, 1),
                    'watch': round(float(probs.get('watch', 0)) * 100, 1),
                    'warning': round(float(probs.get('warning', 0)) * 100, 1),
                    'flood': round(float(probs.get('flood', 0)) * 100, 1),
                },
            },

            'hydro_features': {
                'delta_h_5min': round(hydro.get('delta_h_5min', 0), 2),
                'slope_h': round(hydro.get('slope_h', 0), 3),
                'r_sum': round(hydro.get('r_sum', 0), 2),
                'r_max': round(hydro.get('r_max', 0), 2),
                'drainage_eff': round(hydro.get('drainage_eff', 0), 4),
                'drainage_stress': round(hydro.get('drainage_stress', 0), 4),
            },

            'explanation': result.get('explanation', []),
            'recommended_action': result.get('recommended_action', ''),
            'decision_source': result.get('decision_source', ''),

            # ── Legacy top-level fields (backward compat) ──
            'status': status_legacy.get(fc, 'AN TOAN'),
            'confidence': conf_pct,
            'level': round(float(result.get('level', 0)), 1),
            'flow': round(float(result.get('flow', 0)), 3),
            'rain': round(float(result.get('rain', 0)), 2),
            'probs': {
                'safe': round(float(probs.get('safe', 0)) * 100, 1),
                'warning': round(float(probs.get('warning', 0)) * 100, 1),
                'flood': round(float(probs.get('flood', 0)) * 100, 1),
            },
        }
        return payload

    def publish_prediction_mqtt(self, result, station_id: str):
        try:
            payload = self.build_prediction_mqtt_json(result, station_id)
            body = json.dumps(payload, ensure_ascii=False)
            topic = _station_topic(station_id)
            self.client.publish(topic, body, qos=1, retain=False)
            logger.info(f"[MQTT] Published → {topic}")
        except Exception as e:
            logger.error(f"[MQTT] Publish failed: {e}")

    def is_station_prediction_fresh(self, result, max_age_seconds=30):
        if not result:
            return False
        ts = result.get('timestamp')
        if ts is None:
            return False
        try:
            if isinstance(ts, str):
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            else:
                dt = ts
            if getattr(dt, 'tzinfo', None) is not None:
                dt = dt.replace(tzinfo=None)
            return (datetime.now() - dt).total_seconds() <= max_age_seconds
        except Exception:
            return False

    def _get_station_risk_score(self, result):
        if result is None:
            return 0.0
        raw_risk = result.get('risk_score')
        if raw_risk is not None:
            return float(raw_risk)
        station_class = int(result.get('class', CLASS_SAFE))
        fallback = {CLASS_SAFE: 10.0, CLASS_WATCH: 35.0, CLASS_WARNING: 65.0, CLASS_FLOOD: 90.0}
        return float(fallback.get(station_class, 10.0))

    def _get_station_class(self, result):
        if result is None:
            return CLASS_SAFE
        station_class = result.get('class')
        if station_class is None:
            risk = self._get_station_risk_score(result)
            return risk_score_to_class(risk)
        return int(station_class)

    def build_overall_prediction(self):
        now = datetime.now()
        station_predictions = {}
        stations_ready = 0
        missing_stations = []
        stale_stations = []

        for station_id in EXPECTED_STATIONS:
            result = self.last_station_predictions.get(station_id)
            if result is None:
                missing_stations.append(station_id)
                continue
            if self.is_station_prediction_fresh(result):
                station_predictions[station_id] = result
                stations_ready += 1
            else:
                stale_stations.append(station_id)

        if stations_ready < len(EXPECTED_STATIONS):
            if missing_stations:
                overall_status = 'CHUA_DU_DU_LIEU'
            else:
                overall_status = 'DU_LIEU_KHONG_DAY_DU'
            return {
                'schema_version': '2.0',
                'type': 'overall_prediction',
                'area_name': 'Khu vực Đống Đa',
                'timestamp': now.isoformat(),
                'stations_total': len(EXPECTED_STATIONS),
                'stations_ready': stations_ready,
                'overall_status': overall_status,
                'overall_class': None,
                'overall_risk_score': None,
                'overall_confidence': 0.0,
                'highest_risk_station': None,
                'summary': {'safe': 0, 'watch': 0, 'warning': 0, 'flood': 0},
                'forecast': {'max_predicted_level_5min': None, 'stations_trending_up': 0},
                'explanation': ['Chưa đủ dữ liệu từ 3 điểm đo để suy luận tổng thể khu vực.'],
                'recommended_action': 'Chưa đủ dữ liệu từ 3 điểm đo, tiếp tục thu thập và không kết luận an toàn.'
            }

        risk_scores = []
        station_classes = []
        forecast_levels = []
        station_statuses = []
        summary = {'safe': 0, 'watch': 0, 'warning': 0, 'flood': 0}

        for station_id in EXPECTED_STATIONS:
            result = station_predictions[station_id]
            risk = self._get_station_risk_score(result)
            station_class = self._get_station_class(result)
            risk_scores.append(risk)
            station_classes.append(station_class)
            station_statuses.append(CLASS_NAMES.get(station_class, 'AN_TOAN'))
            summary['safe' if station_class == CLASS_SAFE else
                    'watch' if station_class == CLASS_WATCH else
                    'warning' if station_class == CLASS_WARNING else
                    'flood'] += 1

            predicted_level = result.get('predicted_level_5min')
            if predicted_level is not None:
                forecast_levels.append(float(predicted_level))

        max_risk = max(risk_scores)
        avg_risk = sum(risk_scores) / len(risk_scores)
        risky_ratio = (sum(1 for cls in station_classes if cls >= CLASS_WATCH) / len(station_classes)) * 100
        forecast_risk = max((level / H_DANGER) * 100 for level in forecast_levels) if forecast_levels else max_risk
        overall_risk_score = round(max(0.0, min(100.0, 0.45 * max_risk + 0.30 * avg_risk + 0.15 * risky_ratio + 0.10 * forecast_risk)), 1)

        base_class = risk_score_to_class(overall_risk_score)
        flood_count = sum(1 for cls in station_classes if cls == CLASS_FLOOD)
        warning_count = sum(1 for cls in station_classes if cls >= CLASS_WARNING)
        if flood_count >= 2 or max_risk >= 85:
            overall_class = CLASS_FLOOD
        elif flood_count >= 1 or warning_count >= 2:
            overall_class = max(base_class, CLASS_WARNING)
        else:
            overall_class = base_class

        if overall_class == CLASS_SAFE and overall_risk_score >= 30:
            overall_class = CLASS_WATCH

        highest_risk_station = None
        for station_id in EXPECTED_STATIONS:
            result = station_predictions[station_id]
            station_risk = self._get_station_risk_score(result)
            station_status = CLASS_NAMES.get(self._get_station_class(result), 'AN_TOAN')
            station_name = {'THAI_HA': 'Thái Hà', 'PHAM_NGOC_THACH': 'Phạm Ngọc Thạch', 'TRUONG_CHINH': 'Trường Chinh'}.get(station_id, station_id)
            candidate = {
                'station_id': station_id,
                'name': station_name,
                'risk_score': round(station_risk, 1),
                'status': station_status,
            }
            if highest_risk_station is None or station_risk > highest_risk_station['risk_score']:
                highest_risk_station = candidate

        overall_status = {
            CLASS_SAFE: 'AN_TOAN_KHU_VUC',
            CLASS_WATCH: 'THEO_DOI_KHU_VUC',
            CLASS_WARNING: 'CANH_BAO_KHU_VUC',
            CLASS_FLOOD: 'NGAP_LUT_KHU_VUC',
        }.get(overall_class, 'THEO_DOI_KHU_VUC')

        max_predicted_level = round(max(forecast_levels), 1) if forecast_levels else None
        stations_trending_up = 0
        for station_id in EXPECTED_STATIONS:
            result = station_predictions[station_id]
            hydro = result.get('hydro', {})
            slope_h = hydro.get('slope_h', 0)
            predicted_level = result.get('predicted_level_5min')
            level = result.get('level', 0)
            if slope_h > 0 or (predicted_level is not None and predicted_level > level):
                stations_trending_up += 1

        explanation = self.generate_overall_explanation(station_predictions, overall_risk_score)
        recommended_action = self.generate_overall_action(overall_class, highest_risk_station)

        return {
            'schema_version': '2.0',
            'type': 'overall_prediction',
            'area_name': 'Khu vực Đống Đa',
            'timestamp': now.isoformat(),
            'stations_total': len(EXPECTED_STATIONS),
            'stations_ready': stations_ready,
            'overall_status': overall_status,
            'overall_class': overall_class,
            'overall_risk_score': overall_risk_score,
            'overall_confidence': round(sum(float(result.get('confidence', 0)) for result in station_predictions.values()) / len(station_predictions) * 100, 1),
            'highest_risk_station': highest_risk_station,
            'summary': summary,
            'forecast': {
                'max_predicted_level_5min': max_predicted_level,
                'stations_trending_up': stations_trending_up,
            },
            'explanation': explanation,
            'recommended_action': recommended_action,
        }

    def generate_overall_explanation(self, station_predictions, overall_risk_score):
        if not station_predictions:
            return ['Chưa đủ dữ liệu từ 3 điểm đo để suy luận tổng thể khu vực.']

        highest_risk_station = None
        for station_id in EXPECTED_STATIONS:
            result = station_predictions.get(station_id)
            if result is None:
                continue
            station_risk = self._get_station_risk_score(result)
            if highest_risk_station is None or station_risk > highest_risk_station['risk_score']:
                highest_risk_station = {'station_id': station_id, 'risk_score': station_risk}

        warning_count = sum(1 for sid in EXPECTED_STATIONS if self._get_station_class(station_predictions.get(sid)) >= CLASS_WARNING)
        flood_count = sum(1 for sid in EXPECTED_STATIONS if self._get_station_class(station_predictions.get(sid)) == CLASS_FLOOD)
        watch_count = sum(1 for sid in EXPECTED_STATIONS if self._get_station_class(station_predictions.get(sid)) == CLASS_WATCH)

        max_predicted_level = None
        stations_trending_up = 0
        for station_id in EXPECTED_STATIONS:
            result = station_predictions.get(station_id)
            if result is None:
                continue
            predicted_level = result.get('predicted_level_5min')
            if predicted_level is not None:
                max_predicted_level = max(max_predicted_level or 0.0, float(predicted_level))
            hydro = result.get('hydro', {})
            slope_h = hydro.get('slope_h', 0)
            level = result.get('level', 0)
            if slope_h > 0 or (predicted_level is not None and predicted_level > level):
                stations_trending_up += 1

        explanations = []
        if highest_risk_station:
            station_name = {'THAI_HA': 'Thái Hà', 'PHAM_NGOC_THACH': 'Phạm Ngọc Thạch', 'TRUONG_CHINH': 'Trường Chinh'}.get(highest_risk_station['station_id'], highest_risk_station['station_id'])
            explanations.append(f"Trạm {station_name} có nguy cơ cao nhất")
        explanations.append(f"{warning_count + flood_count}/{len(EXPECTED_STATIONS)} trạm đang ở mức cảnh báo/ngập")
        explanations.append(f"{stations_trending_up}/{len(EXPECTED_STATIONS)} trạm có xu hướng mực nước tăng")
        if max_predicted_level is not None:
            explanations.append(f"Mực nước dự báo cao nhất sau 5 phút: {max_predicted_level:.1f} cm")
        explanations.append('Dữ liệu đủ 3 trạm để đánh giá tổng thể khu vực')

        return explanations

    def generate_overall_action(self, overall_class, highest_risk_station):
        if overall_class is None:
            return 'Chưa đủ dữ liệu từ 3 điểm đo, tiếp tục thu thập và không kết luận an toàn.'

        highest_station_name = highest_risk_station.get('name', 'trạm nguy hiểm nhất') if highest_risk_station else 'trạm nguy hiểm nhất'
        if overall_class == CLASS_SAFE:
            return 'Toàn khu vực ổn định, tiếp tục giám sát.'
        if overall_class == CLASS_WATCH:
            return 'Theo dõi sát toàn khu vực trong 5 phút tới.'
        if overall_class == CLASS_WARNING:
            return f'Ưu tiên kiểm tra {highest_station_name}, chuẩn bị bật còi và gửi cảnh báo nếu xu hướng tăng tiếp tục.'
        return 'Bật cảnh báo khẩn cấp, gửi Telegram và triển khai phương án xử lý tại các điểm nguy cơ cao.'

    def publish_overall_prediction_mqtt(self, overall_payload):
        try:
            if overall_payload is None:
                return
            body = json.dumps(overall_payload, ensure_ascii=False)
            topic = 'ai/prediction/overall'
            self.client.publish(topic, body, qos=1, retain=False)
            logger.info(
                f"[MQTT] Published → {topic} | stations_ready={overall_payload.get('stations_ready')} "
                f"overall_risk_score={overall_payload.get('overall_risk_score')} "
                f"overall_status={overall_payload.get('overall_status')} "
                f"highest_risk_station={overall_payload.get('highest_risk_station')}"
            )
        except Exception as e:
            logger.error(f"[MQTT] Publish failed (overall): {e}")

    # ── Weather ─────────────────────────────────────────────────
    def fetch_weather(self):
        try:
            now = datetime.now()
            if (self.weather_data['last_api_call'] is not None and
                    (now - self.weather_data['last_api_call']).total_seconds() < API_CALL_INTERVAL):
                return
            params = {
                'latitude': LAT, 'longitude': LON,
                'hourly': 'precipitation',
                'forecast_hours': 2, 'timezone': 'auto',
            }
            resp = requests.get(OPEN_METEO_API_URL, params=params, timeout=API_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if 'hourly' in data and 'precipitation' in data['hourly']:
                p = data['hourly']['precipitation']
                self.weather_data['rain_forecast'] = p[1] if len(p) > 1 else p[0]
            self.weather_data['last_api_call'] = now
            self.weather_data['api_success'] = True
        except Exception as e:
            logger.error(f"[API] Weather error: {e}")
            self.weather_data['api_success'] = False

    def _calculate_time_decay(self):
        if self.weather_data['last_api_call'] is None:
            return 1
        elapsed = (datetime.now() - self.weather_data['last_api_call']).total_seconds() / 60
        return min(int(elapsed) + 1, 14)

    # ── Buzzer ──────────────────────────────────────────────────
    def send_buzzer_command(self, command):
        """Send plain-text ON/OFF to ESP32 buzzer (fixed format mismatch)."""
        if command not in ('ON', 'OFF'):
            return False
        try:
            # Gửi chuỗi thuần ON/OFF — ESP32 firmware hiểu được
            result = self.client.publish(TOPIC_BUZZER_CMD, command, qos=1)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.info(f"[OK] Buzzer: {command}")
                self.last_command = command
                return True
        except Exception as e:
            logger.error(f"Buzzer publish error: {e}")
        return False

    # ── Telegram ────────────────────────────────────────────────
    def send_telegram_alert(self, station_id: str, risk_score: float,
                            explanation: list = None):
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            logger.warning("[TELEGRAM] Token/ChatID not configured — skipping")
            return

        def _send():
            reasons_text = "\n".join(f"  • {r}" for r in (explanation or []))
            message = (
                f"🚨 CẢNH BÁO NGẬP LỤT — Trạm {station_id}\n"
                f"Risk Score: {risk_score}/100\n"
                f"{reasons_text}\n"
                f"⏰ {datetime.now().strftime('%H:%M:%S %d/%m/%Y')}"
            )
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            try:
                resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
                if resp.status_code == 200:
                    logger.info(f"[TELEGRAM] Sent alert for {station_id}")
                else:
                    logger.warning(f"[TELEGRAM] Error: {resp.text}")
            except Exception as e:
                logger.error(f"[TELEGRAM] {e}")

        Thread(target=_send, daemon=True).start()

    # ── Main loop ───────────────────────────────────────────────
    def run_prediction_loop(self):
        logger.info("Starting FloodMind-AIoT DSS prediction loop...")
        while True:
            try:
                self.fetch_weather()
                with self.lock:
                    station_ids = list(self.station_deques.keys())

                round_results = []
                for sid in station_ids:
                    if len(self.station_deques.get(sid, [])) < LSTM_TIME_STEPS:
                        continue
                    result = self.predict_flood_risk(sid)
                    if result:
                        self.prediction_count[sid] += 1
                        self.last_prediction_global = result
                        round_results.append((sid, result))

                if round_results:
                    flood_stations = [
                        (sid, r) for sid, r in round_results
                        if r['class'] == CLASS_FLOOD and r['risk_score'] >= FLOOD_ALERT_THRESHOLD * 100
                    ]
                    all_safe = all(r['class'] == CLASS_SAFE for _, r in round_results)

                    if flood_stations:
                        logger.critical("[ALERT] FLOOD detected → buzzer ON")
                        self.send_buzzer_command('ON')
                        for sid, r in flood_stations:
                            self.send_telegram_alert(sid, r['risk_score'], r.get('explanation'))
                    elif all_safe:
                        self.send_buzzer_command('OFF')

                time.sleep(5)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Loop error: {e}")
                time.sleep(5)

    def connect(self):
        self.client.connect(MQTT_BROKER, MQTT_PORT, MQTT_KEEPALIVE)
        self.client.loop_start()

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def run(self):
        try:
            self.connect()
            self.run_prediction_loop()
        except KeyboardInterrupt:
            self.disconnect()
        except Exception as e:
            logger.error(f"Fatal: {e}")
            self.disconnect()


def main():
    logger.info("=" * 70)
    logger.info("FloodMind-AIoT DSS — MULTI-STATION AI ENGINE")
    logger.info("=" * 70)
    logger.info(f"Version: {VERSION} | Device: {DEVICE_ID}")
    logger.info(f"Ingest: {TOPIC_FLOOD_MONITOR_WILDCARD}")
    logger.info(f"Publish: {TOPIC_AI_PREDICTION_PREFIX}/<station_id>")
    logger.info("=" * 70)
    AIPredictor().run()


if __name__ == "__main__":
    main()
