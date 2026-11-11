#include "actuators.h"
#include "config.h"
#include "piezo.h"

static Pulse vibPulse;
static Pulse buzzPulse;
static bool alertRunning = false;

// Pin test state. Kept apart from the alert pulses: a test holds an output
// steady, it does not pulse.
static bool testMotorOn = false;
static bool testBuzzOn = false;
static bool testBuzzDc = false;
static uint32_t testMotorEnd = 0;
static uint32_t testBuzzEnd = 0;
// PATTERN test: the alert's on / off pulses for a set time, outside the alert
// state machine (so the Pi fail-safe can't end it).
static bool patternOn = false;
static uint32_t patternEnd = 0;

void setMotor(int duty) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcWrite(MOTOR_PIN, duty);
#else
    ledcWrite(0, duty);
#endif
}

// Differential piezo (piezo.h). 0 means off, which must be piezoOff() --
// never a zero duty, or the inverted leg holds DC across the element.
void setBuzzer(int hz) {
    if (hz > 0) {
        piezoOn((uint32_t)hz);
    } else {
        piezoOff();
    }
}

void initActuators() {
    pinMode(MOTOR_PIN, OUTPUT);
    piezoBegin();   // both piezo legs LOW before anything can raise an alert

#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcAttach(MOTOR_PIN, 20000, 8);  // 20 kHz PWM for motor (own timer)
#else
    ledcSetup(0, 20000, 8);
    ledcAttachPin(MOTOR_PIN, 0);
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

static void endTestMotor() {
    if (!testMotorOn) return;
    testMotorOn = false;
    setMotor(0);
    Serial.println("[TEST] Motor off");
}

static void endTestBuzzer() {
    if (!testBuzzOn) return;
    testBuzzOn = false;
    if (testBuzzDc) {
        piezoDc(false);
        testBuzzDc = false;
    } else {
        setBuzzer(0);
    }
    Serial.println("[TEST] Buzzer off");
}

void startPinTest(uint32_t motorMs, uint32_t buzzerMs, bool buzzerDc) {
    stopAll();
    const uint32_t now = millis();
    if (motorMs > 0) {
        testMotorOn = true;
        testMotorEnd = now + motorMs;
        setMotor(DROWSY_VIB_DUTY);
    }
    if (buzzerMs > 0) {
        testBuzzOn = true;
        testBuzzDc = buzzerDc;
        testBuzzEnd = now + buzzerMs;
        if (buzzerDc) {
            piezoDc(true);
        } else {
            setBuzzer(DROWSY_BUZZ_HZ);
        }
    }
    Serial.printf("[TEST] Motor GPIO %d: %lu ms at duty %d/255 | Buzzer GPIO %d/%d: %lu ms %s\n",
                  MOTOR_PIN, (unsigned long)motorMs, DROWSY_VIB_DUTY,
                  PIEZO_A_PIN, PIEZO_B_PIN, (unsigned long)buzzerMs,
                  buzzerDc ? "DC: 11 HIGH, 18 LOW (silent)" : "5 kHz differential tone");
}

void startPatternTest(uint32_t ms, bool motors) {
    stopAll();
    patternOn = true;
    patternEnd = millis() + ms;
    startPulse(buzzPulse, DROWSY_BUZZ_HZ, DROWSY_BUZZ_ON_MS, DROWSY_BUZZ_OFF_MS, setBuzzer);
    if (motors) startPulse(vibPulse, DROWSY_VIB_DUTY, DROWSY_VIB_ON_MS, DROWSY_VIB_OFF_MS, setMotor);
    Serial.printf("[TEST] Pattern %lu s: buzzer %d ms on / %d ms off%s. STOP ends it\n",
                  (unsigned long)(ms / 1000), DROWSY_BUZZ_ON_MS, DROWSY_BUZZ_OFF_MS,
                  motors ? ", motors 500 / 300 ms" : "");
}

bool isPinTestActive() {
    return testMotorOn || testBuzzOn || patternOn;
}

void updateActuators() {
    const uint32_t now = millis();
    // Signed difference, so the comparison survives millis() wrapping.
    if (testMotorOn && (int32_t)(now - testMotorEnd) >= 0) endTestMotor();
    if (testBuzzOn && (int32_t)(now - testBuzzEnd) >= 0) endTestBuzzer();
    if (patternOn && (int32_t)(now - patternEnd) >= 0) stopAll();
    updatePulse(vibPulse, setMotor);
    updatePulse(buzzPulse, setBuzzer);
    piezoUpdate();
}

void startAlert() {
    endTestMotor();
    endTestBuzzer();
    piezoOff();   // cancels a BEEP or SWEEP so its timer cannot cut the alert tone
    alertRunning = true;
    startPulse(vibPulse, DROWSY_VIB_DUTY, DROWSY_VIB_ON_MS, DROWSY_VIB_OFF_MS, setMotor);
    startPulse(buzzPulse, DROWSY_BUZZ_HZ, DROWSY_BUZZ_ON_MS, DROWSY_BUZZ_OFF_MS, setBuzzer);
}

void stopAll() {
    endTestMotor();
    endTestBuzzer();
    if (patternOn) {
        patternOn = false;
        Serial.println("[TEST] Pattern off");
    }
    alertRunning = false;
    startPulse(vibPulse, 0, 0, 0, setMotor);
    startPulse(buzzPulse, 0, 0, 0, setBuzzer);
}

bool isAlertActive() {
    return alertRunning;
}
