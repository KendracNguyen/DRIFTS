#include "obd_reader.h"
#include "config.h"
#include <driver/twai.h>

ObdReader::ObdReader()
    : twaiInstalled(false),
      simulated(false),
      currentRpm(0.0f),
      currentSpeed(0.0f),
      currentThrottle(0.0f),
      currentBrakeDecel(0.0f),
      lastSpeed(0.0f),
      lastSpeedTimeMs(0) {}

bool ObdReader::init() {
    twai_general_config_t g_config = TWAI_GENERAL_CONFIG_DEFAULT(
        (gpio_num_t)CAN_TX_PIN,
        (gpio_num_t)CAN_RX_PIN,
        CAN_LISTEN_ONLY_MODE ? TWAI_MODE_LISTEN_ONLY : TWAI_MODE_NORMAL
    );
    twai_timing_config_t t_config = TWAI_TIMING_CONFIG_500KBITS();
    twai_filter_config_t f_config = TWAI_FILTER_CONFIG_ACCEPT_ALL();

    esp_err_t err = twai_driver_install(&g_config, &t_config, &f_config);
    if (err != ESP_OK) {
        Serial.printf("[OBD] Failed to install TWAI driver: 0x%x\n", err);
        twaiInstalled = false;
        return false;
    }

    err = twai_start();
    if (err != ESP_OK) {
        Serial.printf("[OBD] Failed to start TWAI driver: 0x%x\n", err);
        twaiInstalled = false;
        return false;
    }

    twaiInstalled = true;
    lastSpeedTimeMs = millis();
    Serial.printf("[OBD] TWAI CAN driver started at 500 kbps (listen-only: %s)\n",
                  CAN_LISTEN_ONLY_MODE ? "YES" : "NO");
    return true;
}

void ObdReader::poll() {
    if (!twaiInstalled || simulated) return;

    twai_message_t message;
    while (twai_receive(&message, 0) == ESP_OK) {
        parseCanFrame(message.identifier, message.data, message.data_length_code);
    }
}

void ObdReader::parseCanFrame(uint32_t identifier, const uint8_t *data, uint8_t dlc) {
    if (dlc < 3) return;

    // Standard OBD-II response check:
    // Typically IDs 0x7E8 - 0x7EF or general diagnostic response
    // data[1] == 0x41 (Service 01 response)
    int offset = 0;
    if (data[1] == 0x41) {
        offset = 0;
    } else if (dlc >= 4 && data[2] == 0x41) {
        offset = 1;
    } else {
        return;
    }

    uint8_t pid = data[offset + 2];
    uint32_t now = millis();

    switch (pid) {
        case 0x0C: // Engine RPM: ((A * 256) + B) / 4
            if (dlc >= offset + 5) {
                currentRpm = ((data[offset + 3] * 256.0f) + data[offset + 4]) / 4.0f;
            }
            break;

        case 0x0D: { // Vehicle Speed: A (km/h)
            if (dlc >= offset + 4) {
                float newSpeed = (float)data[offset + 3];
                float dt = (now - lastSpeedTimeMs) / 1000.0f;
                if (dt > 0.05f) {
                    if (lastSpeed > newSpeed) {
                        currentBrakeDecel = (lastSpeed - newSpeed) / dt;
                    } else {
                        currentBrakeDecel = 0.0f;
                    }
                    lastSpeed = newSpeed;
                    lastSpeedTimeMs = now;
                }
                currentSpeed = newSpeed;
            }
            break;
        }

        case 0x11: // Throttle Position: (A * 100) / 255
            if (dlc >= offset + 4) {
                currentThrottle = (data[offset + 3] * 100.0f) / 255.0f;
            }
            break;

        default:
            break;
    }
}

void ObdReader::injectValues(float rpm, float speed, float throttle) {
    simulated = true;
    currentRpm = rpm;
    uint32_t now = millis();
    float dt = (now - lastSpeedTimeMs) / 1000.0f;
    if (dt > 0.05f) {
        if (lastSpeed > speed) {
            currentBrakeDecel = (lastSpeed - speed) / dt;
        } else {
            currentBrakeDecel = 0.0f;
        }
        lastSpeed = speed;
        lastSpeedTimeMs = now;
    }
    currentSpeed = speed;
    currentThrottle = throttle;
}
