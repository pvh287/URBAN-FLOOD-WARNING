"""
=========================================================================
 FloodMind-AIoT DSS — Configuration Module
=========================================================================
 Centralised configuration for the FloodMind AI Engine.
 Loads secrets from environment variables / .env file.
=========================================================================
"""

import os
import logging
from pathlib import Path

# ---------- try to load .env automatically (python-dotenv) ----------
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent / '.env'
    load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv not installed — fall back to OS env vars

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ==================== MQTT CONFIGURATION ====================
MQTT_BROKER = os.environ.get('MQTT_BROKER', 'broker.emqx.io')
MQTT_PORT = int(os.environ.get('MQTT_PORT', 1883))
MQTT_KEEPALIVE = 60

# MQTT Topics
TOPIC_DATA = 'openhab/water/data'                          # Legacy single-station (optional)
TOPIC_FLOOD_MONITOR_WILDCARD = 'flood/monitor/+/data'      # Multi-station sensor stream
TOPIC_BUZZER_CMD = 'openhab/water/buzzer/cmd'              # Send ON/OFF to ESP32 buzzer
TOPIC_BUZZER_STATUS = 'openhab/water/buzzer/status'        # Monitor buzzer status
TOPIC_AI_PREDICTION_PREFIX = 'ai/prediction'               # AI publishes to ai/prediction/{station_id}
TOPIC_SENSOR_COMMAND = 'sensor/command'                    # Web → backend: buzzer / reset_ai

# WebSocket cho trình duyệt (mqtt.js)
MQTT_WS_URL = 'ws://broker.emqx.io:8083/mqtt'

# ==================== OPEN-METEO API (FREE - NO API KEY) ====================
OPEN_METEO_API_URL = 'https://api.open-meteo.com/v1/forecast'
API_TIMEOUT = 10
API_CALL_INTERVAL = 900  # 15 minutes

# ==================== TELEGRAM ALERTS (FROM ENV) ====================
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

if not TELEGRAM_TOKEN:
    logger.warning("[CONFIG] TELEGRAM_TOKEN not set — Telegram alerts disabled. "
                   "Set via .env or environment variable.")
if not TELEGRAM_CHAT_ID:
    logger.warning("[CONFIG] TELEGRAM_CHAT_ID not set — Telegram alerts disabled.")

# ==================== LOCATION COORDINATES ====================
LAT = 21.009498200175187
LON = 105.82395392902815
LOCATION_NAME = "Ngã tư Thái Hà - Chùa Bộc, Hà Nội"

# ==================== TOPOGRAPHIC INDEX ====================
TOPO_INDEX = 0.8

# ==================== DIRECTORY STRUCTURE ====================
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
MODEL_DIR = BASE_DIR / 'models'

DATA_DIR.mkdir(parents=True, exist_ok=True)
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# ==================== MODEL FILES ====================
MODEL_FILE_V2 = str(MODEL_DIR / 'flood_model_v2.keras')
MODEL_FILE_LEGACY = str(MODEL_DIR / 'flood_model.h5')
MODEL_FILE = MODEL_FILE_LEGACY                              # alias for backward compat

SCALER_SENSOR_FILE = str(MODEL_DIR / 'scaler_sensor.pkl')
SCALER_HYDRO_FILE = str(MODEL_DIR / 'scaler_hydro.pkl')
SCALER_WEATHER_FILE = str(MODEL_DIR / 'scaler_weather.pkl')
SCALER_LEGACY_FILE = str(MODEL_DIR / 'scaler.pkl')

MODEL_METADATA_FILE = str(MODEL_DIR / 'model_metadata.json')

SENSOR_HISTORY_FILE = str(DATA_DIR / 'sensor_history.csv')

# ==================== ANOMALY DETECTION THRESHOLDS ====================
MAX_DELTA_LEVEL = 20
MIN_FLOW_RATE = 0.0
MAX_FLOW_RATE = 100.0

# ==================== FLOOD RISK CLASSIFICATION (4-CLASS) ====================
CLASS_SAFE = 0
CLASS_WATCH = 1
CLASS_WARNING = 2
CLASS_FLOOD = 3

CLASS_NAMES = {
    CLASS_SAFE: 'AN_TOAN',
    CLASS_WATCH: 'THEO_DOI',
    CLASS_WARNING: 'CANH_BAO',
    CLASS_FLOOD: 'NGAP_LUT',
}
CLASS_NAMES_EN = {
    CLASS_SAFE: 'SAFE',
    CLASS_WATCH: 'WATCH',
    CLASS_WARNING: 'WARNING',
    CLASS_FLOOD: 'FLOOD',
}

# Legacy 3-class aliases (for backward compat with old model)
LEGACY_CLASS_SAFE = 0
LEGACY_CLASS_WARNING = 1
LEGACY_CLASS_FLOOD = 2

OUTPUT_CLASSES = 3          # legacy model output
OUTPUT_CLASSES_V2 = 4       # v2 model output

# ==================== HYDROLOGICAL THRESHOLDS (Rule Guard) ====================
H_SAFE = 20                 # cm — below this is definitely safe
H_WATCH = 30                # cm — watch threshold
H_WARNING = 40              # cm — potential risk
H_DANGER = 60               # cm — dangerous

RAIN_HEAVY_5MIN = 10        # mm total in 5-min window → heavy rain
SLOPE_H_WARNING = 3         # cm/min rising rate → watch
SLOPE_H_DANGER = 6          # cm/min rising rate → danger

DRAINAGE_STRESS_HIGH = 10   # h/(q+1) ratio → drainage overwhelmed

# ==================== ALERT THRESHOLDS ====================
FLOOD_ALERT_THRESHOLD = 0.75    # Risk score / confidence to trigger buzzer + Telegram
WARNING_ALERT_THRESHOLD = 0.50

# ==================== LSTM / MODEL PARAMETERS ====================
LSTM_TIME_STEPS = 60        # 60 samples × 5 s/sample = 300 s = 5 minutes
WINDOW_MINUTES = 5
SAMPLE_INTERVAL_SEC = 5

LSTM_FEATURES = 5           # [level, flow, flow_efficiency, delta_level, rain_local]
LSTM_UNITS = 64
LSTM_DROPOUT = 0.2

WEATHER_FEATURES = 3        # [rain_forecast, time_decay, topo_index]
WEATHER_DENSE_UNITS = 16

FUSION_DENSE_UNITS = 64

# Training parameters
BATCH_SIZE = 32
EPOCHS = 50
VALIDATION_SPLIT = 0.2
TEST_SPLIT = 0.1

# ==================== SENSOR RANGE LIMITS (validation) ====================
LEVEL_MIN = 0.0
LEVEL_MAX = 200.0
FLOW_MIN = 0.0
FLOW_MAX = 100.0
RAIN_MIN = 0.0
RAIN_MAX = 100.0

# ==================== DATA FRESHNESS ====================
SENSOR_STALE_SECONDS = 15       # seconds without data → stale
SENSOR_OFFLINE_SECONDS = 30     # seconds without data → offline

# ==================== LOGGING ====================
LOG_LEVEL = logging.INFO
ENABLE_FILE_LOGGING = True
LOG_FILE = str(DATA_DIR / 'system.log')

if ENABLE_FILE_LOGGING:
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setLevel(LOG_LEVEL)
    formatter = logging.Formatter('[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s')
    file_handler.setFormatter(formatter)
    logging.getLogger().addHandler(file_handler)

# ==================== SYSTEM CONSTANTS ====================
SYSTEM_NAME = "FloodMind-AIoT DSS"
VERSION = "2.0.0"
DEVICE_ID = "SmartWaterMonitor_01"

logger.info(f"Configuration loaded: {SYSTEM_NAME} v{VERSION}")
logger.info(f"Location: {LOCATION_NAME} (LAT={LAT}, LON={LON})")
logger.info(f"Topographic Index: {TOPO_INDEX}")
logger.info(f"Telegram alerts: {'ENABLED' if TELEGRAM_TOKEN else 'DISABLED'}")