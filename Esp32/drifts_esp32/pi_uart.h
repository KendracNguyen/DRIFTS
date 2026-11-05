// ===============================================================
//  pi_uart.h - wired link to the Raspberry Pi Zero (UART1, firmware v21)
//
//  Three wires: ESP32 GPIO 19 (TX) -> Pi GPIO 15 (RX, pin 10),
//  ESP32 GPIO 20 (RX) <- Pi GPIO 14 (TX, pin 8), and GND. Both boards are
//  3.3 V, so no level shifter. 115200 baud, 8N1, one message per line.
//
//  The same messages as the BLE Pi link, so the main loop treats the Pi as
//  one peer reached over either link:
//    ESP32 -> Pi:  WAKE | IDLE | STATUS {json} (1 Hz) | HELLO <fw> (at boot)
//    Pi -> ESP32:  PING (heartbeat, the fail-safe input) | DROWSY |
//                  NOT_DROWSY | EYE:<n> | BEEP | TEST:... | DRV:s:t:b
//
//  "Connected" means the Pi has been heard within PI_UART_LINK_MS. A wire
//  has no connection event, so silence is the only sign it is gone.
// ===============================================================

#pragma once

#include <Arduino.h>

class PiUart {
public:
    void init();
    void service(uint32_t now_ms);   // read bytes; call every loop

    void wakePi();
    void setPiIdle();
    void pushStatus(const char *json);

    bool isPiConnected(uint32_t now_ms) const;
    bool hasResult() const { return count_ > 0; }
    String popResult();
    uint32_t piSilentForMs(uint32_t now_ms) const;
    unsigned long droppedLines() const;

private:
    static constexpr int QUEUE = 8;
    String queue_[QUEUE];
    int head_ = 0, count_ = 0;
    uint32_t lastHeardMs_ = 0;
    bool everHeard_ = false;
    bool announced_ = false;
};

extern PiUart PiWire;
