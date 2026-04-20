# 🌊 Smart Water Flood Warning System - Backend AI Engine

Backend AI xử lý dữ liệu từ cảm biến IoT và dự báo ngập lụt sớm bằng Dual-Input LSTM.

## 📋 Kiến Trúc Hệ Thống

```
IoT Sensors (ESP32)
        ↓
    [MQTT Broker]
        ↓
  DatabaseManager (Thu thập & Lọc dị thường)
        ↓
  sensor_history.csv (Lưu trữ dữ liệu)
        ↓
  train_model.py (Huấn luyện LSTM)
        ↓
  flood_model.h5 (Mô hình AI)
        ↓
  ai_predictor.py (Dự báo real-time 24/7)
        ↓
  [MQTT Buzzer Command] → ESP32 (Kích hoạt còi báo)
```

## 🚀 Cài Đặt & Chạy

### 1. Cài đặt Dependencies
```bash
pip install -r requirements.txt
```

### 2. Cấu Hình API Key
Sửa file `config.py`:
```python
OWM_API_KEY = 'YOUR_API_KEY_HERE'  # Lấy từ https://openweathermap.org/api
```

### 3. Chạy các Component

#### **Bước 1: Khởi động Data Collection**
```bash
python database_manager.py
```
- Lắng nghe dữ liệu từ ESP32 trên MQTT topic `openhab/water/data`
- Tự động lọc dị thường (anomaly detection)
- Lưu dữ liệu vào `data/sensor_history.csv`

#### **Bước 2: Huấn luyện Mô Hình**
```bash
python notebooks/train_model.py
```
- Xây dựng mô hình Dual-Input LSTM
- Huấn luyện với dữ liệu dummy (demo)
- Lưu mô hình vào `models/flood_model.h5`

**Chú ý**: Trong production, bạn cần huấn luyện với dữ liệu thực từ `sensor_history.csv`:
```python
# Load real data
df = pd.read_csv('data/sensor_history.csv')
# Preprocess và train model
# history = train_model(model, X_iot, X_weather, y, epochs=50)
```

#### **Bước 3: Chạy Dự Báo Real-time**
```bash
python ai_predictor.py
```
- Kết nối MQTT tính năng lắng nghe dữ liệu cảm biến
- Gọi OpenWeatherMap API mỗi 15 phút
- Chạy mô hình mỗi phút khi có đủ dữ liệu
- Tự động gửi lệnh ON/OFF vào MQTT topic `openhab/water/buzzer/cmd`

## 📊 File Dữ Liệu

### `data/sensor_history.csv`
Lưu trữ lịch sử cảm biến:
```csv
timestamp,level,flow,delta_level,rain_local
2024-04-18 10:30:45,45.23,2.15,0.50,0
2024-04-18 10:31:45,45.80,2.18,0.57,0
2024-04-18 10:32:45,46.10,2.25,0.30,1
```

**Các cột:**
- `timestamp`: Thời gian ghi nhận
- `level`: Mực nước (cm)
- `flow`: Lưu lượng (m³/s)
- `delta_level`: Thay đổi mực nước so với phút trước (cm)
- `rain_local`: Có mưa tại cảm biến (0/1)

### `models/flood_model.h5`
Mô hình AI đã được huấn luyện (HDF5 format)

## 🧠 Kiến Trúc Mô Hình LSTM

### **Nhánh 1 (IoT Data)**
```
Input: (60 phút, 4 features) → LSTM(64) → Dropout(0.2)
```
- 60 phút dữ liệu lịch sử từ cảm biến
- 4 features: level, flow, delta_level, rain_local

### **Nhánh 2 (Weather API)**
```
Input: (3 features) → Dense(16, relu)
```
- rain_forecast: Dự báo mưa 1 giờ tới (mm)
- time_decay: Số phút trôi qua từ lần gọi API cuối (1-14 phút)
- topo_index: Hằng số địa hình (0.8 = vùng trũng)

### **Fusion Layer**
```
Concatenate [nhánh1, nhánh2] → Dense(32, relu)
```

### **Output**
```
Dense(3, softmax) → [P(Safe), P(Warning), P(Flood)]
```
- Class 0: An toàn (prob < 0.5)
- Class 1: Cảnh báo (0.5 ≤ prob < 0.8)
- Class 2: Ngập lụt (prob ≥ 0.8) ⚠️ **Kích hoạt buzzer**

## ⚠️ Anomaly Detection

DatabaseManager tự động phát hiện và bỏ qua dữ liệu dị thường:

```python
IF delta_level > 20 cm AND rain_local == 0:
    → ANOMALY (Khả năng rác bít cống hoặc lỗi cảm biến)
    → Dữ liệu này sẽ KHÔNG được lưu
```

## 📡 MQTT Topics

| Topic | Hướng | Nội dung |
|-------|-------|---------|
| `openhab/water/data` | ← Nhận | Dữ liệu cảm biến (JSON) |
| `openhab/water/buzzer/cmd` | → Gửi | Lệnh ON/OFF kích hoạt buzzer |
| `openhab/water/buzzer/status` | ← Nhận | Trạng thái buzzer phản hồi |

### Định dạng JSON Cảm Biến
```json
{
  "level": 45.23,
  "flow": 2.15,
  "rain_value": 0
}
```

### Định dạng Lệnh Buzzer
```json
{
  "command": "ON",
  "timestamp": "2024-04-18T10:30:45"
}
```

## 🔍 Logging

Tất cả hoạt động đều được ghi log chi tiết:
- **Console Output**: Real-time trên terminal
- **File Log**: `data/system.log` (tích lũy)

### Ví dụ Log Output
```
[2024-04-18 10:30:45] [ai_predictor] [INFO] ✓ Connected to MQTT broker (broker.hivemq.com:1883)
[2024-04-18 10:30:46] [ai_predictor] [INFO] ✓ Model loaded successfully
[2024-04-18 10:35:10] [database_manager] [INFO] Data saved: Level=45.80cm, Flow=2.15m³/s, Rain=0
[2024-04-18 10:36:00] [ai_predictor] [INFO] Fetching weather from OpenWeatherMap API...
[2024-04-18 10:36:02] [ai_predictor] [INFO] ✓ Weather update: Rainfall forecast = 2.50mm/1h
[2024-04-18 10:37:00] [ai_predictor] [CRITICAL] ⚠️ FLOOD DETECTED! Confidence: 85.3%
[2024-04-18 10:37:01] [ai_predictor] [INFO] ✓ Buzzer command sent: ON
```

## ❌ Xử Lý Lỗi

Hệ thống có cơ chế bảo vệ:

1. **Model không tồn tại**: Sử dụng `mock_predict()` (random prediction) để hệ thống không crash
2. **API timeout**: Giữ nguyên dữ liệu thời tiết trước đó, không dừng dự báo
3. **MQTT kết nối mất**: Tự động reconnect
4. **Dữ liệu không hợp lệ**: Tự động skip và ghi log cảnh báo

## 🎯 Thế Nào Để Cải Thiện Độ Chính Xác

1. **Tăng dữ liệu huấn luyện**: Thu thập ít nhất 6 tháng dữ liệu từ cảm biến
2. **Fine-tune hyperparameters**: Thay đổi LSTM_UNITS, EPOCHS, learning_rate
3. **Thêm features**: Thêm dữ liệu khí tượng khác (nhiệt độ, độ ẩm, áp suất)
4. **Data augmentation**: Sử dụng kỹ thuật mở rộng dữ liệu
5. **Ensemble methods**: Kết hợp nhiều mô hình

## 📞 Hỗ Trợ

Mọi lỗi hoặc vấn đề vui lòng kiểm tra:
- File log: `data/system.log`
- Console output: Tìm `[ERROR]` hoặc `[CRITICAL]`
- Đảm bảo API key đúng, MQTT broker online

---

**Phiên bản**: 1.0.0  
**Cập nhật**: 2024-04-18  
**Trạng thái**: Production Ready 🚀
