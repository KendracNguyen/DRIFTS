#pragma once

#include <Arduino.h>
#include <stdint.h>

enum class PiState {
    IDLE,
    ANALYZING,
    DROWSY,
    NOT_DROWSY
};

class BleHub {
public:
    BleHub();

    bool init();
    void wakePi();
    void setPiIdle();

    bool isPiConnected() const;
    bool isPhoneConnected() const;

    bool hasNewResult();
    String popResult();

    void pushPhoneStatus(float rpm, float speed, float throttle,
                         const char *drvStatus, const char *piStatus,
                         bool alertActive);

    void update();
    uint32_t getLastPiMessageTime() const;

private:
    void setupBleServices();
};
