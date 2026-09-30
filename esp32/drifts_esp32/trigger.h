// ===============================================================
//  trigger.h - rolling-window driving anomaly detector
//
//  Rewritten for the BLE dongle data path. Three things changed from
//  the CAN version and all three were bugs:
//
//  1. Windows are wall-clock, not sample counts. The old buffer held
//     64 samples pushed every 10 ms - 0.64 s of history against a
//     window configured for 30-60 s.
//
//  2. Samples are pushed only when a new reading actually arrives.
//     The old code pushed the last value every loop, so the buffers
//     filled with duplicates and every standard deviation collapsed
//     toward zero.
//
//  3. Harsh braking comes from the IMU, not from the OBD speed PID.
//     PID 0x0D has 1 km/h resolution and arrives at ~2 Hz, so a
//     deceleration derived from it moves in steps of about 0.06 g.
//     Brake events are pushed in as events, already debounced.
// ===============================================================

#pragma once

#include <Arduino.h>
#include <stdint.h>

struct TriggerSnapshot {
    float rpm = 0.0f;
    float speed = 0.0f;
    float throttle = 0.0f;

    float rpm_stddev = 0.0f;
    float speed_stddev = 0.0f;
    float throttle_stddev = 0.0f;
    int hard_brake_count = 0;

    int rpm_samples = 0;
    int speed_samples = 0;
    int throttle_samples = 0;

    bool gate_open = false;       // speed gate satisfied
    bool accel_available = false; // an IMU is feeding brake events
    bool triggered = false;
    char reason[24] = {0};
};

class DrivingTrigger {
public:
    DrivingTrigger();

    // Feed one newly decoded reading. Call only on a genuinely new value.
    void pushRpm(float rpm, uint32_t ts_ms);
    void pushSpeed(float kph, uint32_t ts_ms);
    void pushThrottle(float pct, uint32_t ts_ms);

    // Feed a debounced harsh-deceleration event from the IMU.
    void pushBrakeEvent(uint32_t ts_ms);

    // Tell the trigger whether an accelerometer is actually present.
    // With no IMU the brake condition is disabled rather than silently
    // reporting zero events.
    void setAccelAvailable(bool available) { _accelAvailable = available; }

    // Returns true on the rising edge of a trigger.
    bool evaluate(uint32_t now_ms);

    const TriggerSnapshot &snapshot() const { return _snap; }
    bool isInCooldown(uint32_t now_ms) const;

private:
    // 60 s at the worst-case per-PID rate the dongle can sustain, with
    // headroom. Three of these plus a small event ring is about 4.6 kB.
    static constexpr int MAX_SAMPLES = 192;
    static constexpr int MAX_EVENTS = 32;

    struct Sample {
        uint32_t ts_ms;
        float value;
    };

    struct RollingBuffer {
        Sample samples[MAX_SAMPLES];
        int head = 0;
        int count = 0;

        void push(uint32_t ts_ms, float value);
        int  countInWindow(uint32_t now_ms, uint32_t window_ms) const;
        float stddev(uint32_t now_ms, uint32_t window_ms, int *n_out) const;
        // Fraction of samples in the window at or above `threshold`.
        float fractionAtLeast(uint32_t now_ms, uint32_t window_ms, float threshold) const;
        float latest() const;
    };

    struct EventRing {
        uint32_t ts_ms[MAX_EVENTS] = {0};
        int head = 0;
        int count = 0;

        void push(uint32_t ts);
        int countInWindow(uint32_t now_ms, uint32_t window_ms) const;
    };

    RollingBuffer _rpm;
    RollingBuffer _speed;
    RollingBuffer _throttle;
    EventRing _brakes;

    bool _accelAvailable;
    uint32_t _lastTriggerMs;
    bool _wasTriggered;
    TriggerSnapshot _snap;
};
