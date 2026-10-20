#pragma once

// Steering-reversal detector on the gyro yaw rate (degrees per second).
//
// Drowsy drivers drift, then correct sharply, then drift again. Steering
// reversal rate is a long-standing fatigue measure; here it is taken from
// how the car actually turns (yaw rate from the MPU-6050) rather than from
// the steering wheel, which standard OBD-II does not expose.
//
// A reversal is counted when the yaw rate goes beyond +threshold and then
// beyond -threshold (or the other way round) within maxSwingMs. Small
// lane-keeping wobble stays inside the band and is ignored; a slow transition
// between two long bends takes longer than maxSwingMs and is ignored too.
//
// Pure C++, no Arduino dependency, so the host tests can exercise it.

#include <stdint.h>

class WeaveDetector {
public:
    WeaveDetector(float thresholdDps, uint32_t maxSwingMs)
        : _threshold(thresholdDps), _maxSwingMs(maxSwingMs) {}

    // Feed one filtered yaw-rate sample. Returns true when a reversal completes.
    bool feed(float yawDps, uint32_t nowMs) {
        int side = 0;
        if (yawDps >= _threshold) side = 1;
        else if (yawDps <= -_threshold) side = -1;
        if (side == 0) return false;          // inside the dead band

        if (_side == 0 || side == _side) {
            _side = side;
            _lastBeyondMs = nowMs;            // most recent moment past the threshold
            return false;
        }

        // Crossed to the opposite side. A reversal only if it happened fast.
        const bool counted = (uint32_t)(nowMs - _lastBeyondMs) <= _maxSwingMs;
        _side = side;
        _lastBeyondMs = nowMs;
        return counted;
    }

    void reset() { _side = 0; _lastBeyondMs = 0; }

private:
    float _threshold;
    uint32_t _maxSwingMs;
    int _side = 0;               // -1, 0 (not yet seen), +1
    uint32_t _lastBeyondMs = 0;
};
