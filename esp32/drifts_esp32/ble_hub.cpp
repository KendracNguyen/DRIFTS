#include "ble_hub.h"
#include "config.h"

#if defined(USE_NIMBLE) || __has_include(<NimBLEDevice.h>)
    #include <NimBLEDevice.h>
    using BleServerType = NimBLEServer;
    using BleServiceType = NimBLEService;
    using BleCharType = NimBLECharacteristic;
    using BleServerCallbacksType = NimBLEServerCallbacks;
    using BleCharCallbacksType = NimBLECharacteristicCallbacks;
    using Ble2902Type = NimBLE2902;
#else
    #include <BLEDevice.h>
    #include <BLEServer.h>
    #include <BLEUtils.h>
    #include <BLE2902.h>
    using BleServerType = BLEServer;
    using BleServiceType = BLEService;
    using BleCharType = BLECharacteristic;
    using BleServerCallbacksType = BLEServerCallbacks;
    using BleCharCallbacksType = BLECharacteristicCallbacks;
    using Ble2902Type = BLE2902;
#endif

static BleCharType *wakeChar = nullptr;
static BleCharType *resultChar = nullptr;
static BleCharType *statusChar = nullptr;

static volatile bool piConnected = false;
static volatile bool phoneConnected = false;
static volatile uint32_t lastPiMsgMs = 0;

static String pendingResult = "";
static bool hasResult = false;

class HubServerCallbacks : public BleServerCallbacksType {
    void onConnect(BleServerType *pServer) override {
        // Multi-connection server: track overall state and refresh timestamp
        lastPiMsgMs = millis();
        piConnected = true; // Pi connects first; both can connect
    }

    void onDisconnect(BleServerType *pServer) override {
        // Continue advertising for reconnection
        pServer->startAdvertising();
    }
};

class PiResultCallbacks : public BleCharCallbacksType {
    void onWrite(BleCharType *pChar) override {
        lastPiMsgMs = millis();
        String val = pChar->getValue().c_str();
        val.trim();
        if (val.length() > 0) {
            pendingResult = val;
            hasResult = true;
            Serial.printf("[BLE] Pi wrote result: %s\n", val.c_str());
        }
    }
};

BleHub::BleHub() {}

bool BleHub::init() {
    BLEDevice::init(DEVICE_NAME);
    BleServerType *server = BLEDevice::createServer();
    server->setCallbacks(new HubServerCallbacks());

    // 1. Pi Link Service
    BleServiceType *piService = server->createService(PI_SERVICE_UUID);
    wakeChar = piService->createCharacteristic(
        PI_WAKE_CHAR_UUID,
        BLECharacteristic::PROPERTY_NOTIFY
    );
    wakeChar->addDescriptor(new Ble2902Type());

    resultChar = piService->createCharacteristic(
        PI_RESULT_CHAR_UUID,
        BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
    );
    resultChar->setCallbacks(new PiResultCallbacks());
    piService->start();

    // 2. Phone Link Service
    BleServiceType *phoneService = server->createService(PHONE_SERVICE_UUID);
    statusChar = phoneService->createCharacteristic(
        PHONE_STATUS_CHAR_UUID,
        BLECharacteristic::PROPERTY_NOTIFY
    );
    statusChar->addDescriptor(new Ble2902Type());
    phoneService->start();

    // Advertising
    auto *adv = BLEDevice::getAdvertising();
    adv->addServiceUUID(PI_SERVICE_UUID);
    adv->addServiceUUID(PHONE_SERVICE_UUID);
#ifndef USE_NIMBLE
    adv->setScanResponse(true);
#endif
    BLEDevice::startAdvertising();

    Serial.printf("[BLE] Hub initialized. Advertising as %s\n", DEVICE_NAME);
    return true;
}

void BleHub::wakePi() {
    if (wakeChar) {
        Serial.println("[BLE] Sending WAKE to Pi...");
        wakeChar->setValue("WAKE");
        wakeChar->notify();
    }
}

void BleHub::setPiIdle() {
    if (wakeChar) {
        wakeChar->setValue("IDLE");
        wakeChar->notify();
    }
}

bool BleHub::isPiConnected() const {
    return piConnected;
}

bool BleHub::isPhoneConnected() const {
    return phoneConnected;
}

bool BleHub::hasNewResult() {
    return hasResult;
}

String BleHub::popResult() {
    hasResult = false;
    String res = pendingResult;
    pendingResult = "";
    return res;
}

void BleHub::pushPhoneStatus(float rpm, float speed, float throttle,
                           const char *drvStatus, const char *piStatus,
                           bool alertActive) {
    if (!statusChar) return;

    char jsonBuf[128];
    snprintf(jsonBuf, sizeof(jsonBuf),
             "{\"rpm\":%.0f,\"spd\":%.1f,\"thr\":%.1f,\"drv\":\"%s\",\"pi\":\"%s\",\"alert\":%s}",
             rpm, speed, throttle, drvStatus, piStatus, alertActive ? "true" : "false");

    statusChar->setValue(jsonBuf);
    statusChar->notify();
}

void BleHub::update() {
    // Check failsafe: if Pi has been connected but silent for longer than FAILSAFE_MS
    if (piConnected && (millis() - lastPiMsgMs > FAILSAFE_MS)) {
        // Optional: track watchdog or flag failsafe
    }
}

uint32_t BleHub::getLastPiMessageTime() const {
    return lastPiMsgMs;
}
