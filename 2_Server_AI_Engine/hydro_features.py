"""
=========================================================================
 FloodMind-AIoT DSS — Hydrological Feature Engine (Tầng 3)
=========================================================================
 Trích xuất đặc trưng thủy văn từ cửa sổ trượt 60 mẫu (5 phút).
=========================================================================
"""
import numpy as np
import logging

logger = logging.getLogger(__name__)


def extract_hydro_features(data_list):
    """
    Tính đặc trưng thủy văn từ 60 mẫu gần nhất.

    Args:
        data_list: list[dict] với keys: level, flow, rain_local, timestamp

    Returns:
        dict chứa tất cả hydro features
    """
    n = len(data_list)
    if n == 0:
        return _empty_features()

    levels = np.array([d['level'] for d in data_list], dtype=np.float64)
    flows = np.array([d['flow'] for d in data_list], dtype=np.float64)
    rains = np.array([d.get('rain_local', 0.0) for d in data_list], dtype=np.float64)

    h_now = float(levels[-1])
    h_first = float(levels[0])
    h_min = float(np.nanmin(levels))
    h_max = float(np.nanmax(levels))
    h_avg = float(np.nanmean(levels))
    delta_h_5min = h_now - h_first
    slope_h = delta_h_5min / 5.0  # cm/min

    q_now = float(flows[-1])
    q_first = float(flows[0])
    q_min = float(np.nanmin(flows))
    q_max = float(np.nanmax(flows))
    q_avg = float(np.nanmean(flows))
    delta_q_5min = q_now - q_first
    slope_q = delta_q_5min / 5.0

    r_now = float(rains[-1])
    r_sum = float(np.nansum(rains))
    r_max = float(np.nanmax(rains))
    r_avg = float(np.nanmean(rains))
    rain_intensity = r_sum / max(n * 5.0 / 60.0, 0.001)  # mm/min approx

    drainage_eff = q_now / (h_now + 1.0)
    drainage_stress = h_now / (q_now + 1.0)
    flood_pressure = h_now * max(r_sum, 0.001)
    rate_risk = slope_h * max(r_sum, 0.001)

    return {
        'h_now': h_now, 'h_first': h_first, 'h_min': h_min, 'h_max': h_max,
        'h_avg': h_avg, 'delta_h_5min': delta_h_5min, 'slope_h': slope_h,
        'q_now': q_now, 'q_first': q_first, 'q_min': q_min, 'q_max': q_max,
        'q_avg': q_avg, 'delta_q_5min': delta_q_5min, 'slope_q': slope_q,
        'r_now': r_now, 'r_sum': r_sum, 'r_max': r_max, 'r_avg': r_avg,
        'rain_intensity': rain_intensity,
        'drainage_eff': round(drainage_eff, 4),
        'drainage_stress': round(drainage_stress, 4),
        'flood_pressure': round(flood_pressure, 4),
        'rate_risk': round(rate_risk, 4),
    }


def _empty_features():
    keys = [
        'h_now', 'h_first', 'h_min', 'h_max', 'h_avg', 'delta_h_5min', 'slope_h',
        'q_now', 'q_first', 'q_min', 'q_max', 'q_avg', 'delta_q_5min', 'slope_q',
        'r_now', 'r_sum', 'r_max', 'r_avg', 'rain_intensity',
        'drainage_eff', 'drainage_stress', 'flood_pressure', 'rate_risk',
    ]
    return {k: 0.0 for k in keys}
