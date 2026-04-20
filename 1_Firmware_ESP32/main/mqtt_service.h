#ifndef MQTT_SERVICE_H
#define MQTT_SERVICE_H

#include <stdbool.h>
#include "sensor_handler.h"
#include "driver/gpio.h"

#define MODEM_RX_GPIO GPIO_NUM_16
#define MODEM_TX_GPIO GPIO_NUM_17
#define BUZZER_GPIO   GPIO_NUM_23

esp_err_t mqtt_service_init(void);
esp_err_t mqtt_service_start(void);
bool mqtt_service_is_connected(void);
esp_err_t mqtt_service_publish_sensor_data(const sensor_data_t *data);
void mqtt_service_set_buzzer_pin(gpio_num_t pin);

#endif // MQTT_SERVICE_H
