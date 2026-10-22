#pragma once

// Three status LEDs.
//
//   GREEN  (connections)  solid = dongle polling and Pi connected
//                         slow blink = a link is missing
//   YELLOW                solid = caution: alert score 50-69
//                         slow blink = system issue: bench mode or IMU missing
//                         (caution wins if both apply)
//   RED    (drowsiness)   fast blink = drowsy: score below 50, or the
//                                      DROWSY alert is running
//                         off otherwise
//
// All three light together for LED_LAMP_TEST_MS at power-up, and the LEDS
// serial command cycles every pattern, so a dead LED is obvious.
//
// The pattern logic is pure (no Arduino) so the host tests cover it; only
// StatusLeds touches the pins.

#include <stdint.h>

#include "alertness.h"

enum class LedPattern : uint8_t { Off, Solid, SlowBlink, FastBlink };

// 1 Hz and 4 Hz, 50 % duty. Nothing faster: more information does not come
// from faster flashing, distraction does.
inline bool ledPatternOn(LedPattern p, uint32_t nowMs) {
    switch (p) {
        case LedPattern::Off:       return false;
        case LedPattern::Solid:     return true;
        case LedPattern::SlowBlink: return (nowMs % 1000) < 500;
        case LedPattern::FastBlink: return (nowMs % 250) < 125;
    }
    return false;
}

inline const char *ledPatternName(LedPattern p) {
    switch (p) {
        case LedPattern::Off:       return "off";
        case LedPattern::Solid:     return "solid";
        case LedPattern::SlowBlink: return "slow";
        case LedPattern::FastBlink: return "fast";
    }
    return "?";
}

struct LedInputs {
    bool dongleOk = false;       // OBD dongle polling
    bool piOk = false;           // Pi connected
    bool systemIssue = false;    // bench mode or IMU missing
    bool alertActive = false;    // DROWSY alert running (motor + buzzer)
    AlertLevel level = AlertLevel::Ok;
};

struct LedState {
    LedPattern green = LedPattern::Off;
    LedPattern yellow = LedPattern::Off;
    LedPattern red = LedPattern::Off;
};

inline LedState decideLeds(const LedInputs &in) {
    LedState s;
    s.green = (in.dongleOk && in.piOk) ? LedPattern::Solid : LedPattern::SlowBlink;
    const bool drowsy = in.alertActive || in.level == AlertLevel::Low;
    s.red = drowsy ? LedPattern::FastBlink : LedPattern::Off;
    // Caution is the driver-relevant state, so it wins yellow over a system
    // issue. Bench mode is on for all desk testing; if it won, caution would
    // never be visible. System issues are also in STATUS and telemetry.
    if (!drowsy && in.level == AlertLevel::Caution) {
        s.yellow = LedPattern::Solid;
    } else if (in.systemIssue) {
        s.yellow = LedPattern::SlowBlink;
    } else {
        s.yellow = LedPattern::Off;
    }
    return s;
}

#ifdef ARDUINO
class StatusLeds {
public:
    void begin();
    // Call every loop(); cheap, non-blocking.
    void update(const LedInputs &in, uint32_t nowMs);
    // LEDS serial command: cycle each LED through every pattern.
    void startSelfTest(uint32_t nowMs);
    const LedState &state() const { return _state; }

private:
    void write(int pin, bool on);
    LedState _state;
    uint32_t _lampTestUntil = 0;
    uint32_t _selfTestStart = 0;
    bool _selfTest = false;
};

extern StatusLeds Leds;
#endif
