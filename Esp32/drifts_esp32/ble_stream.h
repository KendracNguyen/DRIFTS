// ===============================================================
//  ble_stream.h - Arduino Stream over a NimBLE remote characteristic
//
//  ELMduino talks to an ELM327 through a Stream&. A wired dongle gives
//  you one for free (Serial, BluetoothSerial). A BLE dongle does not:
//  it exposes a notify characteristic for data coming back and a write
//  characteristic for commands going out.
//
//  This class makes that pair look like a Stream, so ELMduino can be
//  used unmodified.
//
//  Two details that matter:
//
//  1. Notifications arrive on the NimBLE host task, not on loop(). The
//     receive ring buffer is written from that task and read from
//     loop(), so every access is inside a critical section.
//
//  2. ELM327 commands are terminated by CR and must go out as one BLE
//     write, not one write per byte. Bytes are buffered until a CR is
//     seen, then sent in MTU-sized chunks.
// ===============================================================

#pragma once

#include <Arduino.h>

class BleStream : public Stream {
public:
    BleStream();

    // Called from the notify callback on the NimBLE host task.
    void ingest(const uint8_t *data, size_t len);

    // Called once a dongle connection is established. `writer` is invoked
    // to push bytes out; it returns false if the write failed.
    using WriteFn = bool (*)(const uint8_t *data, size_t len, void *ctx);
    void attach(WriteFn writer, void *ctx, size_t chunkSize);
    void detach();
    bool isAttached() const { return _writer != nullptr; }

    void clear();

    // Number of bytes dropped because the ring buffer was full. Nonzero
    // means responses were lost and any decode from them is suspect.
    uint32_t overflowCount() const { return _overflows; }

    // --- Stream ---
    int available() override;
    int read() override;
    int peek() override;
    void flush() override;

    // --- Print ---
    size_t write(uint8_t b) override;
    size_t write(const uint8_t *buffer, size_t size) override;

private:
    static constexpr size_t RX_CAPACITY = 1024;
    static constexpr size_t TX_CAPACITY = 64;

    void flushTx();

    uint8_t _rx[RX_CAPACITY];
    volatile size_t _head;     // next write position
    volatile size_t _tail;     // next read position
    volatile uint32_t _overflows;

    uint8_t _tx[TX_CAPACITY];
    size_t _txLen;

    WriteFn _writer;
    void *_ctx;
    size_t _chunk;

    portMUX_TYPE _mux;
};
