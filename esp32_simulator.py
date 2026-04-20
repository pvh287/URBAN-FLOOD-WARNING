#!/usr/bin/env python3
import json
import time
import random
from datetime import datetime
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion

# --- Cấu hình kết nối ---
BROKER_ADDRESS = "broker.emqx.io"
BROKER_PORT = 1883
CLIENT_ID = "multi_station_sim_01"
ROUND_INTERVAL_SEC = 5.0

# --- Danh sách trạm ---
STATIONS = [
    {"station_id": "THAI_HA", "name": "Thái Hà", "lat": 21.0120, "lng": 105.8210},
    {"station_id": "PHAM_NGOC_THACH", "name": "Phạm Ngọc Thạch", "lat": 21.0092, "lng": 105.8348},
    {"station_id": "TRUONG_CHINH", "name": "Trường Chinh", "lat": 21.0014, "lng": 105.8261},
]

# --- Dữ liệu khởi tạo (Sẽ biến thiên liên tục) ---
STATION_DATA = {
    "THAI_HA": {"level": 6.0, "flow": 0.35, "rain": 0.5},
    "PHAM_NGOC_THACH": {"level": 26.0, "flow": 1.9, "rain": 4.0},
    "TRUONG_CHINH": {"level": 46.0, "flow": 0.55, "rain": 9.0},
}

class ESP32Simulator:
    def __init__(self):
        self.client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=CLIENT_ID)
        self.client.on_connect = self.on_connect
        self.is_connected = False

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            self.is_connected = True
            print(f"[OK] Kết nối MQTT {BROKER_ADDRESS}")
        else:
            print(f"[ERROR] Lỗi: {reason_code}")

    def update_simulation_data(self):
        """Tạo hiệu ứng 'nhảy số' ngẫu nhiên cho dữ liệu"""
        for sid in STATION_DATA:
            # Giúp đường biểu đồ có độ dốc và răng cưa rõ rệt
            STATION_DATA[sid]["level"] += random.uniform(-3.0, 3.0)
            STATION_DATA[sid]["level"] = max(2.0, min(98.0, STATION_DATA[sid]["level"]))

            # 2. Lưu lượng (Flow): Biến thiên cao hơn (+/- 0.2 m³/s)
            # Chỉ số này sẽ thay đổi liên tục, làm thanh Gauge nhảy số liên tục
            STATION_DATA[sid]["flow"] += random.uniform(-0.2, 0.2)
            STATION_DATA[sid]["flow"] = max(0.05, STATION_DATA[sid]["flow"])

            # 3. Lượng mưa (Rain): biến thiên rất nhỏ
            STATION_DATA[sid]["rain"] += random.uniform(-0.05, 0.05)
            STATION_DATA[sid]["rain"] = max(0, STATION_DATA[sid]["rain"])

    def send_station_packet(self, station_id, data):
        payload = json.dumps({
            "station_id": station_id,
            "level": round(data["level"], 2),
            "flow": round(data["flow"], 3),
            "rain": round(data["rain"], 2),
            "timestamp": datetime.now().isoformat(),
        }, ensure_ascii=False)
        
        topic = f"flood/monitor/{station_id}/data"
        self.client.publish(topic, payload, qos=0)
        print(f" [SEND] {station_id.ljust(15)} | Level: {round(data['level'], 2)}cm | Flow: {round(data['flow'], 3)}")

    def run_multi_station_loop(self):
        print("\n" + "="*50)
        print("ĐANG CHẠY MÔ PHỎNG ĐA TRẠM (REAL-TIME)")
        print("="*50)
        
        try:
            while True:
                t0 = time.time()
                
                # Cập nhật số liệu mới trước khi gửi
                self.update_simulation_data()
                
                # Gửi dữ liệu lần lượt cho các trạm
                for s in STATIONS:
                    sid = s["station_id"]
                    self.send_station_packet(sid, STATION_DATA[sid])
                    time.sleep(0.1) # Delay nhỏ tránh nghẽn

                # Chờ cho đủ chu kỳ 5s
                elapsed = time.time() - t0
                time.sleep(max(0, ROUND_INTERVAL_SEC - elapsed))
        except KeyboardInterrupt:
            print("\n[INFO] Đã dừng mô phỏng.")

    def connect(self):
        try:
            self.client.connect(BROKER_ADDRESS, BROKER_PORT, 60)
            self.client.loop_start()
            time.sleep(1)
            return self.is_connected
        except Exception as e:
            print(f"[ERROR] {e}")
            return False

    def run(self):
        if not self.connect(): return
        self.run_multi_station_loop()
        self.client.loop_stop()
        self.client.disconnect()

if __name__ == "__main__":
    ESP32Simulator().run()