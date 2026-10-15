#pragma once

// Pin-test command, sent by the Pi over BLE (or typed on the serial console)
// to drive the motor and buzzer outputs for a set time. Used to prove the
// whole alert chain -- Pi -> BLE -> ESP32 -> GPIO 10/11 -> driver -> load --
// before any detection code is involved.
//
//   TEST:<motor_ms>:<buzzer_ms>          buzzer plays the alert tone
//   TEST:<motor_ms>:<buzzer_ms>:DC       buzzer pin held steady HIGH (silent,
//                                         but measurable with a multimeter)
//
// Either duration may be 0 to leave that output off. Durations above max_ms
// are clamped, so a typo cannot hold 5 V on 3 V motors indefinitely.
//
// Pure C++, no Arduino dependency, so the host tests can exercise it.

#include <ctype.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

struct PinTestCmd {
    uint32_t motorMs = 0;
    uint32_t buzzerMs = 0;
    bool buzzerDc = false;
};

namespace pin_test_detail {

inline bool prefixIgnoreCase(const char *s, const char *prefix) {
    for (; *prefix; ++s, ++prefix) {
        if (tolower((unsigned char)*s) != tolower((unsigned char)*prefix)) return false;
    }
    return true;
}

// Reads digits up to ':' or end. Fails on empty, non-digit or overflow.
inline bool readMs(const char *&p, uint32_t *out) {
    if (!isdigit((unsigned char)*p)) return false;
    uint64_t v = 0;
    while (isdigit((unsigned char)*p)) {
        v = v * 10 + (uint64_t)(*p - '0');
        if (v > 0xFFFFFFFFull) return false;
        ++p;
    }
    *out = (uint32_t)v;
    return true;
}

}  // namespace pin_test_detail

inline bool parsePinTest(const char *s, uint32_t max_ms, PinTestCmd *out) {
    using namespace pin_test_detail;
    if (s == nullptr || out == nullptr) return false;
    if (!prefixIgnoreCase(s, "TEST:")) return false;
    const char *p = s + 5;

    PinTestCmd cmd;
    if (!readMs(p, &cmd.motorMs) || *p != ':') return false;
    ++p;
    if (!readMs(p, &cmd.buzzerMs)) return false;

    if (*p == ':') {
        ++p;
        if (prefixIgnoreCase(p, "DC") && p[2] == '\0') {
            cmd.buzzerDc = true;
        } else if (prefixIgnoreCase(p, "TONE") && p[4] == '\0') {
            cmd.buzzerDc = false;
        } else {
            return false;
        }
    } else if (*p != '\0') {
        return false;
    }

    if (cmd.motorMs > max_ms) cmd.motorMs = max_ms;
    if (cmd.buzzerMs > max_ms) cmd.buzzerMs = max_ms;
    *out = cmd;
    return true;
}
