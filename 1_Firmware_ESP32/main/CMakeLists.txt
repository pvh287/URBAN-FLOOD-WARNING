idf_component_register(SRCS "main.c" 
                            "mqtt_service.c" 
                            "sensor_handler.c"
                            "ssd1306.c"
                    INCLUDE_DIRS "."
                    REQUIRES driver nvs_flash mqtt esp_modem esp_netif)