#pragma once

#include <Arduino.h>
#include <stdint.h>

struct Pulse {
    bool active = false;
    int value = 0;          // duty (motor) or frequency (buzzer)
    uint32_t onMs = 0;
    uint32_t offMs = 0;
    bool phaseOn = false;
    uint32_t phaseStart = 0;
};

void initActuators();
void setMotor(int duty);
void setBuzzer(int hz);
void startPulse(Pulse &p, int value, uint32_t onMs, uint32_t offMs, void (*out)(int));
void updatePulse(Pulse &p, void (*out)(int));
void updateActuators();
void startAlert();
void stopAll();
bool isAlertActive();

// Pin test: hold the motor at DROWSY_VIB_DUTY for motorMs and the buzzer on
// for buzzerMs (alert tone, or DC across the piezo when buzzerDc). Cancels any alert
// or earlier test. Ends on its own; stopAll() and startAlert() also end it.
void startPinTest(uint32_t motorMs, uint32_t buzzerMs, bool buzzerDc);
bool isPinTestActive();
