// Host test for line_buffer.h: the wired Pi link's message framing.
#include <cstdio>
#include <string>
#include "../drifts_esp32/line_buffer.h"

static int fails = 0, checks = 0;
#define CHECK(c) do { checks++; if (!(c)) { fails++; printf("FAIL line %d: %s\n", __LINE__, #c); } } while (0)

template <size_t N>
static std::string feed(LineBuffer<N> &b, const char *s, int *lines = nullptr) {
    std::string last;
    for (const char *p = s; *p; p++)
        if (b.push(*p)) { last = b.line(); if (lines) (*lines)++; }
    return last;
}

int main() {
    LineBuffer<16> b;
    CHECK(feed(b, "PING\n") == "PING");
    CHECK(feed(b, "DROWSY\r\n") == "DROWSY");            // CRLF: one line, the empty one ignored
    int n = 0;
    feed(b, "\r\n\n  \n", &n);
    CHECK(n == 0);                                       // blank lines are not messages
    CHECK(feed(b, "  EYE:72  \n") == "EYE:72");          // trimmed
    n = 0;
    std::string last = feed(b, "EYE:1\nEYE:2\n", &n);
    CHECK(n == 2 && last == "EYE:2");
    n = 0;
    last = feed(b, "THIS LINE IS FAR TOO LONG FOR THE BUFFER\nBEEP\n", &n);
    CHECK(n == 1 && last == "BEEP");                     // the long line is dropped whole, not split
    CHECK(b.dropped() == 1);
    CHECK(feed(b, "NOT_DROWSY") == "");                  // no line end yet
    CHECK(feed(b, "\n") == "NOT_DROWSY");
    LineBuffer<8> exact;
    CHECK(feed(exact, "1234567\n") == "1234567");        // N-1 characters fit
    printf("test_pi_uart: %d/%d checks passed%s\n", checks - fails, checks, fails ? "" : "  ALL PASS");
    return fails ? 1 : 0;
}
