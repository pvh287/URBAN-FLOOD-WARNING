/**
 * =========================================================================
 *  FloodMind-AIoT DSS — MQTT Service (ESP32 Firmware)
 * =========================================================================
 *  - Publishes sensor data (level, flow, rain) in schema v1.0
 *  - Subscribes to buzzer commands (plain-text ON/OFF or JSON)
 *  - Uses SIM7600 modem via PPPoS for cellular connectivity
 * =========================================================================
 */

#include "mqtt_service.h"
#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <esp_log.h>
#include <esp_err.h>
#include <driver/gpio.h>
#include <driver/uart.h>
#include <esp_event.h>
#include <mqtt_client.h>
#include <esp_modem_api.h>
#include <esp_netif.h>
#include <esp_netif_ppp.h>
#include <esp_netif.h>
#include <math.h>
#include <esp_timer.h>

#define STATION_ID "THAI_HA"

static const char *TAG = "mqtt_service";
static const char *APN = "v-internet";
static const char *MQTT_URI = "mqtt://broker.emqx.io";
static const char *TOPIC_PUBLISH_DATA = "flood/monitor/" STATION_ID "/data";
static const char *TOPIC_SUBSCRIBE_BUZZER = "openhab/water/buzzer/cmd";
static const char *TOPIC_PUBLISH_BUZZER_STATUS = "openhab/water/buzzer/status";
static const char *DEVICE_ID = "SmartWaterMonitor_01";

static esp_mqtt_client_handle_t s_mqtt_client = NULL;
static esp_modem_dce_t *s_modem = NULL;
static esp_netif_t *s_ppp_netif = NULL;
static gpio_num_t s_buzzer_pin = BUZZER_GPIO;
static bool s_buzzer_state = false;
static bool s_mqtt_connected = false;

static void set_buzzer(bool enabled)
{
    gpio_set_level(s_buzzer_pin, enabled ? 1 : 0);
    s_buzzer_state = enabled;
}

/**
 * Handle buzzer command — supports both plain-text ("ON"/"OFF")
 * and JSON format ({"command":"ON"}) for backward compatibility
 */
static void handle_buzzer_command(const char *cmd)
{
    if (cmd == NULL) {
        return;
    }

    // --- Try plain text first (primary format) ---
    if (strcasecmp(cmd, "ON") == 0) {
        ESP_LOGI(TAG, "Buzzer ON request received (plain-text)");
        set_buzzer(true);
        if (s_mqtt_client) {
            esp_mqtt_client_publish(s_mqtt_client, TOPIC_PUBLISH_BUZZER_STATUS, "ON", 0, 1, 0);
        }
        return;
    }
    if (strcasecmp(cmd, "OFF") == 0) {
        ESP_LOGI(TAG, "Buzzer OFF request received (plain-text)");
        set_buzzer(false);
        if (s_mqtt_client) {
            esp_mqtt_client_publish(s_mqtt_client, TOPIC_PUBLISH_BUZZER_STATUS, "OFF", 0, 1, 0);
        }
        return;
    }

    // --- Try JSON parse: {"command":"ON"} or {"command":"OFF"} ---
    const char *key = "\"command\"";
    const char *found = strstr(cmd, key);
    if (found) {
        /* Find the value after "command":"..." */
        const char *val_start = strstr(found + strlen(key), "\"");
        if (val_start) {
            val_start++; // skip opening quote
            if (strncasecmp(val_start, "ON", 2) == 0) {
                ESP_LOGI(TAG, "Buzzer ON request received (JSON)");
                set_buzzer(true);
                if (s_mqtt_client) {
                    esp_mqtt_client_publish(s_mqtt_client, TOPIC_PUBLISH_BUZZER_STATUS, "ON", 0, 1, 0);
                }
                return;
            } else if (strncasecmp(val_start, "OFF", 3) == 0) {
                ESP_LOGI(TAG, "Buzzer OFF request received (JSON)");
                set_buzzer(false);
                if (s_mqtt_client) {
                    esp_mqtt_client_publish(s_mqtt_client, TOPIC_PUBLISH_BUZZER_STATUS, "OFF", 0, 1, 0);
                }
                return;
            }
        }
    }

    ESP_LOGW(TAG, "Unknown buzzer command: %s", cmd);
}

static void mqtt_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data)
{
    esp_mqtt_event_handle_t event = event_data;

    switch (event->event_id) {
        case MQTT_EVENT_CONNECTED:
            ESP_LOGI(TAG, "MQTT connected");
            s_mqtt_connected = true;
            esp_mqtt_client_subscribe(s_mqtt_client, TOPIC_SUBSCRIBE_BUZZER, 1);
            esp_mqtt_client_publish(s_mqtt_client, TOPIC_PUBLISH_BUZZER_STATUS, s_buzzer_state ? "ON" : "OFF", 0, 1, 0);
            break;

        case MQTT_EVENT_DISCONNECTED:
            ESP_LOGW(TAG, "MQTT disconnected");
            s_mqtt_connected = false;
            break;

        case MQTT_EVENT_DATA:
            ESP_LOGI(TAG, "MQTT data received on topic %.*s", event->topic_len, event->topic);
            if (strncmp(event->topic, TOPIC_SUBSCRIBE_BUZZER, event->topic_len) == 0) {
                char payload[128] = {0};
                size_t len = event->data_len;
                if (len >= sizeof(payload)) {
                    len = sizeof(payload) - 1;
                }
                memcpy(payload, event->data, len);
                payload[len] = '\0';
                handle_buzzer_command(payload);
            }
            break;

        default:
            break;
    }
}

static esp_err_t modem_pppos_init(void)
{
    ESP_LOGI(TAG, "Initializing Modem and PPP...");

    // 1. Cấu hình DTE (Giao tiếp ESP32 với Module SIM)
    // KHÔNG gọi uart_driver_install thủ công nữa, thư viện sẽ tự lo việc này!
    esp_modem_dte_config_t dte_config = ESP_MODEM_DTE_DEFAULT_CONFIG();
    dte_config.uart_config.port_num = UART_NUM_2; // Bắt buộc phải chỉ định UART2
    dte_config.uart_config.tx_io_num = MODEM_TX_GPIO;
    dte_config.uart_config.rx_io_num = MODEM_RX_GPIO;
    dte_config.uart_config.baud_rate = 115200;

    // 2. Cấu hình DCE (Lệnh AT gửi xuống SIM)
    esp_modem_dce_config_t dce_config = ESP_MODEM_DCE_DEFAULT_CONFIG(APN);

    // 3. Khởi tạo card mạng PPP
    esp_netif_config_t cfg = ESP_NETIF_DEFAULT_PPP();
    s_ppp_netif = esp_netif_new(&cfg);
    if (s_ppp_netif == NULL) {
        ESP_LOGE(TAG, "Cannot create PPP network interface");
        return ESP_FAIL;
    }

    // 4. Tạo thiết bị Modem (Gắn SIM7600 vào UART và PPP)
    s_modem = esp_modem_new_dev(ESP_MODEM_DCE_SIM7600, &dte_config, &dce_config, s_ppp_netif);
    if (s_modem == NULL) {
        ESP_LOGE(TAG, "Failed to create modem device");
        return ESP_FAIL;
    }

    // 5. Ra lệnh cho SIM bắt đầu quay số (Dial-up) lên mạng
    esp_err_t err = esp_modem_set_mode(s_modem, ESP_MODEM_MODE_DATA);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to set modem mode: %s", esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "PPPoS initialized with APN %s. Waiting for IP...", APN);
    return ESP_OK;
}
esp_err_t mqtt_service_init(void)
{
    gpio_config_t buzzer_cfg = {
        .mode = GPIO_MODE_OUTPUT,
        .pin_bit_mask = 1ULL << s_buzzer_pin,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&buzzer_cfg);
    set_buzzer(false);

    esp_err_t err = modem_pppos_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Modem PPPoS init failed");
        return err;
    }

    return ESP_OK;
}

esp_err_t mqtt_service_start(void)
{
    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = MQTT_URI,
    };

    s_mqtt_client = esp_mqtt_client_init(&mqtt_cfg);
    if (s_mqtt_client == NULL) {
        ESP_LOGE(TAG, "Failed to create MQTT client");
        return ESP_FAIL;
    }

    esp_mqtt_client_register_event(s_mqtt_client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
    esp_err_t err = esp_mqtt_client_start(s_mqtt_client);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start MQTT client: %s", esp_err_to_name(err));
        return err;
    }

    return ESP_OK;
}

bool mqtt_service_is_connected(void)
{
    return s_mqtt_connected;
}

/**
 * Publish sensor data with schema_version 1.0
 * Includes: station_id, level_cm, flow_lpm, rain_raw, rain_state, timestamp, device_status
 *
 * Backward-compatible: also includes legacy "level" and "flow" keys
 */
esp_err_t mqtt_service_publish_sensor_data(const sensor_data_t *data)
{
    if (data == NULL || s_mqtt_client == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    /* Compute uptime in seconds as a cheap monotonic timestamp */
    int64_t uptime_us = esp_timer_get_time();
    int64_t uptime_s = uptime_us / 1000000;

    /* Determine rain state: ADC < 2000 => rain detected */
    const char *rain_state = (data->rain_value < 2000) ? "RAIN" : "DRY";

    /*
     * Convert rain ADC to approximate mm value:
     *   rain_raw 0 (fully wet) → heavy rain ~20 mm
     *   rain_raw 4095 (fully dry) → 0 mm
     *   Simple linear approximation: rain_mm = (4095 - rain_raw) / 4095 * 20
     */
    float rain_mm = 0.0f;
    if (data->rain_value < 4095) {
        rain_mm = ((4095.0f - (float)data->rain_value) / 4095.0f) * 20.0f;
    }

    char payload[512];
    if (isnan(data->level_cm)) {
        snprintf(payload, sizeof(payload),
                 "{"
                 "\"schema_version\":\"1.0\","
                 "\"station_id\":\"%s\","
                 "\"level_cm\":null,"
                 "\"level\":null,"
                 "\"flow_lpm\":%.2f,"
                 "\"flow\":%.2f,"
                 "\"rain_raw\":%lu,"
                 "\"rain_mm\":%.2f,"
                 "\"rain\":%.2f,"
                 "\"rain_state\":\"%s\","
                 "\"timestamp\":%lld,"
                 "\"device_status\":\"SENSOR_WARN\""
                 "}",
                 STATION_ID,
                 data->flow_lpm, data->flow_lpm,
                 (unsigned long)data->rain_value,
                 rain_mm, rain_mm,
                 rain_state,
                 (long long)uptime_s);
    } else {
        snprintf(payload, sizeof(payload),
                 "{"
                 "\"schema_version\":\"1.0\","
                 "\"station_id\":\"%s\","
                 "\"level_cm\":%.1f,"
                 "\"level\":%.1f,"
                 "\"flow_lpm\":%.2f,"
                 "\"flow\":%.2f,"
                 "\"rain_raw\":%lu,"
                 "\"rain_mm\":%.2f,"
                 "\"rain\":%.2f,"
                 "\"rain_state\":\"%s\","
                 "\"timestamp\":%lld,"
                 "\"device_status\":\"OK\""
                 "}",
                 STATION_ID,
                 data->level_cm, data->level_cm,
                 data->flow_lpm, data->flow_lpm,
                 (unsigned long)data->rain_value,
                 rain_mm, rain_mm,
                 rain_state,
                 (long long)uptime_s);
    }

    ESP_LOGI(TAG, "Publishing sensor data: %s", payload);
    int msg_id = esp_mqtt_client_publish(s_mqtt_client, TOPIC_PUBLISH_DATA, payload, 0, 1, 0);
    return msg_id >= 0 ? ESP_OK : ESP_FAIL;
}

void mqtt_service_set_buzzer_pin(gpio_num_t pin)
{
    s_buzzer_pin = pin;
}
