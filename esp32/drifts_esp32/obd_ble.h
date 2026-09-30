// ===============================================================
//  obd_ble.h - BLE ELM327 dongle client
//
//  The ESP32 is a BLE central here: it scans for an OBD dongle,
//  works out which GATT layout that dongle uses, wraps the pair of
//  characteristics in a BleStream, hands that to ELMduino, and then
//  polls four Mode 01 PIDs round-robin.
//
//  Expect about 6-11 request/response cycles per second in total,
//  shared across all four PIDs. achievedHz() reports what you are
//  actually getting; log it rather than assuming a number.
// ===============================================================

#pragma once

#include <Arduino.h>
#include "ble_stream.h"

enum class ObdState : uint8_t {
    Idle,
    Scanning,
    Connecting,
    Discovering,
    Initialising,
    Polling,
    Backoff,
};

// Which PID a sample came from.
enum ObdSlot : uint8_t {
    SLOT_RPM = 0,
    SLOT_SPEED,
    SLOT_THROTTLE,
    SLOT_LOAD,
    SLOT_COUNT,
};

struct ObdValues {
    float rpm = 0.0f;
    float speed_kph = 0.0f;
    float throttle_pct = 0.0f;
    float load_pct = 0.0f;
    uint32_t ts_ms[SLOT_COUNT] = {0, 0, 0, 0};
};

class ObdBle {
public:
    ObdBle();

    // Must be called after NimBLEDevice::init().
    void begin();

    // Non-blocking; call every loop().
    void service();

    bool isLinkUp() const;                 // connected to the dongle
    bool isPolling() const { return _state == ObdState::Polling; }
    ObdState state() const { return _state; }
    const char *stateName() const;
    const char *layoutName() const { return _layoutName; }

    const ObdValues &values() const { return _values; }

    // True once per newly decoded sample. Clears the flag.
    // `slot` receives which PID it was.
    bool takeFresh(ObdSlot *slot);

    float achievedHz() const { return _hz; }
    uint32_t noDataCount() const { return _noData; }
    uint32_t errorCount() const { return _errors; }
    uint32_t rxOverflows() const { return _stream.overflowCount(); }

    // Called from NimBLE callbacks. Public because the callback objects
    // are free functions/classes in the .cpp.
    void onNotifyBytes(const uint8_t *data, size_t len);
    void onDongleDisconnected();
    void onDongleFound(const void *advertisedDevice);

private:
    bool connectToDongle();
    bool discoverCharacteristics();
    bool initialiseElm();
    void pollOnce();
    void dropLink(const char *why, uint32_t backoffMs);
    void startScan();

    ObdState _state;
    BleStream _stream;
    ObdValues _values;

    uint8_t _slot;
    bool _fresh;
    ObdSlot _freshSlot;

    uint32_t _stateSince;
    uint32_t _backoffUntil;
    uint32_t _lastSuccessMs;
    uint32_t _lastPollMs;

    // rate counter
    uint32_t _windowStart;
    uint32_t _windowCount;
    float _hz;

    uint32_t _noData;
    uint32_t _errors;

    const char *_layoutName;
};

extern ObdBle Obd;
