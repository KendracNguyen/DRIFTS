// ===============================================================
//  config.h - DRIFTS ESP32 configuration
//
//  Data path: BLE ELM327 dongle -> ESP32 (central)
//             ESP32 (peripheral) -> Raspberry Pi + phone app
//
//  The ESP32 holds three BLE connections at once. That needs NimBLE
//  with MYNEWT_VAL_BLE_MAX_CONNECTIONS raised to at least 4, and the
//  "Huge APP" partition scheme. See README_BLE_OBD.md.
// ===============================================================

#pragma once

#include <Arduino.h>
#include <stdint.h>

// ---------------------------------------------------------------
// Pin assignments  --  TARGET: ESP32-C6-WROOM-1
// ---------------------------------------------------------------
// These are NOT the ESP32-WROOM-32 pins. On the C6, GPIO24-30 are the
// SPI flash lines and are not bonded out on the WROOM-1 module:
//   GPIO24 SPICS0   GPIO25 SPIQ    GPIO26 SPIWP   GPIO27 VDD_SPI
//   GPIO28 SPIHD    GPIO29 SPICLK  GPIO30 SPID
//
// The trap: the C6's GPIO validity mask covers 0-30, so ledcAttach(26, ...)
// returns true and digitalWrite(25, ...) compiles and runs. They drive the
// flash lines. You get corruption or a crash loop, with nothing pointing at
// the cause. The old 25/26 assignments were exactly this mistake.
//
// Avoid entirely: 4, 5, 8, 9 (strapping), 12, 13 (USB Serial/JTAG),
// 15 (strapping), 16, 17 (UART0 console), 14 and 24-30 (absent).
// GPIO0-6 are also ADC1_CH0-6; leave them free if analog is ever needed.
constexpr int MOTOR_PIN  = 10;   // PWM -> motor transistor base
constexpr int BUZZER_PIN = 11;   // tone -> buzzer transistor base

// There is no plain user LED on any C6 devkit -- the on-board one is an
// addressable RGB on GPIO 8, where digitalWrite() does nothing visible.
// This is an EXTERNAL LED. To use the on-board RGB instead, drop this and
// call rgbLedWrite(RGB_BUILTIN, r, g, b).
constexpr int LED_PIN    = 2;    // external LED (lit when the Pi is subscribed)

// MPU-6050 on the I2C bus. See "Longitudinal acceleration" below for why
// the IMU lives on the ESP32 rather than on the Pi.
//
// imu.cpp calls Wire.begin(IMU_SDA_PIN, IMU_SCL_PIN) explicitly, which
// matters: the C6's Arduino default SDA is 23, not 21, so a bare
// Wire.begin() would silently use the wrong pin.
constexpr int IMU_SDA_PIN = 21;
constexpr int IMU_SCL_PIN = 22;
constexpr uint8_t IMU_I2C_ADDR = 0x68;

// ---------------------------------------------------------------
// OBD dongle (BLE central role)
// ---------------------------------------------------------------
// Dongle name hints, matched case-insensitively against the advertised
// name. Add your dongle's name here if it is not matched.
constexpr const char *OBD_NAME_HINTS[] = {
    "obd", "elm", "vgate", "icar", "vlink", "v-link",
    "veepeak", "obdlink", "konnwei", "carista"};
constexpr size_t OBD_NAME_HINT_COUNT = sizeof(OBD_NAME_HINTS) / sizeof(OBD_NAME_HINTS[0]);

// If you know your dongle's MAC, set it here to skip name matching.
// Format "aa:bb:cc:dd:ee:ff", empty string to disable.
constexpr const char *OBD_MAC_ADDRESS = "";

constexpr uint32_t OBD_SCAN_DURATION_S    = 10;
constexpr uint32_t OBD_CONNECT_TIMEOUT_MS = 5000;   // NimBLE connect() can hang; watchdog on this
constexpr uint32_t OBD_RECONNECT_DELAY_MS = 3000;

// ELM327 initialisation. Protocol '0' is auto-detect, which costs a
// SEARCHING... stall of up to ~20 s on the first request. Once you know
// the test vehicle, force it: '6' = ISO 15765-4 CAN 11-bit 500 kbaud,
// which is correct for essentially every 2008+ US vehicle.
constexpr char     ELM_PROTOCOL       = '6';
constexpr uint16_t ELM_TIMEOUT_MS     = 2000;
constexpr uint16_t ELM_PAYLOAD_LEN    = 128;

// ---------------------------------------------------------------
// Polling
// ---------------------------------------------------------------
// A BLE ELM327 dongle delivers roughly 6-11 request/response cycles per
// second in total, shared across every PID. With four PIDs in rotation
// that is about 1.5-2.75 Hz per signal. Do not design around more.
// Measured achieved rate is reported in the telemetry as "hz".
constexpr uint32_t OBD_POLL_INTERVAL_MS = 20;   // how often the poller is serviced, not the PID rate
constexpr uint32_t OBD_STALL_TIMEOUT_MS = 8000; // no successful read in this long -> reinitialise

// ---------------------------------------------------------------
// Trigger thresholds
// ---------------------------------------------------------------
// The trigger fires when ANY condition is met and the cooldown has expired.
// All windows are wall-clock, not sample counts.

// Harsh braking, from the IMU's longitudinal axis.
//
// NOTE: this deliberately does NOT come from the OBD speed PID. PID 0x0D
// has 1 km/h resolution, and at ~2 Hz one quantisation step is about
// 0.06 g, so a deceleration computed from it is a staircase and jerk is
// pure noise. Harsh-event detection belongs on the accelerometer; OBD
// provides context (is the engine running, what is the throttle doing).
constexpr float HARSH_BRAKE_G        = 0.35f;  // longitudinal decel counted as a hard brake
constexpr int   BRAKE_EVENT_TRIGGER  = 3;      // this many in the window -> trigger
constexpr int   BRAKE_WINDOW_S       = 60;

// RPM fluctuation: standard deviation over the window.
constexpr float RPM_STDDEV_TRIGGER   = 400.0f;
constexpr int   RPM_WINDOW_S         = 30;

// Throttle jitter: standard deviation of throttle %.
constexpr float THROTTLE_STDDEV_TRIGGER = 15.0f;
constexpr int   THROTTLE_WINDOW_S       = 30;

// Speed variability. Adequate from the OBD PID despite 1 km/h resolution,
// because the standard deviation over 30 s of real driving is many km/h.
constexpr float SPEED_STDDEV_TRIGGER = 8.0f;
constexpr int   SPEED_WINDOW_S       = 30;

// Operating-domain gate. Vehicle-dynamics fatigue metrics are only valid
// on open road above highway speed; below this the trigger is suppressed
// rather than allowed to fire on stop-and-go traffic. Volvo's production
// Driver Alert Control uses 65 km/h for the same reason.
constexpr float MIN_SPEED_KPH        = 65.0f;
constexpr float GATE_COVERAGE        = 0.80f;  // fraction of the window that must be above MIN_SPEED_KPH

// Minimum samples in a window before its statistic is trusted.
constexpr int   MIN_SAMPLES_FOR_STAT = 12;

constexpr int   TRIGGER_COOLDOWN_S   = 30;

// ---------------------------------------------------------------
// Alert behaviour
// ---------------------------------------------------------------
constexpr int DROWSY_VIB_DUTY   = 255;   // 0-255 PWM duty
constexpr int DROWSY_VIB_ON_MS  = 500;
constexpr int DROWSY_VIB_OFF_MS = 300;
constexpr int DROWSY_BUZZ_HZ    = 2700;
constexpr int DROWSY_BUZZ_ON_MS = 300;
constexpr int DROWSY_BUZZ_OFF_MS = 300;

// An alert must not latch forever if the Pi never sends NOT_DROWSY.
constexpr uint32_t MAX_ALERT_MS = 20000;

// ---------------------------------------------------------------
// BLE peripheral (Pi Link + Phone Link)
// ---------------------------------------------------------------
constexpr char DEVICE_NAME[] = "DRIFTS-ESP32";

// Outputs are killed if the Pi goes quiet for this long while alerting.
constexpr uint32_t FAILSAFE_MS = 3000;

// The Pi is expected to PING at this interval; it is the fail-safe input.
constexpr uint32_t PI_PING_EXPECTED_MS = 1000;

constexpr uint32_t PI_RESPONSE_TIMEOUT_MS = 15000;  // Pi has this long to answer a WAKE
constexpr uint32_t PHONE_UPDATE_MS        = 1000;

constexpr char PI_SERVICE_UUID[]     = "DFT10001-B5A3-F393-E0A9-E50E24DCCA9E";
constexpr char PI_WAKE_CHAR_UUID[]   = "DFT10002-B5A3-F393-E0A9-E50E24DCCA9E";  // notify -> Pi
constexpr char PI_RESULT_CHAR_UUID[] = "DFT10003-B5A3-F393-E0A9-E50E24DCCA9E";  // write  <- Pi

constexpr char PHONE_SERVICE_UUID[]     = "DFT20001-B5A3-F393-E0A9-E50E24DCCA9E";
constexpr char PHONE_STATUS_CHAR_UUID[] = "DFT20002-B5A3-F393-E0A9-E50E24DCCA9E";  // notify -> phone
