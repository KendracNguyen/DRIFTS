#include "ble_stream.h"

BleStream::BleStream()
    : _head(0), _tail(0), _overflows(0), _txLen(0),
      _writer(nullptr), _ctx(nullptr), _chunk(20),
      _mux(portMUX_INITIALIZER_UNLOCKED) {}

void BleStream::attach(WriteFn writer, void *ctx, size_t chunkSize) {
    _writer = writer;
    _ctx = ctx;
    _chunk = chunkSize > 0 ? chunkSize : 20;
    _txLen = 0;
    clear();
}

void BleStream::detach() {
    _writer = nullptr;
    _ctx = nullptr;
    _txLen = 0;
    clear();
}

void BleStream::clear() {
    portENTER_CRITICAL(&_mux);
    _head = 0;
    _tail = 0;
    portEXIT_CRITICAL(&_mux);
}

// Runs on the NimBLE host task.
void BleStream::ingest(const uint8_t *data, size_t len) {
    if (data == nullptr || len == 0) return;

    portENTER_CRITICAL(&_mux);
    for (size_t i = 0; i < len; i++) {
        size_t next = (_head + 1) % RX_CAPACITY;
        if (next == _tail) {
            // Full. Drop the byte rather than overwrite unread data: a
            // truncated response is detectable, a corrupted one is not.
            _overflows++;
            break;
        }
        _rx[_head] = data[i];
        _head = next;
    }
    portEXIT_CRITICAL(&_mux);
}

int BleStream::available() {
    portENTER_CRITICAL(&_mux);
    size_t h = _head, t = _tail;
    portEXIT_CRITICAL(&_mux);
    return (int)((h + RX_CAPACITY - t) % RX_CAPACITY);
}

int BleStream::read() {
    int value = -1;
    portENTER_CRITICAL(&_mux);
    if (_head != _tail) {
        value = _rx[_tail];
        _tail = (_tail + 1) % RX_CAPACITY;
    }
    portEXIT_CRITICAL(&_mux);
    return value;
}

int BleStream::peek() {
    int value = -1;
    portENTER_CRITICAL(&_mux);
    if (_head != _tail) value = _rx[_tail];
    portEXIT_CRITICAL(&_mux);
    return value;
}

void BleStream::flush() {
    flushTx();
}

size_t BleStream::write(uint8_t b) {
    return write(&b, 1);
}

size_t BleStream::write(const uint8_t *buffer, size_t size) {
    if (_writer == nullptr || buffer == nullptr) return 0;

    for (size_t i = 0; i < size; i++) {
        if (_txLen < TX_CAPACITY) {
            _tx[_txLen++] = buffer[i];
        }
        // An ELM327 command ends at CR. Never send a line feed: the
        // adapter does not expect one.
        if (buffer[i] == '\r') {
            flushTx();
        }
    }
    // Guard against a command longer than the buffer never terminating.
    if (_txLen >= TX_CAPACITY) flushTx();

    return size;
}

void BleStream::flushTx() {
    if (_writer == nullptr || _txLen == 0) return;

    size_t sent = 0;
    while (sent < _txLen) {
        size_t n = _txLen - sent;
        if (n > _chunk) n = _chunk;
        if (!_writer(_tx + sent, n, _ctx)) break;
        sent += n;
    }
    _txLen = 0;
}
