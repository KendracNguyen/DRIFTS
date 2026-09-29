#pragma once

#include <Arduino.h>
#include <stdint.h>

class ObdReader {
public:
    ObdReader();

    bool init();
    void poll();

    float getRPM() const { return currentRpm; }
    float getSpeed() const { return currentSpeed; }
    float getThrottle() const { return currentThrottle; }
    float getBrakeDecel() const { return currentBrakeDecel; }
    bool isConnected() const { return twaiInstalled; }

    // For manual/test injection over serial or simulation
    void setSimulated(bool enabled) { simulated = enabled; }
    void injectValues(float rpm, float speed, float throttle);

private:
    bool twaiInstalled;
    bool simulated;

    float currentRpm;
    float currentSpeed;
    float currentThrottle;
    float currentBrakeDecel;

    float lastSpeed;
    uint32_t lastSpeedTimeMs;

    void parseCanFrame(uint32_t identifier, const uint8_t *data, uint8_t dlc);
};
