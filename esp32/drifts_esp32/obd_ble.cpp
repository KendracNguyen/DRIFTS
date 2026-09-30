#include "obd_ble.h"
#include "config.h"

#include <NimBLEDevice.h>
#include <ELMduino.h>
#include <string.h>

ObdBle Obd;

// ---------------------------------------------------------------
// GATT layouts used by ELM327 BLE dongles.
//
// There is no standard here. The byte stream is identical ELM327 AT
// dialogue in every case; only the discovery differs. Ordered most
// common first. Note the HM-10 layout uses ONE characteristic for both
// notify and write.
// ---------------------------------------------------------------
struct GattLayout {
    const char *name;
    const char *service;
    const char *notifyChar;
    const char *writeChar;
};

static const GattLayout LAYOUTS[] = {
    {"FFF0 (generic ELM327 BLE)",
     "0000fff0-0000-1000-8000-00805f9b34fb",
     "0000fff1-0000-1000-8000-00805f9b34fb",
     "0000fff2-0000-1000-8000-00805f9b34fb"},
    {"FFE0 (HM-10 style, shared char)",
     "0000ffe0-0000-1000-8000-00805f9b34fb",
     "0000ffe1-0000-1000-8000-00805f9b34fb",
     "0000ffe1-0000-1000-8000-00805f9b34fb"},
    {"18F0 (Vgate iCar)",
     "000018f0-0000-1000-8000-00805f9b34fb",
     "00002af0-0000-1000-8000-00805f9b34fb",
     "00002af1-0000-1000-8000-00805f9b34fb"},
    {"Nordic UART",
     "6e400001-b5a3-f393-e0a9-e50e24dcca9e",
     "6e400003-b5a3-f393-e0a9-e50e24dcca9e",
     "6e400002-b5a3-f393-e0a9-e50e24dcca9e"},
};
static constexpr size_t LAYOUT_COUNT = sizeof(LAYOUTS) / sizeof(LAYOUTS[0]);

// Generic Access / Generic Attribute / Device Information / Battery.
// Never the data channel, so excluded from the fallback probe.
static bool isStandardService(const NimBLEUUID &uuid) {
    static const uint16_t kStandard[] = {0x1800, 0x1801, 0x180A, 0x180F};
    for (uint16_t s : kStandard) {
        if (uuid == NimBLEUUID(s)) return true;
    }
    return false;
}

// ---------------------------------------------------------------
// NimBLE plumbing
// ---------------------------------------------------------------
static NimBLEClient *g_client = nullptr;
static NimBLERemoteCharacteristic *g_notifyChar = nullptr;
static NimBLERemoteCharacteristic *g_writeChar = nullptr;
static NimBLEAddress g_foundAddress;
static volatile bool g_haveCandidate = false;
static volatile bool g_linkDropped = false;

static ELM327 g_elm;

static void notifyCallback(NimBLERemoteCharacteristic *, uint8_t *data, size_t len, bool) {
    Obd.onNotifyBytes(data, len);
}

// BleStream write sink. Uses write-without-response when the dongle
// offers it: no ATT acknowledgement round-trip, which matters when the
// radio is already carrying three connections.
static bool bleWriteSink(const uint8_t *data, size_t len, void *) {
    if (g_writeChar == nullptr) return false;
    const bool withResponse = !g_writeChar->canWriteNoResponse();
    return g_writeChar->writeValue(data, len, withResponse);
}

class DongleClientCallbacks : public NimBLEClientCallbacks {
    void onDisconnect(NimBLEClient *, int reason) override {
        Serial.printf("[OBD] Dongle disconnected (reason %d)\n", reason);
        g_linkDropped = true;
    }
};
static DongleClientCallbacks g_clientCallbacks;

static bool nameLooksLikeDongle(const std::string &name) {
    if (name.empty()) return false;
    String lower(name.c_str());
    lower.toLowerCase();
    for (size_t i = 0; i < OBD_NAME_HINT_COUNT; i++) {
        if (lower.indexOf(OBD_NAME_HINTS[i]) >= 0) return true;
    }
    return false;
}

class DongleScanCallbacks : public NimBLEScanCallbacks {
    void onResult(const NimBLEAdvertisedDevice *dev) override {
        if (g_haveCandidate) return;

        bool match = false;
        if (strlen(OBD_MAC_ADDRESS) > 0) {
            match = (dev->getAddress() == NimBLEAddress(OBD_MAC_ADDRESS, BLE_ADDR_PUBLIC));
        } else {
            match = nameLooksLikeDongle(dev->getName());
            // Some dongles advertise the service UUID but no useful name.
            if (!match) {
                for (size_t i = 0; i < LAYOUT_COUNT; i++) {
                    if (dev->isAdvertisingService(NimBLEUUID(LAYOUTS[i].service))) {
                        match = true;
                        break;
                    }
                }
            }
        }

        if (match) {
            Serial.printf("[OBD] Candidate: %s (%s)\n",
                          dev->getName().c_str(), dev->getAddress().toString().c_str());
            g_foundAddress = dev->getAddress();
            g_haveCandidate = true;
            NimBLEDevice::getScan()->stop();
        }
    }
};
static DongleScanCallbacks g_scanCallbacks;

// ---------------------------------------------------------------
// ObdBle
// ---------------------------------------------------------------
ObdBle::ObdBle()
    : _state(ObdState::Idle), _slot(0), _fresh(false), _freshSlot(SLOT_RPM),
      _stateSince(0), _backoffUntil(0), _lastSuccessMs(0), _lastPollMs(0),
      _windowStart(0), _windowCount(0), _hz(0.0f),
      _noData(0), _errors(0), _layoutName("none") {}

const char *ObdBle::stateName() const {
    switch (_state) {
        case ObdState::Idle:         return "idle";
        case ObdState::Scanning:     return "scanning";
        case ObdState::Connecting:   return "connecting";
        case ObdState::Discovering:  return "discovering";
        case ObdState::Initialising: return "initialising";
        case ObdState::Polling:      return "polling";
        case ObdState::Backoff:      return "backoff";
    }
    return "?";
}

bool ObdBle::isLinkUp() const {
    return g_client != nullptr && g_client->isConnected();
}

void ObdBle::onNotifyBytes(const uint8_t *data, size_t len) {
    _stream.ingest(data, len);
}

void ObdBle::onDongleDisconnected() {
    g_linkDropped = true;
}

void ObdBle::begin() {
    NimBLEScan *scan = NimBLEDevice::getScan();
    scan->setScanCallbacks(&g_scanCallbacks, false);
    scan->setActiveScan(true);   // needed to read advertised names
    scan->setInterval(100);
    scan->setWindow(80);         // leave radio time for the peripheral links
    startScan();
}

void ObdBle::startScan() {
    g_haveCandidate = false;
    _layoutName = "none";
    // NimBLE-Arduino 2.x takes the scan duration in MILLISECONDS.
    // (1.4.x took seconds. If you downgrade the library, change this.)
    NimBLEDevice::getScan()->start(OBD_SCAN_DURATION_S * 1000, false, true);
    _state = ObdState::Scanning;
    _stateSince = millis();
    Serial.println("[OBD] Scanning for a BLE OBD dongle...");
}

void ObdBle::dropLink(const char *why, uint32_t backoffMs) {
    Serial.printf("[OBD] Link down: %s\n", why);
    _stream.detach();
    g_notifyChar = nullptr;
    g_writeChar = nullptr;
    if (g_client != nullptr) {
        if (g_client->isConnected()) g_client->disconnect();
        NimBLEDevice::deleteClient(g_client);
        g_client = nullptr;
    }
    g_linkDropped = false;
    g_haveCandidate = false;
    _layoutName = "none";
    _hz = 0.0f;
    _state = ObdState::Backoff;
    _stateSince = millis();
    _backoffUntil = millis() + backoffMs;
}

bool ObdBle::connectToDongle() {
    g_client = NimBLEDevice::createClient();
    g_client->setClientCallbacks(&g_clientCallbacks, false);
    g_client->setConnectTimeout(OBD_CONNECT_TIMEOUT_MS);

    if (!g_client->connect(g_foundAddress)) {
        Serial.println("[OBD] connect() failed");
        return false;
    }
    // A larger MTU does not speed up short PID replies much, but it costs
    // nothing to ask and helps the multi-frame cases.
    g_client->setMTU(247);
    Serial.printf("[OBD] Connected, MTU %u\n", (unsigned)g_client->getMTU());
    return true;
}

bool ObdBle::discoverCharacteristics() {
    // 1. Try the known layouts in order.
    for (size_t i = 0; i < LAYOUT_COUNT; i++) {
        NimBLERemoteService *svc = g_client->getService(NimBLEUUID(LAYOUTS[i].service));
        if (svc == nullptr) continue;

        NimBLERemoteCharacteristic *nc = svc->getCharacteristic(NimBLEUUID(LAYOUTS[i].notifyChar));
        NimBLERemoteCharacteristic *wc = svc->getCharacteristic(NimBLEUUID(LAYOUTS[i].writeChar));
        if (nc == nullptr || wc == nullptr) continue;
        if (!nc->canNotify()) continue;
        if (!wc->canWrite() && !wc->canWriteNoResponse()) continue;

        g_notifyChar = nc;
        g_writeChar = wc;
        _layoutName = LAYOUTS[i].name;
        Serial.printf("[OBD] Matched layout: %s\n", _layoutName);
        break;
    }

    // 2. Fallback: any non-standard service offering exactly one notify
    //    characteristic and exactly one writable one. Anything more
    //    ambiguous is refused rather than guessed at.
    if (g_notifyChar == nullptr) {
        Serial.println("[OBD] No known layout matched; probing services...");
        const std::vector<NimBLERemoteService *> &services = g_client->getServices(true);
        for (NimBLERemoteService *svc : services) {
            if (isStandardService(svc->getUUID())) continue;

            NimBLERemoteCharacteristic *nc = nullptr;
            NimBLERemoteCharacteristic *wc = nullptr;
            int notifyCount = 0, writeCount = 0;

            const std::vector<NimBLERemoteCharacteristic *> &chars = svc->getCharacteristics(true);
            for (NimBLERemoteCharacteristic *c : chars) {
                if (c->canNotify()) { notifyCount++; nc = c; }
                if (c->canWrite() || c->canWriteNoResponse()) { writeCount++; wc = c; }
            }
            Serial.printf("[OBD]   %s: %d notify, %d write\n",
                          svc->getUUID().toString().c_str(), notifyCount, writeCount);

            if (notifyCount == 1 && writeCount == 1) {
                g_notifyChar = nc;
                g_writeChar = wc;
                _layoutName = "probed";
                Serial.println("[OBD] Using probed layout");
                break;
            }
        }
    }

    if (g_notifyChar == nullptr || g_writeChar == nullptr) {
        Serial.println("[OBD] Could not identify the dongle's data channel.");
        Serial.println("[OBD] Add its UUIDs to LAYOUTS[] in obd_ble.cpp.");
        return false;
    }

    if (!g_notifyChar->subscribe(true, notifyCallback, true)) {
        Serial.println("[OBD] subscribe() failed");
        return false;
    }

    size_t chunk = 20;
    if (g_client->getMTU() > 23) chunk = g_client->getMTU() - 3;
    if (chunk > 64) chunk = 64;   // ELM commands are short; no reason to go large
    _stream.attach(bleWriteSink, nullptr, chunk);
    return true;
}

bool ObdBle::initialiseElm() {
    // ELMduino's begin() is blocking: it sends AT D / AT Z / AT E0 / AT S0 /
    // AT AL / AT ST / AT SP and waits for each. With a forced protocol this
    // is ~1-2 s. With ELM_PROTOCOL '0' the first request also triggers a
    // SEARCHING... stall of up to 20 s. Nothing time-critical is running in
    // this state, but do not shorten the peripheral-side watchdog below it.
    Serial.printf("[OBD] Initialising ELM327 (protocol '%c')...\n", ELM_PROTOCOL);

    if (!g_elm.begin(_stream, /*debug=*/false, ELM_TIMEOUT_MS, ELM_PROTOCOL, ELM_PAYLOAD_LEN)) {
        Serial.println("[OBD] ELM327 init failed");
        return false;
    }
    Serial.println("[OBD] ELM327 ready");

    // Report which PIDs the vehicle actually answers. Throttle (0x11) is
    // missing on some diesels; do not assume.
    Serial.printf("[OBD] supported: RPM=%d SPEED=%d THROTTLE=%d LOAD=%d\n",
                  g_elm.isPidSupported(0x0C), g_elm.isPidSupported(0x0D),
                  g_elm.isPidSupported(0x11), g_elm.isPidSupported(0x04));
    return true;
}

void ObdBle::pollOnce() {
    float value = 0.0f;

    switch (_slot) {
        case SLOT_RPM:      value = g_elm.rpm(); break;
        case SLOT_SPEED:    value = (float)g_elm.kph(); break;
        case SLOT_THROTTLE: value = g_elm.throttle(); break;
        case SLOT_LOAD:     value = g_elm.engineLoad(); break;
        default: _slot = 0; return;
    }

    if (g_elm.nb_rx_state == ELM_SUCCESS) {
        const uint32_t now = millis();
        switch (_slot) {
            case SLOT_RPM:      _values.rpm = value; break;
            case SLOT_SPEED:    _values.speed_kph = value; break;
            case SLOT_THROTTLE: _values.throttle_pct = value; break;
            case SLOT_LOAD:     _values.load_pct = value; break;
        }
        _values.ts_ms[_slot] = now;
        _freshSlot = (ObdSlot)_slot;
        _fresh = true;
        _lastSuccessMs = now;
        _windowCount++;
        _slot = (_slot + 1) % SLOT_COUNT;

    } else if (g_elm.nb_rx_state == ELM_GETTING_MSG) {
        // Still waiting on this one. One request in flight at a time:
        // sending anything now would earn a STOPPED reply.
        return;

    } else {
        // NO DATA is the ordinary reply to a PID this vehicle does not
        // support. It is not a fault and must not tear the link down.
        if (g_elm.nb_rx_state == ELM_NO_DATA) _noData++;
        else _errors++;
        _slot = (_slot + 1) % SLOT_COUNT;
    }
}

void ObdBle::service() {
    const uint32_t now = millis();

    if (g_linkDropped && _state != ObdState::Backoff) {
        dropLink("notified by NimBLE", OBD_RECONNECT_DELAY_MS);
        return;
    }

    switch (_state) {
        case ObdState::Idle:
            startScan();
            break;

        case ObdState::Scanning:
            if (g_haveCandidate) {
                _state = ObdState::Connecting;
                _stateSince = now;
            } else if (now - _stateSince > (OBD_SCAN_DURATION_S * 1000UL) + 2000UL) {
                // Scan ended with nothing. Retry rather than sit idle.
                Serial.println("[OBD] No dongle found; rescanning");
                startScan();
            }
            break;

        case ObdState::Connecting:
            if (connectToDongle()) {
                _state = ObdState::Discovering;
                _stateSince = now;
            } else {
                dropLink("connect failed", OBD_RECONNECT_DELAY_MS);
            }
            break;

        case ObdState::Discovering:
            if (discoverCharacteristics()) {
                _state = ObdState::Initialising;
                _stateSince = now;
            } else {
                dropLink("discovery failed", OBD_RECONNECT_DELAY_MS * 3);
            }
            break;

        case ObdState::Initialising:
            if (initialiseElm()) {
                _slot = 0;
                _lastSuccessMs = now;
                _windowStart = now;
                _windowCount = 0;
                _state = ObdState::Polling;
                _stateSince = now;
            } else {
                dropLink("ELM init failed", OBD_RECONNECT_DELAY_MS * 2);
            }
            break;

        case ObdState::Polling:
            if (!isLinkUp()) {
                dropLink("link lost", OBD_RECONNECT_DELAY_MS);
                break;
            }
            if (now - _lastPollMs >= OBD_POLL_INTERVAL_MS) {
                _lastPollMs = now;
                pollOnce();
            }
            // Rate counter, one-second window.
            if (now - _windowStart >= 1000) {
                _hz = (float)_windowCount * 1000.0f / (float)(now - _windowStart);
                _windowStart = now;
                _windowCount = 0;
            }
            // Dongle reachable but the vehicle stopped answering: ignition
            // off, or the dongle dozed. Re-initialise rather than sit there.
            if (now - _lastSuccessMs > OBD_STALL_TIMEOUT_MS) {
                dropLink("no successful read; reinitialising", OBD_RECONNECT_DELAY_MS);
            }
            break;

        case ObdState::Backoff:
            if (now >= _backoffUntil) startScan();
            break;
    }
}

bool ObdBle::takeFresh(ObdSlot *slot) {
    if (!_fresh) return false;
    _fresh = false;
    if (slot != nullptr) *slot = _freshSlot;
    return true;
}
