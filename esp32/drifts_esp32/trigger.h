#pragma once

#include <Arduino.h>
#include <stdint.h>

struct TriggerSnapshot {
    float rpm = 0.0f;
    float speed = 0.0f;
    float throttle = 0.0f;
    float brake_decel = 0.0f;
    float rpm_stddev = 0.0f;
    float speed_stddev = 0.0f;
    float throttle_stddev = 0.0f;
    int hard_brake_count = 0;
    char trigger_reason[32] = {0};
    bool triggered = false;
};

class DrivingTrigger {
public:
    DrivingTrigger();

    // Call periodically (e.g. every 100ms or on each sample)
    bool evaluate(float rpm, float speed, float throttle, float brake_decel, uint32_t now_ms);

    TriggerSnapshot getSnapshot() const { return lastSnapshot; }
    bool isInCooldown(uint32_t now_ms) const;
    void resetCooldown(uint32_t now_ms);

private:
    static constexpr int MAX_SAMPLES = 64;

    struct Sample {
        uint32_t ts_ms = 0;
        float value = 0.0f;
    };

    struct RollingBuffer {
        Sample samples[MAX_SAMPLES];
        int head = 0;
        int count = 0;

        void push(uint32_t ts_ms, float val);
        float stddev(uint32_t now_ms, uint32_t window_ms) const;
        int countExceeding(uint32_t now_ms, uint32_t window_ms, float threshold) const;
    };

    RollingBuffer rpmBuffer;
    RollingBuffer speedBuffer;
    RollingBuffer throttleBuffer;
    RollingBuffer brakeBuffer;

    uint32_t lastTriggerTimeMs;
    TriggerSnapshot lastSnapshot;
};
