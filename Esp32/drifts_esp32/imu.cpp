#include "imu.h"
#include "config.h"

#include <Wire.h>

Imu Motion;

static constexpr uint8_t REG_PWR_MGMT_1  = 0x6B;
static constexpr uint8_t REG_CONFIG       = 0x1A;
static constexpr uint8_t REG_GYRO_CONFIG  = 0x1B;
static constexpr uint8_t REG_ACCEL_CONFIG = 0x1C;
static constexpr uint8_t REG_ACCEL_XOUT_H = 0x3B;
static constexpr uint8_t REG_WHO_AM_I     = 0x75;

// +/-2 g range: 16384 LSB per g. Ample for vehicle longitudinal accel,
// which rarely exceeds 0.8 g under hard braking.
static constexpr float LSB_PER_G = 16384.0f;
// +/-250 deg/s range: 131 LSB per deg/s. A car rarely yaws faster than
// 30 deg/s even in a tight turn; finest resolution matters more.
static constexpr float LSB_PER_DPS = 131.0f;

static void writeReg(uint8_t reg, uint8_t value) {
    Wire.beginTransmission(IMU_I2C_ADDR);
    Wire.write(reg);
    Wire.write(value);
    Wire.endTransmission();
}

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

    writeReg(REG_ACCEL_CONFIG, 0x00);   // +/-2 g
    writeReg(REG_GYRO_CONFIG, 0x00);    // +/-250 deg/s
    writeReg(REG_CONFIG, 0x03);         // on-chip low-pass ~44 Hz: cuts engine vibration
    delay(50);

    _weave = WeaveDetector(STEER_REVERSAL_DPS, STEER_REVERSAL_MAX_MS);

    _present = true;
    Serial.printf("[IMU] MPU-6050 at 0x%02X (WHO_AM_I 0x%02X)\n", IMU_I2C_ADDR, who);

    calibrate();
    return true;
}

// One burst read: accel X/Y/Z, temperature (skipped), gyro X/Y/Z.
bool Imu::readRaw(int16_t accel[3], int16_t gyro[3]) {
    Wire.beginTransmission(IMU_I2C_ADDR);
    Wire.write(REG_ACCEL_XOUT_H);
    if (Wire.endTransmission(false) != 0) return false;

    Wire.requestFrom((int)IMU_I2C_ADDR, 14);
    if (Wire.available() < 14) return false;

    for (int i = 0; i < 3; i++) accel[i] = (int16_t)((Wire.read() << 8) | Wire.read());
    Wire.read(); Wire.read();   // temperature
    for (int i = 0; i < 3; i++) gyro[i] = (int16_t)((Wire.read() << 8) | Wire.read());
    return true;
}

bool Imu::takeSteeringReversal() {
    if (!_reversalPending) return false;
    _reversalPending = false;
    return true;
}

// Measures the resting offset on the longitudinal axis. The vehicle must
// be stationary and reasonably level for the ~1 s this takes. On a slope
// this bakes in a gravity component, which is a known limitation: a real
// system would estimate pitch continuously.
void Imu::calibrate() {
    const int samples = 100;
    float sum = 0.0f, gyroSum = 0.0f;
    int got = 0;

    for (int i = 0; i < samples; i++) {
        int16_t a[3], g[3];
        if (readRaw(a, g)) {
            sum += (float)a[IMU_LONG_AXIS] / LSB_PER_G;
            gyroSum += (float)g[IMU_YAW_AXIS] / LSB_PER_DPS;
            got++;
        }
        delay(10);
    }
    _bias = (got > 0) ? (sum / (float)got) : 0.0f;
    _gyroBias = (got > 0) ? (gyroSum / (float)got) : 0.0f;
    _filtered = 0.0f;
    _yaw = 0.0f;
    _yawDrift = 0.0f;
    Serial.printf("[IMU] Longitudinal bias %.3f g, yaw bias %.2f deg/s (%d samples)\n",
                  _bias, _gyroBias, got);
}

bool Imu::service(uint32_t now_ms) {
    if (!_present) return false;
    if (now_ms - _lastSampleMs < IMU_SAMPLE_INTERVAL_MS) return false;
    _lastSampleMs = now_ms;

    int16_t raw[3], gyro[3];
    if (!readRaw(raw, gyro)) return false;

    const float g = IMU_LONG_SIGN * (((float)raw[IMU_LONG_AXIS] / LSB_PER_G) - _bias);
    _filtered += IMU_LPF_ALPHA * (g - _filtered);

    // Yaw rate. The at-rest bias is removed first; a very slow tracker then
    // follows whatever drift remains as the sensor warms up. A 40 s time
    // constant is far slower than any weave (1-6 s), so it cannot cancel one.
    const float yawRaw = IMU_YAW_SIGN * (((float)gyro[IMU_YAW_AXIS] / LSB_PER_DPS) - _gyroBias);
    _yawDrift += IMU_YAW_DRIFT_ALPHA * (yawRaw - _yawDrift);
    _yaw += IMU_YAW_LPF_ALPHA * ((yawRaw - _yawDrift) - _yaw);

    if (_weave.feed(_yaw, now_ms)) {
        _reversals++;
        _reversalPending = true;
        Serial.printf("[IMU] Steering reversal #%lu (yaw %+.1f deg/s)\n",
                      (unsigned long)_reversals, _yaw);
    }
    if (_watch && now_ms - _lastWatchMs >= 200) {
        _lastWatchMs = now_ms;
        Serial.printf("[GYRO] yaw=%+.1f deg/s  reversals=%lu  long=%+.2f g\n",
                      _yaw, (unsigned long)_reversals, _filtered);
    }

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
