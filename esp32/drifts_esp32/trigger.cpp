#include "trigger.h"
#include "config.h"

#include <math.h>
#include <string.h>

// ---------------------------------------------------------------
// RollingBuffer
// ---------------------------------------------------------------
void DrivingTrigger::RollingBuffer::push(uint32_t ts_ms, float value) {
    samples[head].ts_ms = ts_ms;
    samples[head].value = value;
    head = (head + 1) % MAX_SAMPLES;
    if (count < MAX_SAMPLES) count++;
}

int DrivingTrigger::RollingBuffer::countInWindow(uint32_t now_ms, uint32_t window_ms) const {
    int n = 0;
    for (int i = 0; i < count; i++) {
        int idx = (head - 1 - i + MAX_SAMPLES) % MAX_SAMPLES;
        if (now_ms - samples[idx].ts_ms > window_ms) break;
        n++;
    }
    return n;
}

float DrivingTrigger::RollingBuffer::stddev(uint32_t now_ms, uint32_t window_ms, int *n_out) const {
    float sum = 0.0f;
    int n = 0;

    for (int i = 0; i < count; i++) {
        int idx = (head - 1 - i + MAX_SAMPLES) % MAX_SAMPLES;
        if (now_ms - samples[idx].ts_ms > window_ms) break;
        sum += samples[idx].value;
        n++;
    }
    if (n_out != nullptr) *n_out = n;
    if (n < 2) return 0.0f;

    const float mean = sum / (float)n;
    float varianceSum = 0.0f;
    for (int i = 0; i < n; i++) {
        int idx = (head - 1 - i + MAX_SAMPLES) % MAX_SAMPLES;
        const float diff = samples[idx].value - mean;
        varianceSum += diff * diff;
    }
    return sqrtf(varianceSum / (float)(n - 1));
}

float DrivingTrigger::RollingBuffer::fractionAtLeast(uint32_t now_ms, uint32_t window_ms,
                                                    float threshold) const {
    int n = 0, hits = 0;
    for (int i = 0; i < count; i++) {
        int idx = (head - 1 - i + MAX_SAMPLES) % MAX_SAMPLES;
        if (now_ms - samples[idx].ts_ms > window_ms) break;
        if (samples[idx].value >= threshold) hits++;
        n++;
    }
    if (n == 0) return 0.0f;
    return (float)hits / (float)n;
}

float DrivingTrigger::RollingBuffer::latest() const {
    if (count == 0) return 0.0f;
    return samples[(head - 1 + MAX_SAMPLES) % MAX_SAMPLES].value;
}

// ---------------------------------------------------------------
// EventRing
// ---------------------------------------------------------------
void DrivingTrigger::EventRing::push(uint32_t ts) {
    ts_ms[head] = ts;
    head = (head + 1) % MAX_EVENTS;
    if (count < MAX_EVENTS) count++;
}

int DrivingTrigger::EventRing::countInWindow(uint32_t now_ms, uint32_t window_ms) const {
    int n = 0;
    for (int i = 0; i < count; i++) {
        int idx = (head - 1 - i + MAX_EVENTS) % MAX_EVENTS;
        if (now_ms - ts_ms[idx] > window_ms) break;
        n++;
    }
    return n;
}

// ---------------------------------------------------------------
// DrivingTrigger
// ---------------------------------------------------------------
DrivingTrigger::DrivingTrigger()
    : _accelAvailable(false), _lastTriggerMs(0), _wasTriggered(false) {}

void DrivingTrigger::pushRpm(float rpm, uint32_t ts_ms)          { _rpm.push(ts_ms, rpm); }
void DrivingTrigger::pushSpeed(float kph, uint32_t ts_ms)        { _speed.push(ts_ms, kph); }
void DrivingTrigger::pushThrottle(float pct, uint32_t ts_ms)     { _throttle.push(ts_ms, pct); }
void DrivingTrigger::pushBrakeEvent(uint32_t ts_ms)              { _brakes.push(ts_ms); }

bool DrivingTrigger::isInCooldown(uint32_t now_ms) const {
    if (_lastTriggerMs == 0) return false;
    return (now_ms - _lastTriggerMs) < (uint32_t)(TRIGGER_COOLDOWN_S * 1000);
}

bool DrivingTrigger::evaluate(uint32_t now_ms) {
    _snap.rpm = _rpm.latest();
    _snap.speed = _speed.latest();
    _snap.throttle = _throttle.latest();

    _snap.rpm_stddev = _rpm.stddev(now_ms, (uint32_t)RPM_WINDOW_S * 1000, &_snap.rpm_samples);
    _snap.speed_stddev = _speed.stddev(now_ms, (uint32_t)SPEED_WINDOW_S * 1000, &_snap.speed_samples);
    _snap.throttle_stddev =
        _throttle.stddev(now_ms, (uint32_t)THROTTLE_WINDOW_S * 1000, &_snap.throttle_samples);
    _snap.hard_brake_count = _brakes.countInWindow(now_ms, (uint32_t)BRAKE_WINDOW_S * 1000);
    _snap.accel_available = _accelAvailable;
    _snap.triggered = false;
    _snap.reason[0] = '\0';

    // Operating-domain gate. These metrics are only valid on open road
    // above highway speed. Below it the system refuses to score rather
    // than firing on stop-and-go traffic, turns and signals.
    const float fastFraction =
        _speed.fractionAtLeast(now_ms, (uint32_t)SPEED_WINDOW_S * 1000, MIN_SPEED_KPH);
    _snap.gate_open = (_snap.speed_samples >= MIN_SAMPLES_FOR_STAT) && (fastFraction >= GATE_COVERAGE);

    // Condition detection is deliberately separate from firing. The
    // snapshot always reports what is currently true, so the phone and
    // the serial log keep showing the reason during the cooldown that
    // follows a trigger instead of reverting to "OK" a moment later.
    const char *reason = nullptr;

    if (_accelAvailable && _snap.hard_brake_count >= BRAKE_EVENT_TRIGGER) {
        reason = "HARD_BRAKES";
    } else if (_snap.speed_samples >= MIN_SAMPLES_FOR_STAT &&
               _snap.speed_stddev >= SPEED_STDDEV_TRIGGER) {
        reason = "SPEED_DRIFT";
    } else if (_snap.rpm_samples >= MIN_SAMPLES_FOR_STAT &&
               _snap.rpm_stddev >= RPM_STDDEV_TRIGGER) {
        reason = "RPM_FLUCTUATION";
    } else if (_snap.throttle_samples >= MIN_SAMPLES_FOR_STAT &&
               _snap.throttle_stddev >= THROTTLE_STDDEV_TRIGGER) {
        reason = "THROTTLE_JITTER";
    }

    const bool conditionMet = (reason != nullptr);
    if (conditionMet) {
        strncpy(_snap.reason, reason, sizeof(_snap.reason) - 1);
    }
    _snap.triggered = conditionMet;

    if (!conditionMet) {
        _wasTriggered = false;
        return false;
    }

    // Firing is gated separately: outside the operating domain, or inside
    // the cooldown, the condition is reported but no wake is issued.
    if (!_snap.gate_open || isInCooldown(now_ms)) {
        return false;
    }

    // Rising edge only: hold the condition without re-firing every loop.
    if (_wasTriggered) return false;

    _wasTriggered = true;
    _lastTriggerMs = now_ms;
    return true;
}
