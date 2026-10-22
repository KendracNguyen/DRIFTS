#pragma once

// Alert score: 0-100, where 100 is fully alert.
//
//   score = 0.70 x eye score + 0.30 x driving score
//
// Eye score comes from the Pi camera. Once the eye-state model exists the Pi
// sends it directly as EYE:<0-100> (PERCLOS over a window). Until then the
// Pi's binary answer is mapped onto it: DROWSY -> 10, NOT_DROWSY -> 90, and an
// estimated value eases back toward 90 over about a minute.
//
// Driving score comes from the ESP32's own signals: steering reversals (gyro),
// harsh brakes (IMU), and RPM / speed / throttle variation (OBD). Each signal
// is expressed as a ratio of its trigger threshold; at threshold it costs
// DRIVE_PENALTY_AT_THRESHOLD points.
//
// If no eye data has arrived for EYE_STALE_MS the score falls back to driving
// only and reports partial() == true, so it never looks more trustworthy than
// it is. A future driving source (Logitech G29 via DRV: messages) plugs into
// the same driving ratios.
//
// Pure C++, no Arduino dependency, so the host tests can exercise it.

#include <stdint.h>

enum class AlertLevel : uint8_t { Ok, Caution, Low };

inline const char *alertLevelName(AlertLevel l) {
    switch (l) {
        case AlertLevel::Ok:      return "OK";
        case AlertLevel::Caution: return "CAUTION";
        case AlertLevel::Low:     return "LOW";
    }
    return "?";
}

// Each field = current value / trigger threshold. 1.0 means "at threshold".
struct DrivingRatios {
    float steer = 0.0f;
    float brake = 0.0f;
    float rpm = 0.0f;
    float speed = 0.0f;
    float throttle = 0.0f;
};

struct AlertConfig {
    float eyeWeight = 0.70f;
    float okAbove = 70.0f;           // >= okAbove: OK
    float lowBelow = 50.0f;          // < lowBelow: LOW (drowsy)
    float hysteresis = 5.0f;         // must clear a band by this much to move up
    float drivePenaltyAtThreshold = 40.0f;
    float driveRatioCap = 1.5f;      // one signal can cost at most 60 points
    uint32_t eyeStaleMs = 120000;    // 2 min without eye data -> partial
    float eyeDrowsy = 10.0f;         // estimate after a DROWSY result
    float eyeAlert = 90.0f;          // estimate after NOT_DROWSY, and recovery target
    float eyeRecoverPerSec = 80.0f / 60.0f;   // estimated eye: 10 -> 90 in ~60 s
};

class AlertScore {
public:
    explicit AlertScore(const AlertConfig &cfg = AlertConfig()) : _cfg(cfg) {}

    // --- inputs -------------------------------------------------------------
    // Measured eye score from the Pi (EYE:<n>). Held until it goes stale.
    void setEye(float value, uint32_t nowMs) {
        _eye = clamp(value, 0.0f, 100.0f);
        _eyeEstimated = false;
        _eyeAtMs = nowMs;
        _haveEye = true;
    }

    // Binary Pi result, mapped while no eye model exists.
    void onPiResult(bool drowsy, uint32_t nowMs) {
        _eye = drowsy ? _cfg.eyeDrowsy : _cfg.eyeAlert;
        _eyeEstimated = true;
        _eyeAtMs = nowMs;
        _haveEye = true;
    }

    // Bench override (serial SCORE n): pins the score for durationMs.
    void force(float value, uint32_t nowMs, uint32_t durationMs) {
        _forced = clamp(value, 0.0f, 100.0f);
        _forceUntilMs = nowMs + durationMs;
        _forcing = true;
    }
    void clearForce() { _forcing = false; }

    // Call about once a second with the current driving ratios.
    void update(const DrivingRatios &d, uint32_t nowMs) {
        // Estimated eye values recover toward "alert" as time passes with no
        // new DROWSY; measured ones are held as reported.
        if (_haveEye && _eyeEstimated && _eye < _cfg.eyeAlert && _lastUpdateMs != 0) {
            const float dt = (float)(uint32_t)(nowMs - _lastUpdateMs) / 1000.0f;
            _eye += _cfg.eyeRecoverPerSec * dt;
            if (_eye > _cfg.eyeAlert) _eye = _cfg.eyeAlert;
        }
        _lastUpdateMs = nowMs;

        _driving = drivingScore(d);
        _partial = !_haveEye || (uint32_t)(nowMs - _eyeAtMs) > _cfg.eyeStaleMs;

        if (_forcing && (int32_t)(nowMs - _forceUntilMs) >= 0) _forcing = false;

        if (_forcing) {
            _score = _forced;
        } else if (_partial) {
            _score = _driving;
        } else {
            _score = _cfg.eyeWeight * _eye + (1.0f - _cfg.eyeWeight) * _driving;
        }
        updateLevel();
    }

    // --- outputs ------------------------------------------------------------
    float score() const { return _score; }
    float eye() const { return _eye; }
    float driving() const { return _driving; }
    bool partial() const { return _partial && !_forcing; }
    bool forced() const { return _forcing; }
    bool eyeEstimated() const { return _eyeEstimated; }
    AlertLevel level() const { return _level; }

    float drivingScore(const DrivingRatios &d) const {
        const float r[] = {d.steer, d.brake, d.rpm, d.speed, d.throttle};
        float penalty = 0.0f;
        for (float x : r) {
            penalty += _cfg.drivePenaltyAtThreshold * clamp(x, 0.0f, _cfg.driveRatioCap);
        }
        return clamp(100.0f - penalty, 0.0f, 100.0f);
    }

private:
    static float clamp(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

    // Downward moves happen at the threshold; upward moves need the extra
    // hysteresis, so a score hovering on a boundary does not flicker the LEDs.
    void updateLevel() {
        const float h = _cfg.hysteresis;
        switch (_level) {
            case AlertLevel::Ok:
                if (_score < _cfg.lowBelow) _level = AlertLevel::Low;
                else if (_score < _cfg.okAbove) _level = AlertLevel::Caution;
                break;
            case AlertLevel::Caution:
                if (_score < _cfg.lowBelow) _level = AlertLevel::Low;
                else if (_score >= _cfg.okAbove + h) _level = AlertLevel::Ok;
                break;
            case AlertLevel::Low:
                if (_score >= _cfg.okAbove + h) _level = AlertLevel::Ok;
                else if (_score >= _cfg.lowBelow + h) _level = AlertLevel::Caution;
                break;
        }
    }

    AlertConfig _cfg;
    float _eye = 90.0f;
    bool _eyeEstimated = true;
    bool _haveEye = false;
    uint32_t _eyeAtMs = 0;
    float _driving = 100.0f;
    float _score = 100.0f;
    bool _partial = true;
    AlertLevel _level = AlertLevel::Ok;
    uint32_t _lastUpdateMs = 0;
    bool _forcing = false;
    float _forced = 100.0f;
    uint32_t _forceUntilMs = 0;
};

// --- Reserved for the Logitech G29 simulator source (not used yet) --------
// DRV:<steer_deg>:<throttle_pct>:<brake_pct>, e.g. DRV:-12.5:30:0
// Parsed and stored so the message format is fixed now; turning a stream of
// these into DrivingRatios is the G29 milestone, after the LEDs and the LCD.
struct DrvSample {
    float steerDeg = 0.0f;
    float throttlePct = 0.0f;
    float brakePct = 0.0f;
};

inline bool parseDrv(const char *s, DrvSample *out) {
    if (s == nullptr || out == nullptr) return false;
    if (!(s[0] == 'D' || s[0] == 'd') || !(s[1] == 'R' || s[1] == 'r') ||
        !(s[2] == 'V' || s[2] == 'v') || s[3] != ':') return false;
    const char *p = s + 4;
    float v[3];
    for (int i = 0; i < 3; i++) {
        // Minimal float parser: optional sign, digits, optional fraction.
        bool neg = false;
        if (*p == '-' || *p == '+') { neg = (*p == '-'); ++p; }
        if (!(*p >= '0' && *p <= '9')) return false;
        float x = 0.0f;
        while (*p >= '0' && *p <= '9') { x = x * 10.0f + (float)(*p - '0'); ++p; }
        if (*p == '.') {
            ++p;
            float scale = 0.1f;
            while (*p >= '0' && *p <= '9') { x += scale * (float)(*p - '0'); scale *= 0.1f; ++p; }
        }
        v[i] = neg ? -x : x;
        if (i < 2) {
            if (*p != ':') return false;
            ++p;
        }
    }
    if (*p != '\0') return false;
    if (v[1] < 0 || v[1] > 100 || v[2] < 0 || v[2] > 100) return false;
    out->steerDeg = v[0];
    out->throttlePct = v[1];
    out->brakePct = v[2];
    return true;
}

// EYE:<0-100> from the Pi. Integer only; anything else is rejected.
inline bool parseEye(const char *s, int *out) {
    if (s == nullptr || out == nullptr) return false;
    if (!(s[0] == 'E' || s[0] == 'e') || !(s[1] == 'Y' || s[1] == 'y') ||
        !(s[2] == 'E' || s[2] == 'e') || s[3] != ':') return false;
    const char *p = s + 4;
    if (!(*p >= '0' && *p <= '9')) return false;
    int v = 0;
    while (*p >= '0' && *p <= '9') {
        v = v * 10 + (*p - '0');
        if (v > 100) return false;
        ++p;
    }
    if (*p != '\0') return false;
    *out = v;
    return true;
}
