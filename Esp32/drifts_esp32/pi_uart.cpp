#include "pi_uart.h"

#include "config.h"
#include "line_buffer.h"

PiUart PiWire;

static LineBuffer<160> g_rx;

void PiUart::init() {
    // The 1 Hz status line is ~250 bytes; a TX buffer this size means
    // print() returns at once instead of waiting ~22 ms for the FIFO.
    Serial1.setTxBufferSize(1024);
    Serial1.setRxBufferSize(512);
    Serial1.begin(PI_UART_BAUD, SERIAL_8N1, PI_UART_RX_PIN, PI_UART_TX_PIN);
    Serial1.println("HELLO " DRIFTS_FW_VERSION);
    Serial.printf("[UART] Pi link on GPIO %d (TX) / %d (RX), %lu baud\n", PI_UART_TX_PIN, PI_UART_RX_PIN,
                  (unsigned long)PI_UART_BAUD);
}

void PiUart::service(uint32_t now_ms) {
    int budget = 256;                        // bounded work per loop
    while (budget-- > 0 && Serial1.available() > 0) {
        if (!g_rx.push((char)Serial1.read())) continue;
        const char *line = g_rx.line();
        lastHeardMs_ = now_ms;
        if (!everHeard_ || !announced_) {
            Serial.println("[UART] Pi heard on the wired link");
            announced_ = true;
        }
        everHeard_ = true;
        if (strcasecmp(line, "PING") == 0) continue;      // heartbeat only
        if (count_ >= QUEUE) {                            // full: drop the oldest
            head_ = (head_ + 1) % QUEUE;
            count_--;
        }
        queue_[(head_ + count_) % QUEUE] = String(line);
        count_++;
    }
    if (announced_ && now_ms - lastHeardMs_ > PI_UART_LINK_MS) {
        Serial.println("[UART] Pi silent on the wired link");
        announced_ = false;
    }
}

String PiUart::popResult() {
    if (count_ == 0) return String("");
    String s = queue_[head_];
    queue_[head_] = String();
    head_ = (head_ + 1) % QUEUE;
    count_--;
    return s;
}

void PiUart::wakePi() {
    Serial1.println("WAKE");
    Serial.println("[UART] WAKE -> Pi");
}

void PiUart::setPiIdle() { Serial1.println("IDLE"); }

void PiUart::pushStatus(const char *json) {
    if (json == nullptr) return;
    // Always sent, heard or not: the Pi may have just booted, and a line it
    // can't read is cheap. availableForWrite() guards a stalled wire.
    if (Serial1.availableForWrite() < (int)strlen(json) + 8) return;
    Serial1.print("STATUS ");
    Serial1.println(json);
}

bool PiUart::isPiConnected(uint32_t now_ms) const {
    return everHeard_ && now_ms - lastHeardMs_ <= PI_UART_LINK_MS;
}

uint32_t PiUart::piSilentForMs(uint32_t now_ms) const {
    if (!everHeard_) return UINT32_MAX;
    return now_ms - lastHeardMs_;
}

unsigned long PiUart::droppedLines() const { return g_rx.dropped(); }
