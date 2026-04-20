#ifndef SENSOR_HANDLER_H
#define SENSOR_HANDLER_H

#include <stdint.h>
#include <stdbool.h>
#include <math.h>
#include "driver/adc.h"

#define PIN_TRIG 2
#define PIN_ECHO 5
#define PIN_FLOW 4
#define PIN_RAIN_ADC_CHANNEL ADC1_CHANNEL_6
#define PIN_RAIN_ADC_ATTEN ADC_ATTEN_DB_11

#define SENSOR_HEIGHT_CM    200.0f
#define FLOW_WINDOW_MS      1000
#define FLOW_HZ_PER_LPM     7.5f
#define ULTRASONIC_SAMPLES  5

typedef struct {
    uint32_t rain_value;
    float flow_lpm;
    float distance_cm;
    float level_cm;
} sensor_data_t;

void sensor_handler_init(void);
void sensor_handler_get_data(sensor_data_t *out_data);
void sensor_handler_reset_flow_counter(void);

#endif // SENSOR_HANDLER_H
