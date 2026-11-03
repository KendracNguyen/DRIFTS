#pragma once

// WS2812B strip (14 LEDs) above the LCD: status pixel + alert-score bar.
//
//   pixel 0      status   green solid  = OBD dongle and Pi connected
//                         red slow     = connection failure (dongle or Pi lost)
//                         yellow slow  = system issue (bench mode, IMU missing)
//   pixels 1-13  bar      length and colour follow the alert score:
//
//     score 70-100  green   75 % -> 100 % of the bar
//     score 40-69   yellow  50 % ->  74 %
//     score 10-39   red     25 % ->  49 %
//     score  0-9    red     whole bar, fast blink (4 Hz)
//
//   The drowsy alarm overrides the bar: whole bar red, 1 s on / 1 s off, in
//   step with the buzzer. A partial score (one input missing) is drawn at
//   half brightness.
//
// The colour bands are the STRIP's own (set by the team, Nov 2026). They are
// not the alert levels: the buzzer and LCD still use 70 / 50 (alertness.h).
// Change STRIP_YELLOW_MIN to 50 to make the strip match them.
//
// Tokyo (Pi 5) draws the same frames: Tokyo/tokyo/ledstrip.py. Both are
// tested against the same table (Esp32/test/test_strip.cpp,
// Tokyo/tests/test_tokyo.py).
//
// Pure (no Arduino) so the host tests cover it; only LedStrip touches the pin.

#include <math.h>
#include <stdint.h>

struct Rgb {
    uint8_t r, g, b;
    bool operator==(const Rgb &o) const { return r == o.r && g == o.g && b == o.b; }
    bool operator!=(const Rgb &o) const { return !(*this == o); }
};

constexpr Rgb STRIP_OFF    = {0, 0, 0};
constexpr Rgb STRIP_GREEN  = {0, 255, 40};
constexpr Rgb STRIP_YELLOW = {255, 150, 0};
constexpr Rgb STRIP_RED    = {255, 0, 0};
constexpr Rgb STRIP_WHITE  = {255, 255, 255};

constexpr int STRIP_LEDS       = 14;
constexpr int STRIP_STATUS_LED = 0;
constexpr int STRIP_BAR_START  = 1;
constexpr int STRIP_BAR_LEN    = STRIP_LEDS - STRIP_BAR_START;   // 13

constexpr float STRIP_GREEN_MIN  = 70.0f;
constexpr float STRIP_YELLOW_MIN = 40.0f;
constexpr float STRIP_RED_MIN    = 10.0f;   // below this: critical, whole bar blinks

struct StripInputs {
    float score = 100.0f;        // alert score 0-100 (100 = fully alert)
    bool partial = false;        // eye or driving data missing
    bool alarm = false;          // drowsy alert running (buzzer + motors)
    uint32_t alarmStartMs = 0;   // phase reference, same as the buzzer's
    bool linksOk = true;         // dongle polling and Pi connected
    bool systemIssue = false;    // bench mode or IMU missing
};

inline Rgb stripBandColor(float score) {
    if (score >= STRIP_GREEN_MIN) return STRIP_GREEN;
    if (score >= STRIP_YELLOW_MIN) return STRIP_YELLOW;
    return STRIP_RED;
}

// Fraction of the bar for a score: each band owns a quarter of the strip.
inline float stripBarFraction(float score) {
    if (score > 100.0f) score = 100.0f;
    if (score < 0.0f) score = 0.0f;
    float lo, top;
    if (score >= STRIP_GREEN_MIN) {
        lo = STRIP_GREEN_MIN; top = 100.0f;
        return 0.75f + 0.25f * (score - lo) / (top - lo);
    }
    if (score >= STRIP_YELLOW_MIN) {
        lo = STRIP_YELLOW_MIN; top = STRIP_GREEN_MIN;
        return 0.50f + 0.25f * (score - lo) / (top - lo);
    }
    if (score >= STRIP_RED_MIN) {
        lo = STRIP_RED_MIN; top = STRIP_YELLOW_MIN;
        return 0.25f + 0.25f * (score - lo) / (top - lo);
    }
    return 1.0f;   // critical: whole bar
}

inline int stripBarLit(float score, int barLen = STRIP_BAR_LEN) {
    int lit = (int)floorf(stripBarFraction(score) * (float)barLen + 0.5f);
    if (lit < 1) lit = 1;
    if (lit > barLen) lit = barLen;
    return lit;
}

// RAINBOW command: the whole strip flashes red, orange, yellow, green, blue,
// indigo, violet, one colour per step (on, then a short off so each is a
// distinct flash). A wiring and colour-order check: if red shows green, the
// strip is RGB rather than GRB.
constexpr Rgb STRIP_RAINBOW[7] = {
    {255, 0, 0}, {255, 100, 0}, {255, 220, 0}, {0, 255, 0}, {0, 60, 255}, {75, 0, 200}, {170, 0, 255}};
constexpr uint32_t STRIP_RAINBOW_STEP_MS = 500;
constexpr uint32_t STRIP_RAINBOW_ON_MS = 380;
constexpr int STRIP_RAINBOW_CYCLES = 3;

// The frame `elapsedMs` into a rainbow run; returns false once it is over.
inline bool rainbowFrame(uint32_t elapsedMs, int cycles, Rgb out[STRIP_LEDS]) {
    const uint32_t step = elapsedMs / STRIP_RAINBOW_STEP_MS;
    if (step >= (uint32_t)(7 * cycles)) return false;
    const bool on = (elapsedMs % STRIP_RAINBOW_STEP_MS) < STRIP_RAINBOW_ON_MS;
    const Rgb c = on ? STRIP_RAINBOW[step % 7] : STRIP_OFF;
    for (int i = 0; i < STRIP_LEDS; i++) out[i] = c;
    return true;
}

inline bool stripSlowBlink(uint32_t nowMs) { return (nowMs % 1000) < 500; }
inline bool stripFastBlink(uint32_t nowMs) { return (nowMs % 250) < 125; }

inline Rgb stripHalf(Rgb c) { return Rgb{(uint8_t)(c.r / 2), (uint8_t)(c.g / 2), (uint8_t)(c.b / 2)}; }

// One frame, full brightness (the driver applies STRIP_BRIGHTNESS).
inline void renderStrip(const StripInputs &in, uint32_t nowMs, Rgb out[STRIP_LEDS]) {
    for (int i = 0; i < STRIP_LEDS; i++) out[i] = STRIP_OFF;

    // Status pixel: a lost link is the most urgent thing it can say.
    if (!in.linksOk) {
        out[STRIP_STATUS_LED] = stripSlowBlink(nowMs) ? STRIP_RED : STRIP_OFF;
    } else if (in.systemIssue) {
        out[STRIP_STATUS_LED] = stripSlowBlink(nowMs) ? STRIP_YELLOW : STRIP_OFF;
    } else {
        out[STRIP_STATUS_LED] = STRIP_GREEN;
    }

    if (in.alarm) {
        const bool on = ((nowMs - in.alarmStartMs) % 2000) < 1000;   // the buzzer's 1 s / 1 s
        for (int i = STRIP_BAR_START; i < STRIP_LEDS; i++) out[i] = on ? STRIP_RED : STRIP_OFF;
        return;
    }
    if (in.score < STRIP_RED_MIN) {
        const bool on = stripFastBlink(nowMs);
        for (int i = STRIP_BAR_START; i < STRIP_LEDS; i++) out[i] = on ? STRIP_RED : STRIP_OFF;
        return;
    }
    Rgb c = stripBandColor(in.score);
    if (in.partial) c = stripHalf(c);
    const int lit = stripBarLit(in.score);
    for (int i = 0; i < lit; i++) out[STRIP_BAR_START + i] = c;
}

#ifdef ARDUINO
class LedStrip {
public:
    void begin();
    // Call every loop(); sends a frame only when it changed (at most 50 Hz).
    void update(const StripInputs &in, uint32_t nowMs);
    // LEDSTRIP serial command: step through every band and status pattern.
    void startSelfTest(uint32_t nowMs);
    // RAINBOW command: flash the seven rainbow colours `cycles` times.
    void startRainbow(uint32_t nowMs, int cycles = STRIP_RAINBOW_CYCLES);
    void stopTests() { _selfTest = false; _rainbow = false; }
    bool present() const { return _ok; }
    int lit() const { return _lit; }

private:
    void show(const Rgb *frame, uint32_t nowMs);
    Rgb _last[STRIP_LEDS] = {};
    bool _ok = false;
    bool _sent = false;
    uint32_t _lastSendMs = 0;
    uint32_t _lampUntil = 0;
    uint32_t _selfTestStart = 0;
    bool _selfTest = false;
    bool _rainbow = false;
    uint32_t _rainbowStart = 0;
    int _rainbowCycles = STRIP_RAINBOW_CYCLES;
    int _lit = 0;
};

extern LedStrip Strip;
#endif
