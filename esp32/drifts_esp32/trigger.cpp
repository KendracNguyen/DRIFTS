#include "trigger.h"
#include "config.h"
#include <math.h>
#include <string.h>

void DrivingTrigger::RollingBuffer::push(uint32_t ts_ms, float val) {
    samples[head].ts_ms = ts_ms;
    samples[head].value = val;
    head = (head + 1) % MAX_SAMPLES;
    if (count < MAX_SAMPLES) count++;
}

float DrivingTrigger::RollingBuffer::stddev(uint32_t now_ms, uint32_t window_ms) const {
    if (count < 2) return 0.0f;

    float sum = 0.0f;
    int valid = 0;

    for (int i = 0; i < count; i++) {
        int idx = (head - 1 - i + MAX_SAMPLES) % MAX_SAMPLES;
        if (now_ms - samples[idx].ts_ms > window_ms) break;
        sum += samples[idx].value;
        valid++;
    }

    if (valid < 2) return 0.0f;

    float mean = sum / valid;
    float varianceSum = 0.0f;

    for (int i = 0; i < count; i++) {
        int idx = (head - 1 - i + MAX_SAMPLES) % MAX_SAMPLES;
        if (now_ms - samples[idx].ts_ms > window_ms) break;
        float diff = samples[idx].value - mean;
        varianceSum += diff * diff;
    }

    return sqrtf(varianceSum / (valid - 1));
}

int DrivingTrigger::RollingBuffer::countExceeding(uint32_t now_ms, uint32_t window_ms, float threshold) const {
    int hits = 0;
    for (int i = 0; i < count; i++) {
        int idx = (head - 1 - i + MAX_SAMPLES) % MAX_SAMPLES;
        if (now_ms - samples[idx].ts_ms > window_ms) break;
        if (samples[idx].value >= threshold) {
            hits++;
        }
    }
    return hits;
}

DrivingTrigger::DrivingTrigger()
    : lastTriggerTimeMs(0) {}

bool DrivingTrigger::isInCooldown(uint32_t now_ms) const {
    if (lastTriggerTimeMs == 0) return false;
    return (now_ms - lastTriggerTimeMs) < (uint32_t)(TRIGGER_COOLDOWN_S * 1000);
}

void DrivingTrigger::resetCooldown(uint32_t now_ms) {
    lastTriggerTimeMs = now_ms;
}

bool DrivingTrigger::evaluate(float rpm, float speed, float throttle, float brake_decel, uint32_t now_ms) {
    rpmBuffer.push(now_ms, rpm);
    speedBuffer.push(now_ms, speed);
    throttleBuffer.push(now_ms, throttle);
    brakeBuffer.push(now_ms, brake_decel);

    float rpm_std = rpmBuffer.stddev(now_ms, RPM_WINDOW_S * 1000);
    float speed_std = speedBuffer.stddev(now_ms, SPEED_WINDOW_S * 1000);
    float thr_std = throttleBuffer.stddev(now_ms, THROTTLE_WINDOW_S * 1000);
    int hard_brakes = brakeBuffer.countExceeding(now_ms, BRAKE_WINDOW_S * 1000, BRAKE_DECEL_THRESHOLD);

    lastSnapshot.rpm = rpm;
    lastSnapshot.speed = speed;
    lastSnapshot.throttle = throttle;
    lastSnapshot.brake_decel = brake_decel;
    lastSnapshot.rpm_stddev = rpm_std;
    lastSnapshot.speed_stddev = speed_std;
    lastSnapshot.throttle_stddev = thr_std;
    lastSnapshot.hard_brake_count = hard_brakes;
    lastSnapshot.triggered = false;
    lastSnapshot.trigger_reason[0] = '\0';

    if (isInCooldown(now_ms)) {
        return false;
    }

    bool shouldTrigger = false;
    if (hard_brakes >= BRAKE_EVENT_TRIGGER) {
        shouldTrigger = true;
        strncpy(lastSnapshot.trigger_reason, "HARD_BRAKES", sizeof(lastSnapshot.trigger_reason) - 1);
    } else if (speed_std >= SPEED_STDDEV_TRIGGER) {
        shouldTrigger = true;
        strncpy(lastSnapshot.trigger_reason, "SPEED_DRIFT", sizeof(lastSnapshot.trigger_reason) - 1);
    } else if (rpm_std >= RPM_STDDEV_TRIGGER) {
        shouldTrigger = true;
        strncpy(lastSnapshot.trigger_reason, "RPM_FLUCTUATION", sizeof(lastSnapshot.trigger_reason) - 1);
    } else if (thr_std >= THROTTLE_STDDEV_TRIGGER) {
        shouldTrigger = true;
        strncpy(lastSnapshot.trigger_reason, "THROTTLE_JITTER", sizeof(lastSnapshot.trigger_reason) - 1);
    }

    if (shouldTrigger) {
        lastTriggerTimeMs = now_ms;
        lastSnapshot.triggered = true;
        return true;
    }

    return false;
}
