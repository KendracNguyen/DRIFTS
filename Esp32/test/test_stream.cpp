#include "../drifts_esp32/ble_stream.h"
#include <cstdio>
#include <vector>
#include <string>

static std::vector<std::string> g_writes;
static bool sink(const uint8_t *d, size_t n, void *) {
    g_writes.push_back(std::string((const char*)d, n));
    return true;
}
static int failures = 0;
#define CHECK(c,m) do{ if(!(c)){printf("FAIL: %s\n",m);failures++;} else printf("ok  : %s\n",m);}while(0)

int main(){
    BleStream s;
    s.attach(sink, nullptr, 20);

    // 1. Nothing goes out until CR: one BLE write per command, not per byte.
    s.write((const uint8_t*)"010C1", strlen("010C1"));
    CHECK(g_writes.empty(), "bytes buffered until CR");
    s.write('\r');
    CHECK(g_writes.size() == 1, "one BLE write per command");
    CHECK(g_writes[0] == "010C1\r", "command sent verbatim with CR");

    // 2. A command longer than the 20-byte chunk is split, not truncated.
    g_writes.clear();
    s.write((const uint8_t*)"ATSP6ATSP6ATSP6ATSP6ATSP6X", strlen("ATSP6ATSP6ATSP6ATSP6ATSP6X"));  // 26 bytes
    s.write('\r');
    std::string joined;
    for (auto &w : g_writes) joined += w;
    CHECK(g_writes.size() == 2, "27-byte command split into 2 chunks");
    CHECK(joined == "ATSP6ATSP6ATSP6ATSP6ATSP6X\r", "chunks reassemble exactly");

    // 3. Receive path: notifications reassemble into the stream in order.
    s.ingest((const uint8_t*)"41 0C 1A", 8);
    s.ingest((const uint8_t*)" F8\r\r>", 6);
    std::string got;
    while (s.available()) got += (char)s.read();
    CHECK(got == "41 0C 1A F8\r\r>", "split notifications reassemble in order");
    CHECK(s.available() == 0, "buffer drained");

    // 4. Ring buffer wraps correctly across many cycles.
    bool wrapOk = true;
    for (int cycle = 0; cycle < 50; cycle++) {
        for (int i = 0; i < 300; i++) { uint8_t b = (uint8_t)(i & 0xFF); s.ingest(&b, 1); }
        for (int i = 0; i < 300; i++) { if (s.read() != (i & 0xFF)) { wrapOk = false; break; } }
        if (!wrapOk) break;
    }
    CHECK(wrapOk, "ring buffer wraps correctly over 15000 bytes");
    CHECK(s.overflowCount() == 0, "no overflow at 300 bytes per burst");

    // 5. Overflow is counted, and drops rather than corrupting older data.
    s.clear();
    std::vector<uint8_t> big(2000, 0xAB);
    s.ingest(big.data(), big.size());
    CHECK(s.overflowCount() > 0, "oversized burst counted as overflow");
    CHECK(s.available() == 1023, "buffer holds capacity-1 bytes, not garbage");

    printf("\n%s\n", failures==0?"ALL PASS":"FAILURES PRESENT");
    return failures==0?0:1;
}
