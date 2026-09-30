// ===============================================================
//  ble_hub.h - BLE peripheral: Pi Link + Phone Link
//
//  The ESP32 serves two peers here while also acting as a central to
//  the OBD dongle. Peers are told apart by what they subscribe to:
//  the Pi subscribes to the WAKE characteristic, the phone subscribes
//  to the STATUS characteristic. The old code set piConnected on any
//  connection and never cleared it, so a phone connecting made the
//  ESP32 believe the Pi was present.
// ===============================================================

#pragma once

#include <Arduino.h>
#include <stdint.h>

class BleHub {
public:
    bool init();

    void wakePi();
    void setPiIdle();

    bool isPiConnected() const;
    bool isPhoneConnected() const;

    bool hasResult() const;
    String popResult();

    // Milliseconds since the Pi last said anything (PING or a result).
    // UINT32_MAX if the Pi has never been heard from.
    uint32_t piSilentForMs(uint32_t now_ms) const;

    void pushPhoneStatus(const char *json);
};

extern BleHub Hub;
