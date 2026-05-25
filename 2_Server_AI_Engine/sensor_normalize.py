"""
=========================================================================
 FloodMind-AIoT DSS — Sensor Payload Normalizer (Tầng 1)
=========================================================================
 Hỗ trợ cả payload cũ (level/flow) và mới (schema_version 1.0).
 Kiểm tra chất lượng dữ liệu (Data Quality).
=========================================================================
"""
import math
import logging
from datetime import datetime, timedelta

from config import (
    LEVEL_MIN, LEVEL_MAX, FLOW_MIN, FLOW_MAX, RAIN_MIN, RAIN_MAX,
    SENSOR_STALE_SECONDS,
)

logger = logging.getLogger(__name__)


def normalize_sensor_payload(data: dict, fallback_station_id: str = 'UNKNOWN'):
    """
    Tầng 1: Chuẩn hóa payload cảm biến (cũ hoặc mới) về format thống nhất.

    Returns:
        dict với keys: station_id, timestamp, level_cm, flow_lpm, rain_mm,
                       rain_raw, data_quality, rain_missing
    """
    # Station ID
    station_id = data.get('station_id', fallback_station_id)

    # --- Level ---
    level = _first_valid(data, ['level_cm', 'level'])

    # --- Flow ---
    flow = _first_valid(data, ['flow_lpm', 'flow'])

    # --- Rain ---
    rain_mm = _first_valid(data, ['rain_mm', 'rain'])
    rain_raw = data.get('rain_raw', None)
    rain_missing = False

    if rain_mm is None and rain_raw is not None:
        # Calibration đơn giản từ ADC raw (0-4095)
        try:
            raw = float(rain_raw)
            rain_mm = max(0.0, (4095.0 - raw) / 4095.0 * 20.0)
        except (ValueError, TypeError):
            rain_mm = 0.0
            rain_missing = True
            logger.warning(f"[{station_id}] rain_raw không hợp lệ: {rain_raw}")

    if rain_mm is None:
        rain_mm = 0.0
        rain_missing = True
        logger.warning(f"[{station_id}] Thiếu dữ liệu mưa — đánh dấu DEGRADED")

    # --- Timestamp ---
    ts = data.get('timestamp', None)
    if ts is not None:
        if isinstance(ts, (int, float)):
            # epoch seconds or uptime — use current time
            timestamp = datetime.now()
        elif isinstance(ts, str):
            try:
                timestamp = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                timestamp = datetime.now()
        else:
            timestamp = datetime.now()
    else:
        timestamp = datetime.now()

    # --- Data Quality ---
    data_quality = 'GOOD'
    if rain_missing:
        data_quality = 'DEGRADED'
    if level is None or flow is None:
        data_quality = 'BAD'

    device_status = data.get('device_status', 'UNKNOWN')
    if device_status == 'SENSOR_WARN':
        data_quality = 'DEGRADED'

    return {
        'station_id': station_id,
        'timestamp': timestamp,
        'level_cm': float(level) if level is not None else 0.0,
        'flow_lpm': float(flow) if flow is not None else 0.0,
        'rain_mm': float(rain_mm),
        'rain_raw': rain_raw,
        'data_quality': data_quality,
        'rain_missing': rain_missing,
    }


def validate_sensor_sample(sample: dict):
    """
    Tầng 1: Kiểm tra dữ liệu cảm biến.
    Returns: data_quality ('GOOD', 'DEGRADED', 'BAD')
    """
    quality = sample.get('data_quality', 'GOOD')

    level = sample.get('level_cm', 0)
    flow = sample.get('flow_lpm', 0)
    rain = sample.get('rain_mm', 0)

    # Check NaN
    if _is_nan(level) or _is_nan(flow):
        return 'BAD'

    # Check range
    if not (LEVEL_MIN <= level <= LEVEL_MAX):
        logger.warning(f"Level ngoài khoảng: {level}")
        return 'DEGRADED'

    if not (FLOW_MIN <= flow <= FLOW_MAX):
        logger.warning(f"Flow ngoài khoảng: {flow}")
        return 'DEGRADED'

    if rain < RAIN_MIN or rain > RAIN_MAX:
        quality = 'DEGRADED'

    # Check timestamp freshness
    ts = sample.get('timestamp')
    if ts and isinstance(ts, datetime):
        age = (datetime.now() - ts).total_seconds()
        if age > SENSOR_STALE_SECONDS:
            logger.warning(f"Dữ liệu cũ: {age:.0f}s")
            quality = 'DEGRADED' if quality == 'GOOD' else quality

    if sample.get('rain_missing', False) and quality == 'GOOD':
        quality = 'DEGRADED'

    return quality


def _first_valid(data, keys):
    """Lấy giá trị đầu tiên hợp lệ từ danh sách keys."""
    for k in keys:
        v = data.get(k)
        if v is not None:
            try:
                val = float(v)
                if not math.isnan(val):
                    return val
            except (ValueError, TypeError):
                continue
    return None


def _is_nan(v):
    try:
        return math.isnan(float(v))
    except (ValueError, TypeError):
        return True
