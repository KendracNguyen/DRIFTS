#include "ble_hub.h"
#include "config.h"

#include <NimBLEDevice.h>

BleHub Hub;

static NimBLEServer *g_server = nullptr;
static NimBLECharacteristic *g_wakeChar = nullptr;    // notify -> Pi
static NimBLECharacteristic *g_resultChar = nullptr;  // write  <- Pi
static NimBLECharacteristic *g_statusChar = nullptr;  // notify -> phone

// Connection handles of the subscribed peers, or BLE_HS_CONN_HANDLE_NONE.
static volatile uint16_t g_piHandle = BLE_HS_CONN_HANDLE_NONE;
static volatile uint16_t g_phoneHandle = BLE_HS_CONN_HANDLE_NONE;
static volatile uint32_t g_lastPiMsgMs = 0;
static volatile bool g_piEverSeen = false;

// Result handoff. A FreeRTOS queue rather than a shared String: the write
// callback runs on the NimBLE host task and loop() reads it, and a heap
// object handed between tasks without synchronisation is a real crash,
// not a theoretical one.
static constexpr size_t RESULT_MAX = 24;
static QueueHandle_t g_resultQueue = nullptr;

class HubServerCallbacks : public NimBLEServerCallbacks {
    void onDisconnect(NimBLEServer *, NimBLEConnInfo &connInfo, int reason) override {
        const uint16_t h = connInfo.getConnHandle();
        if (h == g_piHandle) {
            g_piHandle = BLE_HS_CONN_HANDLE_NONE;
            Serial.println("[BLE] Pi disconnected");
        }
        if (h == g_phoneHandle) {
            g_phoneHandle = BLE_HS_CONN_HANDLE_NONE;
            Serial.println("[BLE] Phone disconnected");
        }
        // Without this the peer can never come back.
        NimBLEDevice::startAdvertising();
    }
};
static HubServerCallbacks g_serverCallbacks;

// Peers identify themselves by what they subscribe to.
class WakeCharCallbacks : public NimBLECharacteristicCallbacks {
    void onSubscribe(NimBLECharacteristic *, NimBLEConnInfo &connInfo, uint16_t subValue) override {
        if (subValue > 0) {
            g_piHandle = connInfo.getConnHandle();
            g_lastPiMsgMs = millis();
            g_piEverSeen = true;
            Serial.printf("[BLE] Pi subscribed (handle %u)\n", (unsigned)g_piHandle);
        } else if (connInfo.getConnHandle() == g_piHandle) {
            g_piHandle = BLE_HS_CONN_HANDLE_NONE;
        }
    }
};
static WakeCharCallbacks g_wakeCallbacks;

class StatusCharCallbacks : public NimBLECharacteristicCallbacks {
    void onSubscribe(NimBLECharacteristic *, NimBLEConnInfo &connInfo, uint16_t subValue) override {
        if (subValue > 0) {
            g_phoneHandle = connInfo.getConnHandle();
            Serial.printf("[BLE] Phone subscribed (handle %u)\n", (unsigned)g_phoneHandle);
        } else if (connInfo.getConnHandle() == g_phoneHandle) {
            g_phoneHandle = BLE_HS_CONN_HANDLE_NONE;
        }
    }
};
static StatusCharCallbacks g_statusCallbacks;

class ResultCharCallbacks : public NimBLECharacteristicCallbacks {
    void onWrite(NimBLECharacteristic *chr, NimBLEConnInfo &connInfo) override {
        g_lastPiMsgMs = millis();
        g_piEverSeen = true;
        if (g_piHandle == BLE_HS_CONN_HANDLE_NONE) g_piHandle = connInfo.getConnHandle();

        String value(chr->getValue().c_str());
        value.trim();
        if (value.length() == 0) return;

        // PING is the fail-safe heartbeat, not a classification result.
        if (value.equalsIgnoreCase("PING")) return;

        char buf[RESULT_MAX] = {0};
        strncpy(buf, value.c_str(), RESULT_MAX - 1);
        if (g_resultQueue != nullptr) {
            xQueueSend(g_resultQueue, buf, 0);
        }
    }
};
static ResultCharCallbacks g_resultCallbacks;

bool BleHub::init() {
    g_resultQueue = xQueueCreate(4, RESULT_MAX);

    NimBLEDevice::init(DEVICE_NAME);
    NimBLEDevice::setMTU(247);

    g_server = NimBLEDevice::createServer();
    g_server->setCallbacks(&g_serverCallbacks);
    // Keep serving the phone and the Pi even if one of them drops.
    g_server->advertiseOnDisconnect(true);

    NimBLEService *piService = g_server->createService(PI_SERVICE_UUID);
    g_wakeChar = piService->createCharacteristic(PI_WAKE_CHAR_UUID, NIMBLE_PROPERTY::NOTIFY);
    g_wakeChar->setCallbacks(&g_wakeCallbacks);
    g_resultChar = piService->createCharacteristic(
        PI_RESULT_CHAR_UUID, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
    g_resultChar->setCallbacks(&g_resultCallbacks);
    piService->start();

    NimBLEService *phoneService = g_server->createService(PHONE_SERVICE_UUID);
    g_statusChar = phoneService->createCharacteristic(PHONE_STATUS_CHAR_UUID, NIMBLE_PROPERTY::NOTIFY);
    g_statusChar->setCallbacks(&g_statusCallbacks);
    phoneService->start();

    // Both services must be started BEFORE advertising begins, or a later
    // client connect() aborts advertising. See NimBLE-Arduino issue #298.
    NimBLEAdvertising *adv = NimBLEDevice::getAdvertising();
    adv->addServiceUUID(PI_SERVICE_UUID);
    adv->addServiceUUID(PHONE_SERVICE_UUID);
    adv->enableScanResponse(true);
    NimBLEDevice::startAdvertising();

    Serial.printf("[BLE] Hub up, advertising as %s\n", DEVICE_NAME);
    return true;
}

void BleHub::wakePi() {
    if (g_wakeChar == nullptr || g_piHandle == BLE_HS_CONN_HANDLE_NONE) return;
    g_wakeChar->setValue("WAKE");
    g_wakeChar->notify();
    Serial.println("[BLE] WAKE -> Pi");
}

void BleHub::setPiIdle() {
    if (g_wakeChar == nullptr || g_piHandle == BLE_HS_CONN_HANDLE_NONE) return;
    g_wakeChar->setValue("IDLE");
    g_wakeChar->notify();
}

bool BleHub::isPiConnected() const {
    return g_piHandle != BLE_HS_CONN_HANDLE_NONE;
}

bool BleHub::isPhoneConnected() const {
    return g_phoneHandle != BLE_HS_CONN_HANDLE_NONE;
}

bool BleHub::hasResult() const {
    return g_resultQueue != nullptr && uxQueueMessagesWaiting(g_resultQueue) > 0;
}

String BleHub::popResult() {
    char buf[RESULT_MAX] = {0};
    if (g_resultQueue != nullptr && xQueueReceive(g_resultQueue, buf, 0) == pdTRUE) {
        return String(buf);
    }
    return String("");
}

uint32_t BleHub::piSilentForMs(uint32_t now_ms) const {
    if (!g_piEverSeen) return UINT32_MAX;
    const uint32_t last = g_lastPiMsgMs;
    return now_ms - last;
}

void BleHub::pushPhoneStatus(const char *json) {
    if (g_statusChar == nullptr || json == nullptr) return;
    g_statusChar->setValue((uint8_t *)json, strlen(json));
    if (g_phoneHandle != BLE_HS_CONN_HANDLE_NONE) g_statusChar->notify();
}
