/*
  DRIFTS ESP32 actuator controller
  --------------------------------
  Receives text commands from the Raspberry Pi over Bluetooth LE
  (Nordic UART Service) and drives the vibration motor and passive buzzer.

  Board:  ESP32 (Arduino core 3.x - Tools > Board > esp32 by Espressif, v3.0 or newer)
  Pins:   change MOTOR_PIN / BUZZER_PIN below to match your wiring.
          Keep the transistor driver + flyback diode circuits between the
          ESP32 pins and the motor/buzzer, exactly as with the Pi.

  Commands (one per line, from the Pi):
    PING                         -> PONG           heartbeat
    STOP                         -> OK STOP        everything off
    VIB  <duty> <on_ms> <off_ms> -> OK VIB         duty 0-255, off_ms 0 = continuous, duty 0 = off
    BUZZ <hz>   <on_ms> <off_ms> -> OK BUZZ        hz 100-10000, off_ms 0 = continuous, hz 0 = off
  Bad input replies "ERR <reason>".

  Fail-safe: if nothing arrives from the Pi for FAILSAFE_MS (the Pi pings
  every second), or the Pi disconnects, all outputs switch off. The ESP32
  sends "FAILSAFE" so the Pi re-sends its current level when it recovers.
*/

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// ---------- settings ----------
#define DEVICE_NAME "DRIFTS-ESP32"   // must match [ble] device_name in config.toml
const int MOTOR_PIN  = 26;           // PWM to motor transistor base/gate
const int BUZZER_PIN = 25;           // tone to buzzer transistor base/gate
const int LED_PIN    = 2;            // on-board LED on most DevKits: lit = Pi connected
const uint32_t FAILSAFE_MS = 3000;

// Nordic UART Service UUIDs
#define NUS_SERVICE "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define NUS_RX      "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  // Pi writes here
#define NUS_TX      "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  // we notify here

// ---------- outputs ----------
struct Pulse {
  bool active = false;
  int value = 0;          // duty (motor) or frequency (buzzer)
  uint32_t onMs = 0, offMs = 0;
  bool phaseOn = false;
  uint32_t phaseStart = 0;
};

Pulse vib, buzz;

void setMotor(int duty)  { ledcWrite(MOTOR_PIN, duty); }
void setBuzzer(int hz)   { ledcWriteTone(BUZZER_PIN, hz); }   // 0 = silent

void startPulse(Pulse &p, int value, uint32_t onMs, uint32_t offMs, void (*out)(int)) {
  if (value <= 0) { p.active = false; out(0); return; }
  p.active = true; p.value = value; p.onMs = onMs; p.offMs = offMs;
  p.phaseOn = true; p.phaseStart = millis();
  out(value);
}

void updatePulse(Pulse &p, void (*out)(int)) {
  if (!p.active || p.offMs == 0) return;   // off, or continuous
  uint32_t now = millis();
  uint32_t len = p.phaseOn ? p.onMs : p.offMs;
  if (now - p.phaseStart >= len) {
    p.phaseOn = !p.phaseOn;
    p.phaseStart = now;
    out(p.phaseOn ? p.value : 0);
  }
}

void stopAll() {
  startPulse(vib, 0, 0, 0, setMotor);
  startPulse(buzz, 0, 0, 0, setBuzzer);
}

// ---------- BLE ----------
BLECharacteristic *txChar = nullptr;
volatile bool piConnected = false;
volatile bool justDisconnected = false;
volatile uint32_t lastMsgMs = 0;
QueueHandle_t lineQueue;             // complete lines from the BLE task -> loop()
const int LINE_MAX = 48;

void reply(const String &msg) {
  Serial.println("<- " + msg);
  if (piConnected && txChar) {
    txChar->setValue(msg.c_str());
    txChar->notify();
  }
}

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *) override {
    piConnected = true;
    lastMsgMs = millis();
  }
  void onDisconnect(BLEServer *) override {
    piConnected = false;
    justDisconnected = true;
  }
};

class RxCallbacks : public BLECharacteristicCallbacks {
  char buf[LINE_MAX];
  int len = 0;
  void onWrite(BLECharacteristic *c) override {
    auto v = c->getValue();
    lastMsgMs = millis();
    for (size_t i = 0; i < v.length(); i++) {
      char ch = v[i];
      if (ch == '\n' || ch == '\r') {
        if (len > 0) { buf[len] = 0; xQueueSend(lineQueue, buf, 0); len = 0; }
      } else if (len < LINE_MAX - 1) {
        buf[len++] = ch;
      }
    }
  }
};

// ---------- command handling ----------
void handleLine(const char *line) {
  char cmd[12] = {0};
  long a = 0, b = 0, c = 0;
  int n = sscanf(line, "%11s %ld %ld %ld", cmd, &a, &b, &c);
  if (n < 1) return;

  if (strcmp(cmd, "PING") == 0) {
    reply("PONG");
  } else if (strcmp(cmd, "STOP") == 0) {
    stopAll();
    reply("OK STOP");
  } else if (strcmp(cmd, "VIB") == 0) {
    if (n < 2 || a < 0 || a > 255 || b < 0 || c < 0) { reply("ERR VIB <0-255> <on_ms> <off_ms>"); return; }
    startPulse(vib, a, n >= 3 ? b : 0, n >= 4 ? c : 0, setMotor);
    reply("OK VIB");
  } else if (strcmp(cmd, "BUZZ") == 0) {
    if (n < 2 || (a != 0 && (a < 100 || a > 10000)) || b < 0 || c < 0) {
      reply("ERR BUZZ <0|100-10000> <on_ms> <off_ms>"); return;
    }
    startPulse(buzz, a, n >= 3 ? b : 0, n >= 4 ? c : 0, setBuzzer);
    reply("OK BUZZ");
  } else {
    reply(String("ERR unknown ") + cmd);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);

  ledcAttach(MOTOR_PIN, 20000, 8);   // 20 kHz PWM (inaudible), 8-bit duty
  ledcAttach(BUZZER_PIN, 2000, 8);   // frequency is changed by ledcWriteTone
  stopAll();

  lineQueue = xQueueCreate(16, LINE_MAX);

  BLEDevice::init(DEVICE_NAME);
  BLEServer *server = BLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());

  BLEService *svc = server->createService(NUS_SERVICE);
  txChar = svc->createCharacteristic(NUS_TX, BLECharacteristic::PROPERTY_NOTIFY);
  txChar->addDescriptor(new BLE2902());
  BLECharacteristic *rx = svc->createCharacteristic(
      NUS_RX, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  rx->setCallbacks(new RxCallbacks());
  svc->start();

  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(NUS_SERVICE);
  adv->setScanResponse(true);
  BLEDevice::startAdvertising();
  Serial.println("DRIFTS ESP32 advertising as " DEVICE_NAME);
}

void loop() {
  char line[LINE_MAX];
  while (xQueueReceive(lineQueue, line, 0) == pdTRUE) {
    if (strcmp(line, "PING") != 0) Serial.println(String("-> ") + line);
    handleLine(line);
  }

  if (justDisconnected) {
    justDisconnected = false;
    stopAll();
    Serial.println("Pi disconnected: outputs off, advertising again");
    delay(200);
    BLEDevice::startAdvertising();
  }

  static bool failsafeTripped = false;
  uint32_t last = lastMsgMs;           // read before millis() so the subtraction can't underflow
  if (piConnected && millis() - last > FAILSAFE_MS) {
    if (!failsafeTripped) {
      stopAll();
      Serial.println("No message from Pi: fail-safe, outputs off");
      reply("FAILSAFE");   // tells the Pi to resend its level once it recovers
      failsafeTripped = true;
    }
  } else {
    failsafeTripped = false;
  }

  updatePulse(vib, setMotor);
  updatePulse(buzz, setBuzzer);
  digitalWrite(LED_PIN, piConnected ? HIGH : LOW);
  delay(5);
}
