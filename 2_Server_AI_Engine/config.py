"""
Configuration module for Smart Water Flood Warning System
Manages MQTT, API keys, coordinates, and system constants
"""

import os
import logging

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ==================== MQTT CONFIGURATION ====================
# Trùng broker với 3_Web_Dashboard/script.js (WebSocket port 8000)
MQTT_BROKER = 'broker.emqx.io'
MQTT_PORT = 1883                # Cổng TCP cho Python (Simulator & AI Engine)
MQTT_KEEPALIVE = 60

# MQTT Topics
TOPIC_DATA = 'openhab/water/data'              # Legacy single-station (optional)
TOPIC_FLOOD_MONITOR_WILDCARD = 'flood/monitor/+/data'  # Multi-station sensor stream
TOPIC_BUZZER_CMD = 'openhab/water/buzzer/cmd'  # Send ON/OFF to ESP32 buzzer
TOPIC_BUZZER_STATUS = 'openhab/water/buzzer/status'  # Monitor buzzer status
TOPIC_AI_PREDICTION_PREFIX = 'ai/prediction'   # AI publishes to ai/prediction/{station_id}
TOPIC_SENSOR_COMMAND = 'sensor/command'        # Web → backend: buzzer / reset_ai

# WebSocket cho trình duyệt (mqtt.js) — HiveMQ public
MQTT_WS_URL = 'ws://broker.emqx.io:8083/mqtt'

# ==================== OPENWEATHERMAP API ====================
# DEPRECATED: Switched to Open-Meteo (Free, No API Key Required)
# Get API key from: https://openweathermap.org/api
# OWM_API_KEY = 'YOUR_API_KEY_HERE'
# OWM_API_URL = 'https://api.openweathermap.org/data/2.5/weather'
# OWM_FORECAST_URL = 'https://api.openweathermap.org/data/2.5/forecast'

# ==================== OPEN-METEO API (FREE - NO API KEY) ====================
OPEN_METEO_API_URL = 'https://api.open-meteo.com/v1/forecast'

# API request timeout (seconds)
API_TIMEOUT = 10

# API call interval (seconds) - Call every 15 minutes
API_CALL_INTERVAL = 900  # 15 * 60

# ==================== LOCATION COORDINATES ====================
# Ngã tư Thái Hà – Chùa Bộc (Đống Đa, Hà Nội) — khớp bản đồ dashboard
LAT = 21.009498200175187
LON = 105.82395392902815
LOCATION_NAME = "Ngã tư Thái Hà - Chùa Bộc, Hà Nội"

# ==================== TOPOGRAPHIC INDEX ====================
# 0.8 represents low-lying area (Vùng trũng) - High flood risk
# 1.0 = normal terrain, < 1.0 = depression zone
TOPO_INDEX = 0.8

# ==================== DATA COLLECTION ====================
# Historical data storage
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
SENSOR_HISTORY_FILE = os.path.join(DATA_DIR, 'sensor_history.csv')
MODEL_DIR = os.path.join(os.path.dirname(__file__), 'models')
MODEL_FILE = os.path.join(MODEL_DIR, 'flood_model.h5')

# Create directories if not exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(MODEL_DIR, exist_ok=True)

# ==================== ANOMALY DETECTION THRESHOLDS ====================
# Maximum acceptable water level change per minute (cm)
MAX_DELTA_LEVEL = 20  # If exceeds this without rain, it's an anomaly

# Minimum flow rate threshold (m³/s) - for sanity check
MIN_FLOW_RATE = 0.0
MAX_FLOW_RATE = 100.0

# ==================== FLOOD DETECTION THRESHOLDS ====================
# Water level thresholds (cm)
SAFE_LEVEL = 30
WARNING_LEVEL = 50
CRITICAL_LEVEL = 80

# Risk classification (AI model output)
CLASS_SAFE = 0          # Probability >= threshold -> SAFE
CLASS_WARNING = 1       # Probability >= threshold -> WARNING
CLASS_FLOOD = 2         # Probability >= threshold -> FLOOD ALERT

# Confidence threshold for sending commands
FLOOD_ALERT_THRESHOLD = 0.8  # 80% confidence to trigger buzzer

# ==================== LSTM MODEL PARAMETERS ====================
# IoT data input shape (60 minutes history, 5 features)
LSTM_TIME_STEPS = 60  # 60 minutes of historical data
LSTM_FEATURES = 5      # [level, flow, flow_efficiency, delta_level, rain_local]
LSTM_UNITS = 64        # LSTM hidden units
LSTM_DROPOUT = 0.2

# Weather API input shape
WEATHER_FEATURES = 3   # [rain_forecast, time_decay, topo_index]
WEATHER_DENSE_UNITS = 16

# Fusion layer (matches train_model merged Dense after Concatenate)
FUSION_DENSE_UNITS = 64
OUTPUT_CLASSES = 3     # Safe, Warning, Flood

# Training parameters (for future use)
BATCH_SIZE = 32
EPOCHS = 50
VALIDATION_SPLIT = 0.2
TEST_SPLIT = 0.1

# ==================== LOGGING CONFIGURATION ====================
# Log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_LEVEL = logging.INFO

# Additional logging features
ENABLE_FILE_LOGGING = True
LOG_FILE = os.path.join(DATA_DIR, 'system.log')

if ENABLE_FILE_LOGGING:
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setLevel(LOG_LEVEL)
    formatter = logging.Formatter('[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s')
    file_handler.setFormatter(formatter)
    logging.getLogger().addHandler(file_handler)

# ==================== SYSTEM CONSTANTS ====================
SYSTEM_NAME = "Smart Water Flood Warning System"
VERSION = "1.0.0"
DEVICE_ID = "SmartWaterMonitor_01"

logger.info(f"Configuration loaded: {SYSTEM_NAME} v{VERSION}")
logger.info(f"Location: {LOCATION_NAME} (LAT={LAT}, LON={LON})")
logger.info(f"Topographic Index: {TOPO_INDEX} (Low-lying area)")