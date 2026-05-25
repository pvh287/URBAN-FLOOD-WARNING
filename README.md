# FloodMind-AIoT DSS: Hệ thống Hỗ trợ Ra quyết định Ngập lụt Đô thị

FloodMind-AIoT DSS (Decision Support System) là nền tảng hệ thống giám sát, cảnh báo sớm và hỗ trợ ra quyết định ngập lụt đô thị thời gian thực. Hệ thống là phiên bản nâng cấp toàn diện từ hệ thống giám sát ngập lụt đơn thuần.

## Tính năng Nổi bật (DSS Features)

Hệ thống hoạt động dựa trên cơ chế 8 Tầng (8-Layer AI Engine):
- **Tầng 1 - Data Quality:** Kiểm tra và chuẩn hóa dữ liệu đầu vào.
- **Tầng 2 - Sliding Window:** Gom 60 mẫu liên tục trong 5 phút để phân tích thay vì chỉ dùng dữ liệu tức thời.
- **Tầng 3 - Hydro Features:** Trích xuất đặc trưng thủy văn (mực nước, tốc độ tăng, lưu lượng, tổng mưa).
- **Tầng 4 - Forecasting:** AI dự báo mực nước 5 phút tiếp theo (Conv1D-GRU model).
- **Tầng 5 - Classify:** Phân loại 4 mức độ: AN TOÀN, THEO DÕI, CẢNH BÁO, NGẬP LỤT.
- **Tầng 6 - Risk Score:** Kết hợp AI probabilities và thông số thủy văn cho ra điểm rủi ro (0-100).
- **Tầng 7 - Rule Guard:** Các rule deterministic đảm bảo an toàn, không bị phụ thuộc rủi ro vào AI model khi xảy ra thiên tai nguy hiểm.
- **Tầng 8 - Explainable AI:** Giải thích lý do ra quyết định và đề xuất hành động.

## Kiến trúc Hệ thống

- **1_Firmware_ESP32:** Firmware thiết bị thu thập dữ liệu đa cảm biến (mực nước, lưu lượng, lượng mưa) sử dụng FreeRTOS và SIM7600 (PPPoS) để kết nối MQTT. (Gửi dữ liệu với Payload Schema v1.0).
- **2_Server_AI_Engine:** Backend Python nhận dữ liệu MQTT, xử lý qua 8 tầng, đưa ra quyết định, gửi cảnh báo Telegram, và điều khiển còi báo động.
- **3_Web_Dashboard:** Giao diện Web hiển thị Dashboard hỗ trợ ra quyết định (DSS), theo dõi thời gian thực 60 mẫu gần nhất, hiển thị điểm Risk Score, AI Explanation và Kịch bản mô phỏng.

## Cách cài đặt và chạy

### 1. ESP32 Firmware
- Sử dụng ESP-IDF để build.
- Firmware đã được cập nhật `schema_version: 1.0` có chứa thông tin `rain_mm`, `rain_raw` và `timestamp`.
- Command nhận từ server cho buzzer hỗ trợ JSON hoặc text: `"ON"` hoặc `"OFF"`.

### 2. Chạy AI Backend Engine
Cài đặt thư viện:
```bash
cd 2_Server_AI_Engine
pip install paho-mqtt requests numpy pandas joblib python-dotenv keras
```

Tạo file `.env` (dựa trên `.env.example`):
```env
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
MQTT_BROKER=broker.emqx.io
MQTT_PORT=1883
```

Chạy Engine:
```bash
python ai_predictor.py
```
*Ghi chú: Nếu không có file model Keras, Engine sẽ tự động Fallback về Rule Guard deterministic để duy trì an toàn hệ thống (không dùng mock_predict random).*

### 3. Huấn luyện Model V2
Dataset: `2_Server_AI_Engine/data/processed_training_data.csv`
```bash
cd 2_Server_AI_Engine
python notebooks/train_model_v2.py
```
Điều này sẽ huấn luyện model Conv1D-GRU Multi-output với Keras 3. File sẽ lưu tại thư mục `models/` cùng với các scaler mới và file `model_metadata.json`.

### 4. Giao diện Web (Dashboard)
Chỉ cần mở file `3_Web_Dashboard/index.html` trực tiếp trên trình duyệt hoặc host thông qua extension Live Server.

### 5. Simulator (Kiểm thử)
Để chạy mô phỏng cảm biến đang hoạt động (với đầy đủ 3 trạm):
```bash
python esp32_simulator.py
```

## MQTT Topics & Payload Schema

- **Publish từ ESP32:** `flood/monitor/<station_id>/data`
- **AI Publish:** `ai/prediction/<station_id>`
- **Buzzer CMD:** `openhab/water/buzzer/cmd`

**Ví dụ Payload ESP32 (v1.0):**
```json
{
  "schema_version": "1.0",
  "station_id": "THAI_HA",
  "level_cm": 15.2,
  "flow_lpm": 2.5,
  "rain_mm": 0.0,
  "rain_raw": 4095,
  "rain_state": "DRY",
  "timestamp": "2026-05-25T10:00:00Z",
  "device_status": "OK"
}
```

**Ví dụ AI Payload (v2.0 DSS):**
```json
{
  "schema_version": "2.0",
  "model_name": "FloodMind-AIoT-DSS",
  "station_id": "THAI_HA",
  "current": { "level_cm": 15.2, "flow_lpm": 2.5, "rain_mm": 0.0 },
  "forecast": { "predicted_level_5min": 15.5 },
  "ai": {
    "class": 0,
    "status": "AN_TOAN",
    "risk_score": 10.5,
    "confidence": 98.2
  },
  "explanation": ["Tất cả chỉ số trong ngưỡng an toàn"],
  "recommended_action": "Tiếp tục giám sát"
}
```
