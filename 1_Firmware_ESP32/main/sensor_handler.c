#include "sensor_handler.h"
#include <esp_timer.h>
#include <esp_log.h>
#include <driver/gpio.h>
#include <esp_intr_alloc.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <math.h>

static const char *TAG = "sensor_handler";
static volatile uint32_t s_flow_pulses = 0;
static portMUX_TYPE s_flow_mux = portMUX_INITIALIZER_UNLOCKED;
static float s_ultrasonic_buffer[ULTRASONIC_SAMPLES];
static size_t s_ultrasonic_index = 0;
static size_t s_ultrasonic_count = 0;
static float s_last_flow_lpm = 0.0f;

static void IRAM_ATTR flow_isr_handler(void *arg)
{
    s_flow_pulses++;
}

static void configure_adc(void)
{
    adc1_config_width(ADC_WIDTH_BIT_12);
    adc1_config_channel_atten(PIN_RAIN_ADC_CHANNEL, PIN_RAIN_ADC_ATTEN);
}

static void configure_gpio(void)
{
    gpio_config_t io_conf = {
        .intr_type = GPIO_INTR_DISABLE,
        .mode = GPIO_MODE_OUTPUT,
        .pin_bit_mask = 1ULL << PIN_TRIG,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .pull_up_en = GPIO_PULLUP_DISABLE,
    };
    gpio_config(&io_conf);

    io_conf.intr_type = GPIO_INTR_DISABLE;
    io_conf.mode = GPIO_MODE_INPUT;
    io_conf.pin_bit_mask = 1ULL << PIN_ECHO;
    io_conf.pull_up_en = GPIO_PULLUP_DISABLE;
    io_conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
    gpio_config(&io_conf);

    io_conf.intr_type = GPIO_INTR_POSEDGE;
    io_conf.mode = GPIO_MODE_INPUT;
    io_conf.pin_bit_mask = 1ULL << PIN_FLOW;
    io_conf.pull_up_en = GPIO_PULLUP_ENABLE;
    io_conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
    gpio_config(&io_conf);

    gpio_set_level(PIN_TRIG, 0);
    gpio_install_isr_service(0);
    gpio_isr_handler_add(PIN_FLOW, flow_isr_handler, NULL);
}

static float read_distance_cm(void)
{
    gpio_set_level(PIN_TRIG, 0);
    esp_rom_delay_us(3);
    gpio_set_level(PIN_TRIG, 1);
    esp_rom_delay_us(3);
    gpio_set_level(PIN_TRIG, 0);

    int64_t start = esp_timer_get_time();
    while (gpio_get_level(PIN_ECHO) == 0) {
        if ((esp_timer_get_time() - start) > 30000) {
            ESP_LOGW(TAG, "Ultrasonic trigger timeout waiting for echo high");
            return NAN;
        }
        esp_rom_delay_us(1);
    }

    int64_t pulse_start = esp_timer_get_time();
    while (gpio_get_level(PIN_ECHO) == 1) {
        if ((esp_timer_get_time() - pulse_start) > 30000) {
            ESP_LOGW(TAG, "Ultrasonic echo timeout waiting for echo low");
            return NAN;
        }
        esp_rom_delay_us(1);
    }

    int64_t pulse_end = esp_timer_get_time();
    float duration_us = (float)(pulse_end - pulse_start);
    return (duration_us * 0.0343f) / 2.0f;
}

static float compute_moving_average_distance(void)
{
    float distance = read_distance_cm();
    if (!isnan(distance) && distance > 0.5f && distance < 600.0f) {
        s_ultrasonic_buffer[s_ultrasonic_index] = distance;
        s_ultrasonic_index = (s_ultrasonic_index + 1) % ULTRASONIC_SAMPLES;
        if (s_ultrasonic_count < ULTRASONIC_SAMPLES) {
            s_ultrasonic_count++;
        }
    }

    if (s_ultrasonic_count == 0) {
        return NAN;
    }

    float sum = 0.0f;
    for (size_t i = 0; i < s_ultrasonic_count; ++i) {
        sum += s_ultrasonic_buffer[i];
    }
    return sum / (float)s_ultrasonic_count;
}

static void calculate_flow(void)
{
    portENTER_CRITICAL(&s_flow_mux);
    uint32_t pulses = s_flow_pulses;
    s_flow_pulses = 0;
    portEXIT_CRITICAL(&s_flow_mux);

    float frequency_hz = (float)pulses * (1000.0f / (float)FLOW_WINDOW_MS);
    s_last_flow_lpm = frequency_hz / FLOW_HZ_PER_LPM;
}

void sensor_handler_init(void)
{
    configure_adc();
    configure_gpio();
    memset(s_ultrasonic_buffer, 0, sizeof(s_ultrasonic_buffer));
    s_ultrasonic_index = 0;
    s_ultrasonic_count = 0;
    s_last_flow_lpm = 0.0f;
}

void sensor_handler_get_data(sensor_data_t *out_data)
{
    if (out_data == NULL) {
        return;
    }

    calculate_flow();
    out_data->rain_value = adc1_get_raw(PIN_RAIN_ADC_CHANNEL);
    out_data->flow_lpm = s_last_flow_lpm;
    out_data->distance_cm = compute_moving_average_distance();
    out_data->level_cm = isnan(out_data->distance_cm) ? NAN : fmaxf(0.0f, SENSOR_HEIGHT_CM - out_data->distance_cm);
}

void sensor_handler_reset_flow_counter(void)
{
    portENTER_CRITICAL(&s_flow_mux);
    s_flow_pulses = 0;
    portEXIT_CRITICAL(&s_flow_mux);
}
