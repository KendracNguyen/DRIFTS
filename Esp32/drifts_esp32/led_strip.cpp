#include "led_strip.h"
#include "config.h"

#include <Arduino.h>

LedStrip Strip;

// WS2812B timing on the RMT peripheral at a 100 ns tick:
//   1 bit: 800 ns high, 400 ns low     0 bit: 400 ns high, 800 ns low
// The RMT clocks the bits out in hardware, with interrupts left on, so BLE
// is not disturbed. 14 LEDs take ~0.4 ms.
static constexpr uint32_t RMT_TICK_HZ = 10000000;
static rmt_data_t symbols[STRIP_LEDS * 24];

static constexpr uint32_t STRIP_MIN_FRAME_MS = 20;      // 50 Hz at most
static constexpr uint32_t STRIP_REFRESH_MS = 1000;      // resend now and then (noise on the data line)
static constexpr uint32_t STRIP_SELF_TEST_STEP_MS = 1500;

struct SelfTestStep {
    const char *label;
    float score;
    bool partial, alarm, linksOk, systemIssue;
};
static const SelfTestStep SELF_TEST[] = {
    {"score 100 green, full bar", 100, false, false, true, false},
    {"score 85 green", 85, false, false, true, false},
    {"score 70 green, 3/4 bar", 70, false, false, true, false},
    {"score 69 yellow", 69, false, false, true, false},
    {"score 40 yellow, 1/2 bar", 40, false, false, true, false},
    {"score 39 red", 39, false, false, true, false},
    {"score 10 red, 1/4 bar", 10, false, false, true, false},
    {"score 5 critical: whole bar fast red", 5, false, false, true, false},
    {"score 85 partial: half brightness", 85, true, false, true, false},
    {"alarm: whole bar red 1 s / 1 s", 30, false, true, true, false},
    {"status: connection failure (red slow)", 85, false, false, false, false},
    {"status: system issue (yellow slow)", 85, false, false, true, true},
};
static constexpr int SELF_TEST_STEPS = sizeof(SELF_TEST) / sizeof(SELF_TEST[0]);

void LedStrip::begin() {
    if (!STRIP_ENABLED) {
        Serial.println("[STRIP] Disabled (STRIP_ENABLED = false)");
        return;
    }
    _ok = rmtInit(STRIP_PIN, RMT_TX_MODE, RMT_MEM_NUM_BLOCKS_1, RMT_TICK_HZ);
    if (!_ok) {
        Serial.printf("[STRIP] RMT init on GPIO %d failed: strip off\n", STRIP_PIN);
        return;
    }
    rmtSetEOT(STRIP_PIN, LOW);
    _lampUntil = millis() + LED_LAMP_TEST_MS;
    Serial.printf("[STRIP] WS2812B x%d on GPIO %d, brightness %d/255\n", STRIP_LEDS, STRIP_PIN,
                  STRIP_BRIGHTNESS);
}

void LedStrip::startSelfTest(uint32_t nowMs) {
    if (!_ok) {
        Serial.println("[STRIP] Not running (disabled or RMT init failed)");
        return;
    }
    _selfTest = true;
    _selfTestStart = nowMs;
    Serial.printf("[STRIP] Self test: %d steps, %lu ms each\n", SELF_TEST_STEPS,
                  (unsigned long)STRIP_SELF_TEST_STEP_MS);
}

void LedStrip::startRainbow(uint32_t nowMs, int cycles) {
    if (!_ok) {
        Serial.println("[STRIP] Not running (disabled or RMT init failed)");
        return;
    }
    _selfTest = false;
    _rainbow = true;
    _rainbowStart = nowMs;
    _rainbowCycles = cycles;
    Serial.printf("[STRIP] Rainbow: red, orange, yellow, green, blue, indigo, violet x%d (%lu ms each)\n", cycles,
                  (unsigned long)STRIP_RAINBOW_STEP_MS);
}

void LedStrip::show(const Rgb *frame, uint32_t nowMs) {
    bool same = _sent;
    for (int i = 0; same && i < STRIP_LEDS; i++) same = frame[i] == _last[i];
    if (same && nowMs - _lastSendMs < STRIP_REFRESH_MS) return;
    if (!same && _sent && nowMs - _lastSendMs < STRIP_MIN_FRAME_MS) return;

    size_t k = 0;
    for (int i = 0; i < STRIP_LEDS; i++) {
        _last[i] = frame[i];
        const uint8_t grb[3] = {
            (uint8_t)((frame[i].g * STRIP_BRIGHTNESS) / 255),
            (uint8_t)((frame[i].r * STRIP_BRIGHTNESS) / 255),
            (uint8_t)((frame[i].b * STRIP_BRIGHTNESS) / 255),
        };
        for (int c = 0; c < 3; c++) {
            for (int bit = 7; bit >= 0; bit--) {
                const bool one = (grb[c] >> bit) & 1;
                symbols[k].level0 = 1;
                symbols[k].duration0 = one ? 8 : 4;
                symbols[k].level1 = 0;
                symbols[k].duration1 = one ? 4 : 8;
                k++;
            }
        }
    }
    rmtWrite(STRIP_PIN, symbols, k, 10);
    _sent = true;
    _lastSendMs = nowMs;
}

void LedStrip::update(const StripInputs &in, uint32_t nowMs) {
    if (!_ok) return;
    Rgb frame[STRIP_LEDS];

    if ((int32_t)(nowMs - _lampUntil) < 0) {
        // Lamp test: white sweep left to right over LED_LAMP_TEST_MS.
        const uint32_t elapsed = LED_LAMP_TEST_MS - (_lampUntil - nowMs);
        const int upTo = (int)((elapsed * STRIP_LEDS) / LED_LAMP_TEST_MS);
        for (int i = 0; i < STRIP_LEDS; i++) frame[i] = i <= upTo ? STRIP_WHITE : STRIP_OFF;
        show(frame, nowMs);
        return;
    }

    if (_rainbow) {
        if (rainbowFrame(nowMs - _rainbowStart, _rainbowCycles, frame)) {
            _lit = STRIP_BAR_LEN;
            show(frame, nowMs);
            return;
        }
        _rainbow = false;
        Serial.println("[STRIP] Rainbow done");
    }

    StripInputs use = in;
    if (_selfTest) {
        const uint32_t step = (nowMs - _selfTestStart) / STRIP_SELF_TEST_STEP_MS;
        static int announced = -1;
        if (step >= (uint32_t)SELF_TEST_STEPS) {
            _selfTest = false;
            announced = -1;
            Serial.println("[STRIP] Self test done");
        } else {
            const SelfTestStep &s = SELF_TEST[step];
            if ((int)step != announced) {
                announced = (int)step;
                Serial.printf("[STRIP] %d/%d %s\n", (int)step + 1, SELF_TEST_STEPS, s.label);
            }
            use.score = s.score;
            use.partial = s.partial;
            use.alarm = s.alarm;
            use.alarmStartMs = _selfTestStart + step * STRIP_SELF_TEST_STEP_MS;
            use.linksOk = s.linksOk;
            use.systemIssue = s.systemIssue;
        }
    }
    renderStrip(use, nowMs, frame);
    _lit = (use.alarm || use.score < STRIP_RED_MIN) ? STRIP_BAR_LEN : stripBarLit(use.score);
    show(frame, nowMs);
}
