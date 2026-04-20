"""
Database Manager - MQTT Data Collection with Anomaly Detection
Listens to IoT sensor data, applies anomaly detection filters,
and stores validated data to CSV file
"""

import logging
import json
import pandas as pd
import paho.mqtt.client as mqtt
from datetime import datetime
from config import (
    MQTT_BROKER, MQTT_PORT, MQTT_KEEPALIVE,
    TOPIC_DATA, SENSOR_HISTORY_FILE, MAX_DELTA_LEVEL
)

# Setup logging
logger = logging.getLogger(__name__)

class DatabaseManager:
    """Manages MQTT data collection and anomaly detection"""
    
    def __init__(self):
        """Initialize MQTT client and data storage"""
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        
        # Store previous water level for delta calculation
        self.previous_level = None
        self.previous_timestamp = None
        
        # Initialize CSV file with headers if not exists
        self._init_csv_file()
        
        logger.info("DatabaseManager initialized successfully")
    
    def _init_csv_file(self):
        """Create CSV file with headers if it doesn't exist"""
        try:
            # Check if file exists
            try:
                pd.read_csv(SENSOR_HISTORY_FILE)
                logger.info(f"CSV file exists: {SENSOR_HISTORY_FILE}")
            except FileNotFoundError:
                # Create new CSV with headers
                df = pd.DataFrame(columns=[
                    'timestamp', 'level', 'flow', 'delta_level', 'rain_local'
                ])
                df.to_csv(SENSOR_HISTORY_FILE, index=False)
                logger.info(f"CSV file created: {SENSOR_HISTORY_FILE}")
        except Exception as e:
            logger.error(f"Error initializing CSV file: {e}")
            raise
    
    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connection callback"""
        if rc == 0:
            logger.info(f"Connected to MQTT broker ({MQTT_BROKER}:{MQTT_PORT})")
            client.subscribe(TOPIC_DATA)
            logger.info(f"Subscribed to topic: {TOPIC_DATA}")
        else:
            logger.error(f"Failed to connect to MQTT broker. RC: {rc}")
    
    def _on_message(self, client, userdata, msg):
        """MQTT message callback - process incoming sensor data"""
        try:
            # Decode and parse JSON message
            payload = msg.payload.decode('utf-8')
            data = json.loads(payload)
            
            logger.debug(f"Raw message received: {data}")
            
            # Extract required fields safely with defaults to prevent NoneType errors
            level = float(data.get('level', 0.0))
            flow = float(data.get('flow', 0.0))
            rain_local = float(data.get('rain', 0.0))  # Key chính xác từ ESP32 là 'rain'
            
            # Process data through anomaly detection
            self._process_sensor_data(level, flow, rain_local)
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}. Raw payload: {msg.payload}")
        except KeyError as e:
            logger.error(f"Missing required field in JSON: {e}. Payload: {payload}")
        except ValueError as e:
            logger.error(f"Value conversion error: {e}. Payload: {payload}")
        except Exception as e:
            logger.error(f"Unexpected error in message processing: {e}")
    
    def _detect_anomaly(self, level, rain_local):
        """
        Anomaly Detection Filter
        
        Rule: IF delta_level > 20 cm AND rain_local == 0
              => Anomaly (potential clog or sensor malfunction)
        
        Returns:
            tuple: (is_anomaly: bool, delta_level: float, error_message: str)
        """
        try:
            # Calculate delta_level (change from previous level)
            if self.previous_level is None:
                delta_level = 0
                logger.info("First measurement, no delta calculation")
                return False, delta_level, None
            
            delta_level = level - self.previous_level
            
            # Check for anomaly
            if delta_level > MAX_DELTA_LEVEL and rain_local == 0:
                error_msg = (
                    f"ANOMALY DETECTED: "
                    f"Level jump {delta_level:.1f} cm without rainfall. "
                    f"Possible causes: Pipe clog, sensor malfunction, or debris buildup"
                )
                logger.warning(error_msg)
                return True, delta_level, error_msg
            
            if delta_level < -MAX_DELTA_LEVEL:
                error_msg = (
                    f"WARNING: Sudden level drop {delta_level:.1f} cm. "
                    f"Check for drainage overflow or sensor issues"
                )
                logger.warning(error_msg)
            
            return False, delta_level, None
            
        except Exception as e:
            logger.error(f"Error in anomaly detection: {e}")
            return False, 0, str(e)
    
    def _process_sensor_data(self, level, flow, rain_local):
        """
        Process sensor data with anomaly detection
        
        Args:
            level (float): Water level in cm
            flow (float): Flow rate in m³/s
            rain_local (int): Local rain status (0=no rain, 1=rain)
        """
        try:
            # Validate data ranges
            if not (0 <= level <= 200):
                logger.error(f"Invalid level value: {level} cm (must be 0-200)")
                return
            
            if not (0 <= flow <= 100):
                logger.error(f"Invalid flow value: {flow} m³/s (must be 0-100)")
                return
            
            # Perform anomaly detection
            is_anomaly, delta_level, error_msg = self._detect_anomaly(level, rain_local)
            
            if is_anomaly:
                logger.warning(f"Data rejected due to anomaly: {error_msg}")
                # Update previous level anyway for next check
                self.previous_level = level
                return
            
            # Data is valid, save to CSV
            self._save_to_csv(level, flow, delta_level, rain_local)
            
            # Update previous level for next iteration
            self.previous_level = level
            self.previous_timestamp = datetime.now()
            
        except Exception as e:
            logger.error(f"Error processing sensor data: {e}")
    
    def _save_to_csv(self, level, flow, delta_level, rain_local):
        """
        Save validated data to CSV file (append mode)
        
        Args:
            level (float): Water level in cm
            flow (float): Flow rate in m³/s
            delta_level (float): Change from previous level
            rain_local (int): Local rain status
        """
        try:
            # Create new row
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            new_row = {
                'timestamp': timestamp,
                'level': round(level, 2),
                'flow': round(flow, 3),
                'delta_level': round(delta_level, 2),
                'rain_local': rain_local
            }
            
            # Append to CSV (create new DataFrame and concatenate)
            df_new = pd.DataFrame([new_row])
            df_existing = pd.read_csv(SENSOR_HISTORY_FILE)
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            df_combined.to_csv(SENSOR_HISTORY_FILE, index=False)
            
            logger.info(
                f"Data saved: Level={level:.1f}cm, Flow={flow:.3f}m³/s, "
                f"Rain={rain_local}, ΔLevel={delta_level:.2f}cm"
            )
            
        except Exception as e:
            logger.error(f"Error saving data to CSV: {e}")
    
    def connect(self):
        """Connect to MQTT broker and start listening"""
        try:
            logger.info(f"Attempting to connect to {MQTT_BROKER}:{MQTT_PORT}...")
            self.client.connect(MQTT_BROKER, MQTT_PORT, MQTT_KEEPALIVE)
            self.client.loop_start()
            logger.info("MQTT client started successfully")
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            raise
    
    def disconnect(self):
        """Disconnect from MQTT broker"""
        try:
            self.client.loop_stop()
            self.client.disconnect()
            logger.info("Disconnected from MQTT broker")
        except Exception as e:
            logger.error(f"Error disconnecting from MQTT: {e}")
    
    def get_latest_data(self, rows=10):
        """
        Retrieve latest data entries from CSV
        
        Args:
            rows (int): Number of latest rows to retrieve
            
        Returns:
            pd.DataFrame: Latest sensor data
        """
        try:
            df = pd.read_csv(SENSOR_HISTORY_FILE)
            return df.tail(rows)
        except Exception as e:
            logger.error(f"Error retrieving latest data: {e}")
            return None
    
    def get_statistics(self):
        """Calculate statistics from stored data"""
        try:
            df = pd.read_csv(SENSOR_HISTORY_FILE)
            if df.empty:
                logger.warning("No data available for statistics")
                return None
            
            stats = {
                'total_records': len(df),
                'avg_level': df['level'].mean(),
                'max_level': df['level'].max(),
                'min_level': df['level'].min(),
                'avg_flow': df['flow'].mean(),
                'rain_occurrences': df['rain_local'].sum()
            }
            
            logger.info(f"Statistics: {stats}")
            return stats
            
        except Exception as e:
            logger.error(f"Error calculating statistics: {e}")
            return None


def main():
    """Test the DatabaseManager"""
    logger.info("Starting DatabaseManager test...")
    
    try:
        manager = DatabaseManager()
        manager.connect()
        
        # Keep running until interrupted
        logger.info("DatabaseManager running. Press Ctrl+C to stop...")
        import time
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        manager.disconnect()
    except Exception as e:
        logger.error(f"Fatal error: {e}")


if __name__ == "__main__":
    main()
