/*
  DRIFTS v3 - ESP32 controller
  ============================
  Data path:
    BLE ELM327 dongle  --(BLE central)-->  ESP32
    ESP32  --(BLE peripheral)-->  Raspberry Pi Zero 2 W  (WAKE / result)
    ESP32  --(BLE peripheral)-->  phone app              (telemetry)

  The ESP32 holds three BLE connections at once. That requires NimBLE
  with MYNEWT_VAL_BLE_MAX_CONNECTIONS >= 4 and the "Huge APP" partition
  scheme. See README_BLE_OBD.md in this folder.

  Serial test commands (115200 baud):
    WAKE          trigger a Pi analysis cycle by hand
    DROWSY        simulate a DROWSY result from the Pi
    NOT_DROWSY    simulate a NOT_DROWSY result
    STOP          stop the alert
    BRAKE         inject one harsh-brake event
    STATUS        print the current state
*/

#include "config.h"
#include "actuators.h"
#include "ble_hub.h"
#include "obd_ble.h"
#include "trigger.h"
#include "imu.h"

static DrivingTrigger drivingTrigger;

enum class SystemState : uint8_t { Idle, WaitingPi, Alerting };

static SystemState state = SystemState::Idle;
static uint32_t stateStartMs = 0;
static uint32_t lastPhoneMs = 0;
static const char *piStatus = "idle";

static void enterIdle(const char *why) {
    if (state == SystemState::Alerting) stopAll();
    state = SystemState::Idle;
    stateStartMs = millis();
    piStatus = "idle";
    if (why != nullptr) Serial.printf("[MAIN] -> IDLE (%s)\n", why);
}

static void enterAlerting() {
    startAlert();
    state = SystemState::Alerting;
    stateStartMs = millis();
    piStatus = "drowsy";
    Serial.println("[MAIN] -> ALERTING");
}

void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println("\n==================================");
    Serial.println("  DRIFTS v3 - ESP32 (BLE OBD)");
    Serial.println("==================================");

    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    initActuators();
    Serial.println("[MAIN] Actuators ready");

    // The IMU supplies harsh-braking events. Without it that condition is
    // disabled rather than silently reporting zero.
    const bool imuOk = Motion.begin();
    drivingTrigger.setAccelAvailable(imuOk);

    // NimBLEDevice::init() happens inside the hub; the dongle client
    // reuses the same stack, so the hub must come first.
    Hub.init();
    Obd.begin();

    if (GATE_BENCH_MODE) {
        Serial.println();
        Serial.println("  ***********************************************");
        Serial.println("  *  GATE_BENCH_MODE = true                     *");
        Serial.println("  *  Speed gate DISABLED. The trigger will fire *");
        Serial.println("  *  at any speed, including parked.            *");
        Serial.println("  *  NOT VALID FOR ROAD DATA. Set false in      *");
        Serial.println("  *  config.h once thresholds are confirmed.    *");
        Serial.println("  ***********************************************");
        Serial.println();
    } else {
        Serial.printf("[MAIN] Speed gate active: %.0f km/h over %.0f%% of the window\n",
                      MIN_SPEED_KPH, GATE_COVERAGE * 100.0f);
    }

    Serial.println("[MAIN] Ready. Commands: WAKE / DROWSY / NOT_DROWSY / STOP / BRAKE / STATUS");
    Serial.println("==================================\n");
    stateStartMs = millis();
}

static void printStatus() {
    const TriggerSnapshot &s = drivingTrigger.snapshot();
    Serial.printf("[STATUS] obd=%s layout=%s %.1f Hz | rpm %.0f (sd %.0f, n %d) "
                  "spd %.0f (sd %.1f, n %d) thr %.0f (sd %.1f, n %d) | "
                  "brakes %d accel=%d gate=%s | pi=%d phone=%d | nodata %lu err %lu ovf %lu\n",
                  Obd.stateName(), Obd.layoutName(), Obd.achievedHz(),
                  s.rpm, s.rpm_stddev, s.rpm_samples,
                  s.speed, s.speed_stddev, s.speed_samples,
                  s.throttle, s.throttle_stddev, s.throttle_samples,
                  s.hard_brake_count, (int)s.accel_available,
                  GATE_BENCH_MODE ? "BENCH" : (s.gate_open ? "open" : "closed"),
                  (int)Hub.isPiConnected(), (int)Hub.isPhoneConnected(),
                  (unsigned long)Obd.noDataCount(), (unsigned long)Obd.errorCount(),
                  (unsigned long)Obd.rxOverflows());
}

static void handleSerial() {
    if (!Serial.available()) return;
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.isEmpty()) return;

    if (cmd.equalsIgnoreCase("WAKE")) {
        Hub.wakePi();
        state = SystemState::WaitingPi;
        stateStartMs = millis();
        piStatus = "analyzing";
        Serial.println("[CMD] Manual WAKE");
    } else if (cmd.equalsIgnoreCase("DROWSY")) {
        enterAlerting();
    } else if (cmd.equalsIgnoreCase("NOT_DROWSY")) {
        piStatus = "not_drowsy";
        enterIdle("simulated NOT_DROWSY");
    } else if (cmd.equalsIgnoreCase("STOP")) {
        enterIdle("manual STOP");
    } else if (cmd.equalsIgnoreCase("BRAKE")) {
        drivingTrigger.pushBrakeEvent(millis());
        Serial.println("[CMD] Injected brake event");
    } else if (cmd.equalsIgnoreCase("STATUS")) {
        printStatus();
    }
}

void loop() {
    const uint32_t now = millis();

    handleSerial();

    // 1. Service the dongle link and feed any new reading into the trigger.
    Obd.service();
    ObdSlot slot;
    while (Obd.takeFresh(&slot)) {
        const ObdValues &v = Obd.values();
        switch (slot) {
            case SLOT_RPM:      drivingTrigger.pushRpm(v.rpm, v.ts_ms[SLOT_RPM]); break;
            case SLOT_SPEED:    drivingTrigger.pushSpeed(v.speed_kph, v.ts_ms[SLOT_SPEED]); break;
            case SLOT_THROTTLE: drivingTrigger.pushThrottle(v.throttle_pct, v.ts_ms[SLOT_THROTTLE]); break;
            default: break;   // engine load is carried for context, not scored
        }
    }

    // 2. Harsh braking comes from the accelerometer.
    if (Motion.service(now)) {
        drivingTrigger.pushBrakeEvent(now);
    }

    // 3. Evaluate, and wake the Pi on a rising edge.
    const bool fired = drivingTrigger.evaluate(now);
    const TriggerSnapshot &snap = drivingTrigger.snapshot();

    if (fired && state == SystemState::Idle) {
        Serial.printf("[TRIGGER]%s %s -> waking Pi\n",
                      GATE_BENCH_MODE ? " [BENCH]" : "", snap.reason);
        Hub.wakePi();
        state = SystemState::WaitingPi;
        stateStartMs = now;
        piStatus = "analyzing";
    }

    // 4. Results from the Pi.
    while (Hub.hasResult()) {
        String result = Hub.popResult();
        Serial.printf("[MAIN] Pi result: %s\n", result.c_str());

        if (result.equalsIgnoreCase("DROWSY")) {
            enterAlerting();
        } else if (result.equalsIgnoreCase("NOT_DROWSY")) {
            piStatus = "not_drowsy";
            Hub.setPiIdle();
            enterIdle("Pi reported NOT_DROWSY");
        }
    }

    // 5. The Pi had its chance and did not answer.
    if (state == SystemState::WaitingPi && now - stateStartMs > PI_RESPONSE_TIMEOUT_MS) {
        enterIdle("Pi response timed out");
    }

    // 6. Fail-safe. Restored from v1, where it worked and where losing it
    //    meant a crashed Pi left the motor and buzzer running indefinitely.
    //
    //    It applies ONLY while the Pi is connected. Without this guard a
    //    bench test with no Pi attached kills the alert instantly, because
    //    piSilentForMs() returns UINT32_MAX when the Pi has never been
    //    seen -- which reads as a dead motor driver, not as a fail-safe.
    //    MAX_ALERT_MS still bounds the alert in that case.
    if (state == SystemState::Alerting) {
        if (Hub.isPiConnected() && Hub.piSilentForMs(now) > FAILSAFE_MS) {
            enterIdle("fail-safe: Pi silent");
        } else if (now - stateStartMs > MAX_ALERT_MS) {
            // An alert must not latch even if the Pi is alive but never
            // sends NOT_DROWSY.
            enterIdle("maximum alert duration reached");
        }
    }

    updateActuators();
    digitalWrite(LED_PIN, Hub.isPiConnected() ? HIGH : LOW);

    // 7. Telemetry to the phone.
    if (now - lastPhoneMs >= PHONE_UPDATE_MS) {
        lastPhoneMs = now;
        char json[224];
        snprintf(json, sizeof(json),
                 "{\"rpm\":%.0f,\"spd\":%.1f,\"thr\":%.1f,\"g\":%.2f,"
                 "\"obd\":\"%s\",\"hz\":%.1f,\"gate\":\"%s\",\"drv\":\"%s\","
                 "\"pi\":\"%s\",\"alert\":%s}",
                 snap.rpm, snap.speed, snap.throttle, Motion.longitudinalG(),
                 Obd.stateName(), Obd.achievedHz(),
                 GATE_BENCH_MODE ? "BENCH" : (snap.gate_open ? "open" : "closed"),
                 snap.triggered ? "TRIGGERED" : "OK",
                 piStatus, isAlertActive() ? "true" : "false");
        Hub.pushPhoneStatus(json);
    }

    delay(5);
}
