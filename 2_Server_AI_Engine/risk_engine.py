"""
=========================================================================
 FloodMind-AIoT DSS — Risk Score + Rule Guard + Explainable AI
=========================================================================
 Tầng 5-8: Risk Classifier, Risk Score Fusion, Rule Guard, XAI
=========================================================================
"""
import logging
from config import (
    CLASS_SAFE, CLASS_WATCH, CLASS_WARNING, CLASS_FLOOD,
    H_SAFE, H_WATCH, H_WARNING, H_DANGER,
    RAIN_HEAVY_5MIN, SLOPE_H_WARNING, SLOPE_H_DANGER,
    DRAINAGE_STRESS_HIGH, FLOOD_ALERT_THRESHOLD, WARNING_ALERT_THRESHOLD,
)

logger = logging.getLogger(__name__)


def level_rule_class(level_cm):
    if level_cm >= H_DANGER:
        return CLASS_FLOOD
    elif level_cm >= H_WARNING:
        return CLASS_WARNING
    elif level_cm >= H_WATCH:
        return CLASS_WATCH
    else:
        return CLASS_SAFE


def calculate_risk_score(probs, hydro, data_quality='GOOD'):
    """
    Tầng 6: Risk Score Fusion 0–100.
    Kết hợp xác suất AI + đặc trưng thủy văn.
    """
    p_flood = float(probs.get('flood', 0))
    p_warning = float(probs.get('warning', 0))

    h_now = hydro.get('h_now', 0)
    slope_h = hydro.get('slope_h', 0)
    r_sum = hydro.get('r_sum', 0)
    drainage_stress = hydro.get('drainage_stress', 0)

    norm_level = min(h_now / max(H_DANGER, 1), 1.0)
    norm_slope = min(abs(slope_h) / max(SLOPE_H_DANGER, 1), 1.0)
    norm_rain = min(r_sum / max(RAIN_HEAVY_5MIN, 1), 1.0)
    norm_drain = min(drainage_stress / max(DRAINAGE_STRESS_HIGH, 1), 1.0)
    ai_factor = max(p_flood, p_warning * 0.7)

    score = 100.0 * (
        0.40 * norm_level
        + 0.20 * norm_slope
        + 0.15 * norm_rain
        + 0.15 * ai_factor
        + 0.10 * norm_drain
    )

    if h_now >= H_DANGER:
        score = max(score, 90.0)
    elif h_now >= H_WARNING:
        score = max(score, 65.0)
    elif h_now >= H_WATCH:
        score = max(score, 35.0)

    if data_quality != 'GOOD':
        score = max(score, 30.0)

    return round(max(0.0, min(100.0, score)), 1)


def rule_guard(hydro, predicted_class, risk_score, data_quality='GOOD',
               predicted_level_5min=None):
    """
    Tầng 7: Rule Guard — luật an toàn bắt buộc.
    Returns: (final_class, final_risk_score, rule_class)
    """
    h_now = hydro.get('h_now', 0)
    slope_h = hydro.get('slope_h', 0)
    r_sum = hydro.get('r_sum', 0)
    delta_h = hydro.get('delta_h_5min', 0)

    rule_class = level_rule_class(h_now)

    if predicted_level_5min is not None:
        if predicted_level_5min >= H_DANGER:
            rule_class = max(rule_class, CLASS_FLOOD)
        elif predicted_level_5min >= H_WARNING:
            rule_class = max(rule_class, CLASS_WARNING)

    if r_sum >= RAIN_HEAVY_5MIN and slope_h > 0:
        rule_class = max(rule_class, CLASS_WARNING)

    if slope_h >= SLOPE_H_DANGER:
        rule_class = max(rule_class, CLASS_FLOOD)
    elif slope_h >= SLOPE_H_WARNING:
        rule_class = max(rule_class, CLASS_WARNING)

    if data_quality != 'GOOD':
        rule_class = max(rule_class, CLASS_WATCH)

    final_class = max(predicted_class, rule_class)

    return final_class, round(max(0, min(100, risk_score)), 1), rule_class


def generate_explanation(hydro, final_class, risk_score, data_quality,
                         ai_class, predicted_level_5min=None):
    """
    Tầng 8: Explainable AI — giải thích lý do cảnh báo.
    """
    reasons = []
    h_now = hydro.get('h_now', 0)
    slope_h = hydro.get('slope_h', 0)
    r_sum = hydro.get('r_sum', 0)
    drainage_eff = hydro.get('drainage_eff', 0)
    drainage_stress = hydro.get('drainage_stress', 0)
    delta_h = hydro.get('delta_h_5min', 0)

    if h_now >= H_DANGER:
        reasons.append(f"Mực nước hiện tại ({h_now:.1f} cm) vượt ngưỡng nguy hiểm ({H_DANGER} cm)")
    elif h_now >= H_WARNING:
        reasons.append(f"Mực nước ({h_now:.1f} cm) vượt ngưỡng cảnh báo ({H_WARNING} cm)")
    elif h_now >= H_WATCH:
        reasons.append(f"Mực nước ({h_now:.1f} cm) vượt ngưỡng theo dõi ({H_WATCH} cm)")
    elif h_now >= H_SAFE:
        reasons.append(f"Mực nước ({h_now:.1f} cm) ở mức cần theo dõi")

    if slope_h >= SLOPE_H_DANGER:
        reasons.append(f"Mực nước tăng rất nhanh ({slope_h:.2f} cm/phút)")
    elif slope_h >= SLOPE_H_WARNING:
        reasons.append(f"Mực nước đang tăng ({slope_h:.2f} cm/phút)")

    if r_sum >= RAIN_HEAVY_5MIN:
        reasons.append(f"Tổng lượng mưa 5 phút ({r_sum:.1f} mm) vượt ngưỡng mưa lớn")
    elif r_sum > 0:
        reasons.append(f"Có mưa cục bộ ({r_sum:.1f} mm trong 5 phút)")

    if drainage_stress >= DRAINAGE_STRESS_HIGH:
        reasons.append("Hiệu suất thoát nước giảm mạnh — hệ thống thoát nước quá tải")

    if predicted_level_5min is not None:
        if predicted_level_5min >= H_DANGER:
            reasons.append(f"Dự báo mực nước 5 phút tới ({predicted_level_5min:.1f} cm) có thể vượt ngưỡng nguy hiểm")
        elif predicted_level_5min >= H_WARNING:
            reasons.append(f"Dự báo mực nước 5 phút tới ({predicted_level_5min:.1f} cm) có thể vượt ngưỡng cảnh báo")

    if data_quality == 'BAD':
        reasons.append("Chất lượng dữ liệu: BAD — Dữ liệu cảm biến không đáng tin cậy, cần kiểm tra.")
    elif data_quality == 'DEGRADED':
        reasons.append("Chất lượng dữ liệu: DEGRADED — Dữ liệu cảm biến chưa đầy đủ, cần theo dõi thêm.")
    elif data_quality == 'STALE':
        reasons.append("Chất lượng dữ liệu: STALE — Dữ liệu trạm bị chậm, cần kiểm tra kết nối/cảm biến.")
    elif data_quality != 'GOOD':
        reasons.append(f"Chất lượng dữ liệu: {data_quality} — cần kiểm tra cảm biến")

    if final_class > ai_class and ai_class == CLASS_SAFE:
        reasons.append("Rule Guard đã nâng cảnh báo do dữ liệu cảm biến vượt ngưỡng an toàn.")
    elif final_class > ai_class:
        reasons.append(f"Rule Guard đã điều chỉnh mức cảnh báo lên {final_class} do điều kiện thực tế")

    if not reasons:
        reasons.append("Tất cả chỉ số trong ngưỡng an toàn")

    return reasons


def generate_recommended_action(final_class, data_quality='GOOD'):
    """Tầng 8: Đề xuất hành động."""
    if data_quality == 'STALE':
        return "Dữ liệu trạm bị chậm, kiểm tra kết nối/cảm biến"
    elif data_quality == 'BAD':
        return "Kiểm tra kết nối/cảm biến trước khi ra quyết định"

    actions = {
        CLASS_SAFE: "Tiếp tục giám sát bình thường",
        CLASS_WATCH: "Theo dõi sát trong 5 phút tới",
        CLASS_WARNING: "Chuẩn bị bật còi cảnh báo, kiểm tra khu vực trạm",
        CLASS_FLOOD: "BẬT CÒI CẢNH BÁO, gửi Telegram, triển khai xử lý khẩn cấp",
    }
    return actions.get(final_class, "Tiếp tục giám sát")


def map_legacy_class_to_v2(legacy_class):
    """Chuyển 3-class cũ (0,1,2) sang 4-class mới (0,1,2,3)."""
    mapping = {0: CLASS_SAFE, 1: CLASS_WARNING, 2: CLASS_FLOOD}
    return mapping.get(int(legacy_class), CLASS_SAFE)


def risk_score_to_class(risk_score):
    """Phân loại từ risk score nếu cần."""
    if risk_score >= 75:
        return CLASS_FLOOD
    elif risk_score >= 50:
        return CLASS_WARNING
    elif risk_score >= 30:
        return CLASS_WATCH
    return CLASS_SAFE
