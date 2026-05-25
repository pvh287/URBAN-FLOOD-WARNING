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

# --- Danh sách trạm (Đã thêm lại Thái Hà) ---
STATIONS = [
    {"station_id": "THAI_HA", "name": "Thái Hà", "lat": 21.0120, "lng": 105.8210},
    {"station_id": "PHAM_NGOC_THACH", "name": "Phạm Ngọc Thạch", "lat": 21.0092, "lng": 105.8348},
    {"station_id": "TRUONG_CHINH", "name": "Trường Chinh", "lat": 21.0014, "lng": 105.8261},
]

# --- Dữ liệu khởi tạo ---
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
        """Biến thiên mạnh để Dashboard sinh động"""
        for sid in STATION_DATA:
            # 1. Level: +/- 2.0 cm
            STATION_DATA[sid]["level"] += random.uniform(-2.0, 2.0)
            STATION_DATA[sid]["level"] = max(2.0, min(98.0, STATION_DATA[sid]["level"]))

            # 2. Flow: +/- 0.2 m3/s
            STATION_DATA[sid]["flow"] += random.uniform(-0.2, 0.2)
            STATION_DATA[sid]["flow"] = max(0.05, STATION_DATA[sid]["flow"])

            # 3. Rain: +/- 0.5 mm (Dễ thấy thay đổi hơn)
            STATION_DATA[sid]["rain"] += random.uniform(-0.5, 0.5)
            STATION_DATA[sid]["rain"] = max(0, STATION_DATA[sid]["rain"])

    def send_station_packet(self, station_id, data):
        payload = json.dumps({
            "schema_version": "1.0",
            "station_id": station_id,
            "level_cm": round(data["level"], 2),
            "flow_lpm": round(data["flow"], 3),
            "rain_mm": round(data["rain"], 2),
            "rain_raw": 4095 - int(round(data["rain"], 2) * (4095/20.0)),
            "rain_state": "RAIN" if data["rain"] > 0 else "DRY",
            "timestamp": datetime.now().isoformat(),
            "device_status": "OK"
        }, ensure_ascii=False)
        
        topic = f"flood/monitor/{station_id}/data"
        self.client.publish(topic, payload, qos=0)
        print(f" [SEND] {station_id.ljust(15)} | Level: {round(data['level'], 2):>5}cm | Flow: {round(data['flow'], 3):>6} | Rain: {round(data['rain'], 2):>5}mm")

    def run_multi_station_loop(self):
        print("\n" + "="*75)
        print("ĐANG CHẠY MÔ PHỎNG 3 TRẠM (GỒM THÁI HÀ) - REAL-TIME")
        print("="*75)
        try:
            while True:
                t0 = time.time()
                self.update_simulation_data()
                for s in STATIONS:
                    sid = s["station_id"]
                    self.send_station_packet(sid, STATION_DATA[sid])
                    time.sleep(0.1)
                elapsed = time.time() - t0
                time.sleep(max(0, ROUND_INTERVAL_SEC - elapsed))
        except KeyboardInterrupt:
            print("\n[INFO] Dừng mô phỏng.")

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