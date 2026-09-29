/*
  DRIFTS v2 ESP32 Firmware
  ========================
  - Passively sniffs OBD-II CAN bus frames via SN65HVD230 and TWAI driver
  - Evaluates rolling statistical thresholds (braking, RPM stddev, speed stddev, throttle jitter)
  - Sends WAKE notification to Raspberry Pi Zero 2 W over BLE Pi Link service
  - Receives DROWSY / NOT_DROWSY classification from Pi and triggers haptic/audio alert
  - Publishes live telemetry & status to Mobile App over BLE Phone Link service
*/

#include "config.h"
#include "actuators.h"
#include "obd_reader.h"
#include "trigger.h"
#include "ble_hub.h"

static ObdReader obd;
static DrivingTrigger drivingTrigger;
static BleHub bleHub;

// States
enum class SystemState {
    IDLE,
    WAITING_PI,
    ALERTING
};

static SystemState state = SystemState::IDLE;
static uint32_t stateStartTimeMs = 0;
static const uint32_t PI_RESPONSE_TIMEOUT_MS = 15000; // Allow Pi up to 15s for analysis

static uint32_t lastPhoneUpdateMs = 0;
static char currentPiStatus[16] = "idle";

void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println("\n==================================");
    Serial.println("  DRIFTS v2 — ESP32 Controller");
    Serial.println("==================================");

    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    // 1. Actuators
    initActuators();
    Serial.println("[MAIN] Actuators initialized");

    // 2. OBD-II CAN (TWAI)
    if (!obd.init()) {
        Serial.println("[WARN] CAN TWAI init failed; simulation mode available");
    }

    // 3. BLE Hub (Pi Link + Phone Link)
    bleHub.init();

    Serial.println("[MAIN] System ready. Serial test commands available:");
    Serial.println("  WAKE               -> trigger Pi manually");
    Serial.println("  SIM <rpm> <spd> <thr> -> inject OBD data");
    Serial.println("  STOP               -> stop alert");
    Serial.println("==================================\n");
}

void handleSerialCommands() {
    if (!Serial.available()) return;
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.length() == 0) return;

    if (cmd.equalsIgnoreCase("WAKE")) {
        Serial.println("[CMD] Manual WAKE triggered");
        bleHub.wakePi();
        state = SystemState::WAITING_PI;
        stateStartTimeMs = millis();
        strncpy(currentPiStatus, "analyzing", sizeof(currentPiStatus) - 1);
    } else if (cmd.equalsIgnoreCase("STOP")) {
        Serial.println("[CMD] Manual STOP");
        stopAll();
        state = SystemState::IDLE;
        strncpy(currentPiStatus, "idle", sizeof(currentPiStatus) - 1);
    } else if (cmd.startsWith("SIM") || cmd.startsWith("sim")) {
        float rpm = 0, spd = 0, thr = 0;
        int parsed = sscanf(cmd.c_str() + 3, "%f %f %f", &rpm, &spd, &thr);
        if (parsed >= 2) {
            obd.injectValues(rpm, spd, thr);
            Serial.printf("[CMD] Injected: RPM=%.0f, SPD=%.1f, THR=%.1f\n", rpm, spd, thr);
        }
    } else if (cmd.equalsIgnoreCase("DROWSY")) {
        Serial.println("[CMD] Simulated DROWSY from Pi");
        startAlert();
        state = SystemState::ALERTING;
        strncpy(currentPiStatus, "drowsy", sizeof(currentPiStatus) - 1);
    } else if (cmd.equalsIgnoreCase("NOT_DROWSY")) {
        Serial.println("[CMD] Simulated NOT_DROWSY from Pi");
        stopAll();
        state = SystemState::IDLE;
        strncpy(currentPiStatus, "not_drowsy", sizeof(currentPiStatus) - 1);
    }
}

void loop() {
    uint32_t now = millis();

    // 1. Check serial test commands
    handleSerialCommands();

    // 2. Poll OBD-II CAN frames
    obd.poll();

    float rpm = obd.getRPM();
    float speed = obd.getSpeed();
    float throttle = obd.getThrottle();
    float brake_decel = obd.getBrakeDecel();

    // 3. Evaluate Driving Behavior
    bool triggered = drivingTrigger.evaluate(rpm, speed, throttle, brake_decel, now);
    TriggerSnapshot snap = drivingTrigger.getSnapshot();

    if (triggered && state == SystemState::IDLE) {
        Serial.printf("[TRIGGER] Anomaly detected: %s! Waking Pi...\n", snap.trigger_reason);
        bleHub.wakePi();
        state = SystemState::WAITING_PI;
        stateStartTimeMs = now;
        strncpy(currentPiStatus, "analyzing", sizeof(currentPiStatus) - 1);
    }

    // 4. Handle Pi Results from BLE
    if (bleHub.hasNewResult()) {
        String result = bleHub.popResult();
        Serial.printf("[MAIN] Processing Pi result: %s\n", result.c_str());

        if (result.equalsIgnoreCase("DROWSY")) {
            startAlert();
            state = SystemState::ALERTING;
            strncpy(currentPiStatus, "drowsy", sizeof(currentPiStatus) - 1);
        } else if (result.equalsIgnoreCase("NOT_DROWSY")) {
            stopAll();
            state = SystemState::IDLE;
            strncpy(currentPiStatus, "not_drowsy", sizeof(currentPiStatus) - 1);
            bleHub.setPiIdle();
        }
    }

    // 5. Check timeout when waiting for Pi
    if (state == SystemState::WAITING_PI && (now - stateStartTimeMs > PI_RESPONSE_TIMEOUT_MS)) {
        Serial.println("[WARN] Pi response timed out; returning to IDLE");
        state = SystemState::IDLE;
        strncpy(currentPiStatus, "idle", sizeof(currentPiStatus) - 1);
    }

    // 6. Update Actuators (haptic motor & buzzer PWM phases)
    updateActuators();

    // 7. Update status LED (lit if Pi is connected)
    digitalWrite(LED_PIN, bleHub.isPiConnected() ? HIGH : LOW);

    // 8. Periodically push JSON status to Mobile App via Phone Link BLE service (~1 Hz)
    if (now - lastPhoneUpdateMs >= 1000) {
        lastPhoneUpdateMs = now;
        const char *drvStatus = snap.triggered ? "TRIGGERED" : "OK";
        bleHub.pushPhoneStatus(rpm, speed, throttle, drvStatus, currentPiStatus, isAlertActive());
    }

    delay(10);
}
