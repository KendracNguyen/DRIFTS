#include "actuators.h"
#include "config.h"

static Pulse vibPulse;
static Pulse buzzPulse;
static bool alertRunning = false;

void setMotor(int duty) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcWrite(MOTOR_PIN, duty);
#else
    ledcWrite(0, duty);
#endif
}

void setBuzzer(int hz) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcWriteTone(BUZZER_PIN, hz);
#else
    if (hz <= 0) {
        ledcWrite(1, 0);
    } else {
        ledcWriteTone(1, hz);
    }
#endif
}

void initActuators() {
    pinMode(MOTOR_PIN, OUTPUT);
    pinMode(BUZZER_PIN, OUTPUT);

#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcAttach(MOTOR_PIN, 20000, 8);  // 20 kHz PWM for motor
    ledcAttach(BUZZER_PIN, 2000, 8);   // Tone for buzzer
#else
    ledcSetup(0, 20000, 8);
    ledcAttachPin(MOTOR_PIN, 0);
    ledcSetup(1, 2000, 8);
    ledcAttachPin(BUZZER_PIN, 1);
#endif

    stopAll();
}

void startPulse(Pulse &p, int value, uint32_t onMs, uint32_t offMs, void (*out)(int)) {
    if (value <= 0) {
        p.active = false;
        out(0);
        return;
    }
    p.active = true;
    p.value = value;
    p.onMs = onMs;
    p.offMs = offMs;
    p.phaseOn = true;
    p.phaseStart = millis();
    out(value);
}

void updatePulse(Pulse &p, void (*out)(int)) {
    if (!p.active) return;
    if (p.offMs == 0) return; // Continuous mode

    uint32_t now = millis();
    uint32_t len = p.phaseOn ? p.onMs : p.offMs;
    if (now - p.phaseStart >= len) {
        p.phaseOn = !p.phaseOn;
        p.phaseStart = now;
        out(p.phaseOn ? p.value : 0);
    }
}

void updateActuators() {
    updatePulse(vibPulse, setMotor);
    updatePulse(buzzPulse, setBuzzer);
}

void startAlert() {
    alertRunning = true;
    startPulse(vibPulse, DROWSY_VIB_DUTY, DROWSY_VIB_ON_MS, DROWSY_VIB_OFF_MS, setMotor);
    startPulse(buzzPulse, DROWSY_BUZZ_HZ, DROWSY_BUZZ_ON_MS, DROWSY_BUZZ_OFF_MS, setBuzzer);
}

void stopAll() {
    alertRunning = false;
    startPulse(vibPulse, 0, 0, 0, setMotor);
    startPulse(buzzPulse, 0, 0, 0, setBuzzer);
}

bool isAlertActive() {
    return alertRunning;
}
