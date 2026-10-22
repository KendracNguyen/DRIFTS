#include "status_leds.h"
#include "config.h"

#include <Arduino.h>

StatusLeds Leds;

static constexpr int LED_PWM_HZ = 5000;
static constexpr int LED_PWM_BITS = 8;
// Self test: each LED shows solid, slow, fast for this long, one after another.
static constexpr uint32_t SELF_TEST_STEP_MS = 2000;

void StatusLeds::begin() {
    const int pins[] = {LED_GREEN_PIN, LED_YELLOW_PIN, LED_RED_PIN};
    for (int pin : pins) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
        ledcAttach(pin, LED_PWM_HZ, LED_PWM_BITS);
#else
        pinMode(pin, OUTPUT);
#endif
        write(pin, true);      // lamp test: all on at power-up
    }
    _lampTestUntil = millis() + LED_LAMP_TEST_MS;
}

void StatusLeds::write(int pin, bool on) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcWrite(pin, on ? LED_BRIGHTNESS : 0);
#else
    digitalWrite(pin, on ? HIGH : LOW);
#endif
}

void StatusLeds::startSelfTest(uint32_t nowMs) {
    _selfTest = true;
    _selfTestStart = nowMs;
    Serial.println("[LED] Self test: GREEN, then YELLOW, then RED; each solid -> slow -> fast");
}

void StatusLeds::update(const LedInputs &in, uint32_t nowMs) {
    if ((int32_t)(nowMs - _lampTestUntil) < 0) return;   // lamp test still showing

    if (_selfTest) {
        const uint32_t step = (nowMs - _selfTestStart) / SELF_TEST_STEP_MS;   // 0..8
        if (step >= 9) {
            _selfTest = false;
            Serial.println("[LED] Self test done");
        } else {
            static const LedPattern seq[3] = {LedPattern::Solid, LedPattern::SlowBlink, LedPattern::FastBlink};
            LedState t;
            LedPattern *slot[3] = {&t.green, &t.yellow, &t.red};
            *slot[step / 3] = seq[step % 3];
            _state = t;
        }
    }
    if (!_selfTest) _state = decideLeds(in);

    write(LED_GREEN_PIN, ledPatternOn(_state.green, nowMs));
    write(LED_YELLOW_PIN, ledPatternOn(_state.yellow, nowMs));
    write(LED_RED_PIN, ledPatternOn(_state.red, nowMs));
}
