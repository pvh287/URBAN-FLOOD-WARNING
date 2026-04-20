#include <stdio.h>
#include <string.h>
#include <stdbool.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "nvs_flash.h"
#include "esp_log.h"
#include "driver/gpio.h"
#include "esp_netif.h"
#include "esp_event.h"

#include "sensor_handler.h"
#include "mqtt_service.h"
#include "ssd1306.h"

static const char *TAG = "app_main";
static QueueHandle_t s_sensor_queue = NULL;

// TASK 1: Đọc cảm biến liên tục và ghi đè vào Queue
static void sensor_task(void *arg)
{
    sensor_data_t data = {0};
    const TickType_t interval = pdMS_TO_TICKS(1000);

    while (true) {
        sensor_handler_get_data(&data);
        if (s_sensor_queue != NULL) {
            xQueueOverwrite(s_sensor_queue, &data);
        }
        vTaskDelay(interval);
    }
}

// TASK 2: Xử lý mạng MQTT (Chạy ngầm độc lập)
static void mqtt_task(void *arg)
{
    sensor_data_t data = {0};
    const TickType_t wait_time = pdMS_TO_TICKS(5000); // Gửi dữ liệu 5 giây/lần

    // Chờ 10 giây để Module SIM khởi động xong hệ điều hành và sẵn sàng nhận lệnh AT
    ESP_LOGI(TAG, "Dang cho 10 giay de Module SIM khoi dong xong...");
    vTaskDelay(pdMS_TO_TICKS(10000));

    if (mqtt_service_init() != ESP_OK) {
        ESP_LOGE(TAG, "MQTT service init failed");
    }

    if (mqtt_service_start() != ESP_OK) {
        ESP_LOGE(TAG, "MQTT service start failed");
    }

    while (true) {
        // Dùng xQueuePeek để "nhìn" bản copy dữ liệu mới nhất mà không làm mất nó
        if (s_sensor_queue != NULL && xQueuePeek(s_sensor_queue, &data, wait_time) == pdPASS) {
            if (mqtt_service_is_connected()) {
                mqtt_service_publish_sensor_data(&data);
                ESP_LOGI(TAG, "Da gui du lieu len MQTT");
            } else {
                ESP_LOGW(TAG, "MQTT not connected, skipping publish");
            }
        }
        // Trễ 5 giây cho mỗi lần gửi dữ liệu lên mạng
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}

// HÀM MAIN: Dùng làm TASK hiển thị OLED (Luôn luôn mượt mà)
void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    mqtt_service_set_buzzer_pin(BUZZER_GPIO);
    gpio_reset_pin(BUZZER_GPIO);
    gpio_set_direction(BUZZER_GPIO, GPIO_MODE_OUTPUT);
    gpio_set_level(BUZZER_GPIO, 0);

    // 1. Khởi tạo OLED và hiện màn hình chờ
    init_ssd1306();
    ssd1306_clear_screen();
    ssd1306_print_str(0, 0, "HUNG 64DTVT2", false);
    ssd1306_print_str(0, 24, "Dang khoi tao...", false);
    ssd1306_display();

    // 2. Khởi tạo Cảm biến & Queue
    sensor_handler_init();
    s_sensor_queue = xQueueCreate(1, sizeof(sensor_data_t));
    if (s_sensor_queue == NULL) {
        ESP_LOGE(TAG, "Failed to create sensor queue");
        return;
    }

    // 3. Khởi chạy 2 Task chạy ngầm
    xTaskCreate(sensor_task, "sensor_task", 4096, NULL, 5, NULL);
    xTaskCreate(mqtt_task, "mqtt_task", 8192, NULL, 5, NULL);

    // 4. Vòng lặp chính quản lý giao diện (Chạy 1 giây/lần)
    sensor_data_t display_data = {0};
    char buf[64];

    while (true) {
        // Dùng xQueuePeek lấy dữ liệu hiển thị (không ảnh hưởng đến MQTT)
        if (xQueuePeek(s_sensor_queue, &display_data, 0) == pdPASS) {
            
            ssd1306_clear_screen(); 
            
            // Dòng 1: Tiêu đề
            ssd1306_print_str(0, 0, "HUNG 64DTVT2", false);
            
            // Dòng 2: Lưu lượng
            snprintf(buf, sizeof(buf), "L.Luong: %.1f L/m", display_data.flow_lpm);
            ssd1306_print_str(0, 16, buf, false);
            
            // Dòng 3: Mực nước
            snprintf(buf, sizeof(buf), "M.Nuoc: %.1f cm", display_data.level_cm);
            ssd1306_print_str(0, 32, buf, false);
            
            // Dòng 4: Mưa & Trạng thái MQTT
            const char* rain = (display_data.rain_value < 2000) ? "MUA" : "KHONG";
            const char* mqtt_stat = mqtt_service_is_connected() ? "ON" : "OFF";
            snprintf(buf, sizeof(buf), "Mua:%s MQTT:%s", rain, mqtt_stat);
            ssd1306_print_str(0, 48, buf, false);
            
            ssd1306_display();
        }
        
        // Quét lại màn hình mỗi 1 giây
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}