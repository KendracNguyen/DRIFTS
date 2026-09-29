// ===============================================================
//  config.h — DRIFTS ESP32 configuration
//  Edit these values to tune trigger sensitivity and hardware pins.
// ===============================================================

#pragma once

#include <Arduino.h>
#include <stdint.h>

// -- Pin Assignments --
constexpr int MOTOR_PIN    = 26;    // PWM -> motor transistor base
constexpr int BUZZER_PIN   = 25;    // tone -> buzzer transistor base
constexpr int LED_PIN      = 2;     // on-board LED (lit when Pi is connected)
constexpr int CAN_TX_PIN   = 21;    // ESP32 TWAI TX -> SN65HVD230 CTX
constexpr int CAN_RX_PIN   = 22;    // ESP32 TWAI RX -> SN65HVD230 CRX

// -- CAN Bus --
constexpr long CAN_BAUD            = 500000; // most modern vehicles use 500 kbps
constexpr bool CAN_LISTEN_ONLY_MODE = true;   // true = passive listen-only, false = active request allowed

// -- OBD-II Trigger Thresholds --
// The trigger fires when ANY of these conditions is met:

// Brake: count of hard-brake events (>threshold) within the rolling window
constexpr float BRAKE_DECEL_THRESHOLD   = 8.0f;   // km/h drop per second = "hard brake"
constexpr int   BRAKE_EVENT_TRIGGER     = 3;      // this many hard brakes in the window -> trigger
constexpr int   BRAKE_WINDOW_S          = 60;     // rolling window (seconds)

// RPM fluctuation: std-dev of RPM over window
constexpr float RPM_STDDEV_TRIGGER      = 400.0f; // RPM std-dev above this -> trigger
constexpr int   RPM_WINDOW_S            = 30;

// Throttle jitter: std-dev of throttle % over window
constexpr float THROTTLE_STDDEV_TRIGGER = 15.0f;  // % std-dev above this -> trigger
constexpr int   THROTTLE_WINDOW_S       = 30;

// Speed drift: std-dev of speed over window (lane weaving / erratic speed)
constexpr float SPEED_STDDEV_TRIGGER    = 8.0f;   // km/h std-dev above this -> trigger
constexpr int   SPEED_WINDOW_S          = 30;

// -- Trigger Cooldown --
constexpr int TRIGGER_COOLDOWN_S        = 30;     // don't re-trigger within this window (seconds)

// -- BLE Configuration --
constexpr char DEVICE_NAME[]            = "DRIFTS-ESP32";
constexpr uint32_t FAILSAFE_MS          = 5000;   // turn off outputs if Pi silent this long

// Custom UUIDs for Pi Link service (Wake & Result)
constexpr char PI_SERVICE_UUID[]        = "DFT10001-B5A3-F393-E0A9-E50E24DCCA9E";
constexpr char PI_WAKE_CHAR_UUID[]      = "DFT10002-B5A3-F393-E0A9-E50E24DCCA9E"; // Notify -> Pi
constexpr char PI_RESULT_CHAR_UUID[]    = "DFT10003-B5A3-F393-E0A9-E50E24DCCA9E"; // Write <- Pi

// Custom UUIDs for Phone Link service (Live Telemetry & Alerts)
constexpr char PHONE_SERVICE_UUID[]     = "DFT20001-B5A3-F393-E0A9-E50E24DCCA9E";
constexpr char PHONE_STATUS_CHAR_UUID[] = "DFT20002-B5A3-F393-E0A9-E50E24DCCA9E"; // Notify -> Phone

// -- Alert Commands (motor/buzzer) --
constexpr int DROWSY_VIB_DUTY           = 255;    // 0-255 PWM duty
constexpr int DROWSY_VIB_ON_MS          = 500;
constexpr int DROWSY_VIB_OFF_MS         = 300;
constexpr int DROWSY_BUZZ_HZ            = 2700;   // audio tone frequency
constexpr int DROWSY_BUZZ_ON_MS         = 300;
constexpr int DROWSY_BUZZ_OFF_MS        = 300;
