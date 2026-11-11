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

// Printed at boot and by STATUS, so the flashed image can be identified.
// Matches the drifts_c6_8M_uartchip_<version>.bin file name.
#define DRIFTS_FW_VERSION "v23"

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
// Avoid entirely: 5, 8, 9 (strapping), 12, 13 (USB Serial/JTAG),
// 15 (strapping), 16, 17 (UART0 console), 14 and 24-30 (absent),
// 6, 7 (pad JTAG), 4 (strapping).
// GPIO0-6 are also ADC1_CH0-6; leave them free if analog is ever needed.
constexpr int MOTOR_PIN  = 10;   // PWM -> motor module IN via R1 100 R

// Passive piezo LS1 driven differentially between two pins (see piezo.h):
// GPIO 11 -> R6 100 R -> LS1 -> R7 100 R <- GPIO 18, neither leg to GND.
// Pin B carries the same LEDC channel as pin A, inverted: 6.6 Vpp across LS1.
constexpr int PIEZO_A_PIN = 11;   // leg A, non-inverted
constexpr int PIEZO_B_PIN = 18;   // leg B, inverted
constexpr int PIEZO_FREQ_HZ  = 5000;   // alert tone, 50 % duty
constexpr int PIEZO_LEDC_RES = 8;
// Explicit channel, highest of the C6's six. ledcAttach() hands out the
// lowest free channel, and only the motor uses it (channel 0), so 5 is
// never taken. The 5 kHz piezo must also not share a timer with anything:
// the core reuses a timer for any channel with the same frequency and
// resolution (the motor runs at 20 kHz).
constexpr int PIEZO_LEDC_CH  = 5;
// 1 = sweep 4-6 kHz once at boot (piezo.h). The SWEEP serial command does
// the same at any time.
#define PIEZO_SWEEP_TEST 0

// Which buzzer is on GPIO 11 (see piezo.h):
//   BUZZER_PIEZO_DIFF   bare passive piezo between GPIO 11 and GPIO 18 (5 kHz tone)
//   BUZZER_ACTIVE_HIGH  3-pin active buzzer module that sounds while its signal is HIGH
//   BUZZER_ACTIVE_LOW   3-pin active buzzer module that sounds while its signal is LOW
//                       ("low level trigger"; beeps non-stop if driven like a piezo)
// Modules: signal -> GPIO 11, VCC -> 3V3 (or 5V for a 5 V module), GND -> GND;
// GPIO 18 is not used. Wrong kind? Idle beeping = ACTIVE_LOW; a weak buzz on
// TEST:0:3000 but a loud one on TEST:0:3000:DC = ACTIVE_HIGH.
#define BUZZER_PIEZO_DIFF  0
#define BUZZER_ACTIVE_HIGH 1
#define BUZZER_ACTIVE_LOW  2
#define BUZZER_KIND BUZZER_ACTIVE_LOW


// MPU-6050 on the I2C bus. See "Longitudinal acceleration" below for why
// the IMU lives on the ESP32 rather than on the Pi.
//
// imu.cpp calls Wire.begin(IMU_SDA_PIN, IMU_SCL_PIN) explicitly, which
// matters: the C6's Arduino default SDA is 23, not 21, so a bare
// Wire.begin() would silently use the wrong pin.
constexpr int IMU_SDA_PIN = 21;
constexpr int IMU_SCL_PIN = 22;

// The I2C bus carries only the MPU-6050 (a 3.3 V part: no level shifter).
// 100 kHz is kept for long, unshielded car wiring; the IMU's 14-byte read
// takes ~1.6 ms every 20 ms, under 10 % of the bus.
constexpr uint32_t I2C_CLOCK_HZ = 100000;

// Wired link to the Pi Zero (pi_uart.h): UART1 through the GPIO matrix.
// 3.3 V on both sides, so the Pi's GPIO 14/15 connect directly, through a
// 1 k series resistor in each line (limits current if only one board is on).
//   ESP32 GPIO 19 (TX) -> 1k -> Pi GPIO 15 RX (pin 10)
//   ESP32 GPIO 20 (RX) <- 1k <- Pi GPIO 14 TX (pin 8)
//   GND -- Pi GND (pin 6)
// UART0 (GPIO 16/17) stays the USB serial console.
constexpr int PI_UART_TX_PIN = 19;
constexpr int PI_UART_RX_PIN = 20;
constexpr uint32_t PI_UART_BAUD = 115200;
// The Pi counts as connected on the wire if heard within this long. It
// PINGs every second, so this rides over a missed line or two.
constexpr uint32_t PI_UART_LINK_MS = 5000;

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

// Steering weave, from the IMU gyro yaw rate (how fast the car is turning).
// Standard OBD-II has no steering-angle PID; the car's own rotation is a
// better fatigue signal anyway, and the gyro gives it at 50 Hz instead of
// the ~1 Hz per signal the dongle manages (measured on the A5).
//
// A reversal = yaw rate past +STEER_REVERSAL_DPS then past -STEER_REVERSAL_DPS
// (or back) within STEER_REVERSAL_MAX_MS. At 100 km/h, 2 deg/s of yaw is
// about 0.1 g of lateral acceleration: a real correction, not lane-keeping
// wobble. 2 s rejects the slow transition between highway bends.
//
// STARTING VALUES, NOT CALIBRATED. Confirm on the road (Docs/steering-weave.md).
constexpr float    STEER_REVERSAL_DPS     = 2.0f;
constexpr uint32_t STEER_REVERSAL_MAX_MS  = 2000;
constexpr int      STEER_REVERSAL_TRIGGER = 6;      // this many in the window -> trigger
constexpr int      STEER_WINDOW_S         = 60;

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

// ---------------------------------------------------------------
// Operating-domain gate
// ---------------------------------------------------------------
// Vehicle-dynamics fatigue metrics are only valid on open road above
// highway speed. In production the trigger is suppressed below it rather
// than allowed to fire on stop-and-go traffic, turns and signals. Volvo's
// Driver Alert Control uses 65 km/h for the same reason.
//
// PRODUCTION VALUES -- restore these once bench testing is confirmed.
constexpr float MIN_SPEED_KPH_PRODUCTION = 65.0f;
constexpr float GATE_COVERAGE_PRODUCTION = 0.80f;

// BENCH MODE
// ==========
// true  = the gate opens as soon as OBD data is flowing, at any speed.
//         Lets the trigger chain be exercised with the car parked or on a
//         desk. The firmware prints a banner at boot, on every trigger,
//         and sets "gate":"BENCH" in the phone telemetry, so this cannot
//         be shipped by accident.
// false = production. Trigger requires real highway conditions.
//
// >>> SET THIS BACK TO false ONCE THRESHOLDS ARE CONFIRMED ON THE ROAD <<<
constexpr bool GATE_BENCH_MODE = true;

constexpr float MIN_SPEED_KPH = GATE_BENCH_MODE ? 0.0f : MIN_SPEED_KPH_PRODUCTION;
constexpr float GATE_COVERAGE = GATE_BENCH_MODE ? 0.0f : GATE_COVERAGE_PRODUCTION;

// Minimum samples in a window before its statistic is trusted.
constexpr int   MIN_SAMPLES_FOR_STAT = 12;

constexpr int   TRIGGER_COOLDOWN_S   = 30;

// ---------------------------------------------------------------
// Alert behaviour
// ---------------------------------------------------------------
constexpr int DROWSY_VIB_DUTY   = 255;   // 0-255 PWM duty
constexpr int DROWSY_VIB_ON_MS  = 500;
constexpr int DROWSY_VIB_OFF_MS = 300;
constexpr int DROWSY_BUZZ_HZ    = PIEZO_FREQ_HZ;
// While fatigue is detected the buzzer repeats a 1 s beep, 1 s pause --
// clearly an alarm, not one constant tone.
constexpr int DROWSY_BUZZ_ON_MS = 1000;
constexpr int DROWSY_BUZZ_OFF_MS = 1000;

// BEEP command (serial, or any BLE device writing to the Pi result
// characteristic): one beep of this length. Proves the buzzer from a phone.
constexpr uint32_t BEEP_MS = 1000;

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

constexpr char PI_SERVICE_UUID[]     = "DF710001-B5A3-F393-E0A9-E50E24DCCA9E";
constexpr char PI_WAKE_CHAR_UUID[]   = "DF710002-B5A3-F393-E0A9-E50E24DCCA9E";  // notify -> Pi
constexpr char PI_RESULT_CHAR_UUID[] = "DF710003-B5A3-F393-E0A9-E50E24DCCA9E";  // write  <- Pi

constexpr char PHONE_SERVICE_UUID[]     = "DF720001-B5A3-F393-E0A9-E50E24DCCA9E";
constexpr char PHONE_STATUS_CHAR_UUID[] = "DF720002-B5A3-F393-E0A9-E50E24DCCA9E";  // notify -> phone
