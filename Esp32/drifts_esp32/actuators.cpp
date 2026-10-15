#include "actuators.h"
#include "config.h"

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

// Steady HIGH on the buzzer pin means taking it away from LEDC and driving it
// as a plain GPIO; the tone path needs it handed back afterwards.
static void buzzerPinDc(bool high) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    if (high) {
        ledcDetach(BUZZER_PIN);
        pinMode(BUZZER_PIN, OUTPUT);
        digitalWrite(BUZZER_PIN, HIGH);
    } else {
        digitalWrite(BUZZER_PIN, LOW);
        ledcAttach(BUZZER_PIN, 2000, 8);
        setBuzzer(0);
    }
#else
    ledcWrite(1, high ? 255 : 0);
#endif
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
        buzzerPinDc(false);
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
            buzzerPinDc(true);
        } else {
            setBuzzer(DROWSY_BUZZ_HZ);
        }
    }
    Serial.printf("[TEST] Motor GPIO %d: %lu ms at duty %d/255 | Buzzer GPIO %d: %lu ms %s\n",
                  MOTOR_PIN, (unsigned long)motorMs, DROWSY_VIB_DUTY,
                  BUZZER_PIN, (unsigned long)buzzerMs,
                  buzzerDc ? "steady HIGH (silent)" : "tone");
}

bool isPinTestActive() {
    return testMotorOn || testBuzzOn;
}

void updateActuators() {
    const uint32_t now = millis();
    // Signed difference, so the comparison survives millis() wrapping.
    if (testMotorOn && (int32_t)(now - testMotorEnd) >= 0) endTestMotor();
    if (testBuzzOn && (int32_t)(now - testBuzzEnd) >= 0) endTestBuzzer();
    updatePulse(vibPulse, setMotor);
    updatePulse(buzzPulse, setBuzzer);
}

void startAlert() {
    endTestMotor();
    endTestBuzzer();
    alertRunning = true;
    startPulse(vibPulse, DROWSY_VIB_DUTY, DROWSY_VIB_ON_MS, DROWSY_VIB_OFF_MS, setMotor);
    startPulse(buzzPulse, DROWSY_BUZZ_HZ, DROWSY_BUZZ_ON_MS, DROWSY_BUZZ_OFF_MS, setBuzzer);
}

void stopAll() {
    endTestMotor();
    endTestBuzzer();
    alertRunning = false;
    startPulse(vibPulse, 0, 0, 0, setMotor);
    startPulse(buzzPulse, 0, 0, 0, setBuzzer);
}

bool isAlertActive() {
    return alertRunning;
}
