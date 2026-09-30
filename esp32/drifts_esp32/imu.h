// ===============================================================
//  imu.h - MPU-6050 longitudinal acceleration, harsh-brake events
//
//  This is where harsh braking is detected, not from the OBD speed
//  PID. See the note in config.h.
//
//  Mounting matters: LONG_AXIS picks which accelerometer axis points
//  along the vehicle, and LONG_SIGN makes braking read negative. Get
//  these wrong and hard braking registers as hard acceleration.
//  Verify once on a test drive before trusting any event count.
// ===============================================================

#pragma once

#include <Arduino.h>
#include <stdint.h>

// 0 = X, 1 = Y, 2 = Z. Sign flips the axis so that braking is negative.
constexpr int   IMU_LONG_AXIS = 0;
constexpr float IMU_LONG_SIGN = 1.0f;

constexpr uint32_t IMU_SAMPLE_INTERVAL_MS = 20;    // 50 Hz
constexpr float    IMU_LPF_ALPHA          = 0.2f;  // first-order low-pass on the axis
constexpr uint32_t IMU_BRAKE_MIN_MS       = 200;   // sustained this long to count as one event
constexpr float    IMU_BRAKE_REARM_G      = 0.15f; // must recover above -this before re-arming

class Imu {
public:
    bool begin();
    bool isPresent() const { return _present; }

    // Non-blocking; call every loop(). Returns true when a new harsh
    // deceleration event has just been confirmed.
    bool service(uint32_t now_ms);

    float longitudinalG() const { return _filtered; }

private:
    bool readRawAccel(int16_t *ax, int16_t *ay, int16_t *az);
    void calibrate();

    bool _present = false;
    float _bias = 0.0f;
    float _filtered = 0.0f;
    uint32_t _lastSampleMs = 0;

    bool _inBrake = false;        // currently below the threshold
    bool _armed = true;           // ready to emit another event
    uint32_t _brakeStartMs = 0;
};

extern Imu Motion;
