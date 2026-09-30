#include "imu.h"
#include "config.h"

#include <Wire.h>

Imu Motion;

static constexpr uint8_t REG_PWR_MGMT_1  = 0x6B;
static constexpr uint8_t REG_ACCEL_CONFIG = 0x1C;
static constexpr uint8_t REG_ACCEL_XOUT_H = 0x3B;
static constexpr uint8_t REG_WHO_AM_I     = 0x75;

// +/-2 g range: 16384 LSB per g. Ample for vehicle longitudinal accel,
// which rarely exceeds 0.8 g under hard braking.
static constexpr float LSB_PER_G = 16384.0f;

bool Imu::begin() {
    Wire.begin(IMU_SDA_PIN, IMU_SCL_PIN);
    Wire.setClock(400000);

    Wire.beginTransmission(IMU_I2C_ADDR);
    Wire.write(REG_WHO_AM_I);
    if (Wire.endTransmission(false) != 0) {
        Serial.println("[IMU] No response on I2C; harsh-brake detection disabled");
        _present = false;
        return false;
    }
    Wire.requestFrom((int)IMU_I2C_ADDR, 1);
    if (Wire.available() < 1) {
        Serial.println("[IMU] WHO_AM_I read failed");
        _present = false;
        return false;
    }
    const uint8_t who = Wire.read();

    // Wake the device and select the +/-2 g range.
    Wire.beginTransmission(IMU_I2C_ADDR);
    Wire.write(REG_PWR_MGMT_1);
    Wire.write(0x00);
    Wire.endTransmission();
    delay(100);

    Wire.beginTransmission(IMU_I2C_ADDR);
    Wire.write(REG_ACCEL_CONFIG);
    Wire.write(0x00);
    Wire.endTransmission();
    delay(50);

    _present = true;
    Serial.printf("[IMU] MPU-6050 at 0x%02X (WHO_AM_I 0x%02X)\n", IMU_I2C_ADDR, who);

    calibrate();
    return true;
}

bool Imu::readRawAccel(int16_t *ax, int16_t *ay, int16_t *az) {
    Wire.beginTransmission(IMU_I2C_ADDR);
    Wire.write(REG_ACCEL_XOUT_H);
    if (Wire.endTransmission(false) != 0) return false;

    Wire.requestFrom((int)IMU_I2C_ADDR, 6);
    if (Wire.available() < 6) return false;

    *ax = (int16_t)((Wire.read() << 8) | Wire.read());
    *ay = (int16_t)((Wire.read() << 8) | Wire.read());
    *az = (int16_t)((Wire.read() << 8) | Wire.read());
    return true;
}

// Measures the resting offset on the longitudinal axis. The vehicle must
// be stationary and reasonably level for the ~1 s this takes. On a slope
// this bakes in a gravity component, which is a known limitation: a real
// system would estimate pitch continuously.
void Imu::calibrate() {
    const int samples = 100;
    float sum = 0.0f;
    int got = 0;

    for (int i = 0; i < samples; i++) {
        int16_t raw[3];
        if (readRawAccel(&raw[0], &raw[1], &raw[2])) {
            sum += (float)raw[IMU_LONG_AXIS] / LSB_PER_G;
            got++;
        }
        delay(10);
    }
    _bias = (got > 0) ? (sum / (float)got) : 0.0f;
    _filtered = 0.0f;
    Serial.printf("[IMU] Longitudinal bias %.3f g (%d samples)\n", _bias, got);
}

bool Imu::service(uint32_t now_ms) {
    if (!_present) return false;
    if (now_ms - _lastSampleMs < IMU_SAMPLE_INTERVAL_MS) return false;
    _lastSampleMs = now_ms;

    int16_t raw[3];
    if (!readRawAccel(&raw[0], &raw[1], &raw[2])) return false;

    const float g = IMU_LONG_SIGN * (((float)raw[IMU_LONG_AXIS] / LSB_PER_G) - _bias);
    _filtered += IMU_LPF_ALPHA * (g - _filtered);

    bool event = false;

    if (_filtered <= -HARSH_BRAKE_G) {
        if (!_inBrake) {
            _inBrake = true;
            _brakeStartMs = now_ms;
        } else if (_armed && (now_ms - _brakeStartMs >= IMU_BRAKE_MIN_MS)) {
            // One event per sustained deceleration, not one per sample.
            event = true;
            _armed = false;
            Serial.printf("[IMU] Harsh brake: %.2f g\n", _filtered);
        }
    } else {
        _inBrake = false;
        if (_filtered > -IMU_BRAKE_REARM_G) _armed = true;
    }

    return event;
}
