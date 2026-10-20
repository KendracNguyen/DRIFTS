// ===============================================================
//  imu.h - MPU-6050: harsh-brake events (accelerometer) and
//          steering reversals (gyro yaw rate)
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

#include "weave.h"

// 0 = X, 1 = Y, 2 = Z. Sign flips the axis so that braking is negative.
constexpr int   IMU_LONG_AXIS = 0;
constexpr float IMU_LONG_SIGN = 1.0f;

// Gyro axis that points straight up when the board is mounted: Z if the
// breakout lies flat. Sign only matters for the logged direction (+ = left
// turn when correct); reversal counting uses both directions.
constexpr int   IMU_YAW_AXIS = 2;
constexpr float IMU_YAW_SIGN = 1.0f;
constexpr float IMU_YAW_LPF_ALPHA  = 0.2f;     // first-order low-pass on yaw rate
constexpr float IMU_YAW_DRIFT_ALPHA = 0.0005f; // ~40 s tracker for slow gyro bias drift

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

    // Filtered yaw rate in deg/s, bias-corrected.
    float yawRateDps() const { return _yaw; }
    // True once per newly detected steering reversal. Clears the flag.
    bool takeSteeringReversal();
    uint32_t reversalCount() const { return _reversals; }

    // GYRO serial command: print yaw and reversals at 5 Hz.
    void setWatch(bool on) { _watch = on; }
    bool watching() const { return _watch; }

private:
    bool readRaw(int16_t accel[3], int16_t gyro[3]);
    void calibrate();

    bool _present = false;
    float _bias = 0.0f;
    float _filtered = 0.0f;
    uint32_t _lastSampleMs = 0;

    float _gyroBias = 0.0f;       // deg/s at rest, from calibrate()
    float _yawDrift = 0.0f;       // slow residual drift tracked while driving
    float _yaw = 0.0f;
    WeaveDetector _weave{2.0f, 2000};   // re-initialised from config.h in begin()
    bool _reversalPending = false;
    uint32_t _reversals = 0;
    bool _watch = false;
    uint32_t _lastWatchMs = 0;

    bool _inBrake = false;        // currently below the threshold
    bool _armed = true;           // ready to emit another event
    uint32_t _brakeStartMs = 0;
};

extern Imu Motion;
