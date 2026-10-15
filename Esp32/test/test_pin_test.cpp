// Host tests for the TEST:<motor_ms>:<buzzer_ms>[:DC|:TONE] command parser.
#include <stdio.h>

#include "../drifts_esp32/pin_test.h"

static int failures = 0;
static int checks = 0;

#define CHECK(cond, msg)                                     \
    do {                                                     \
        ++checks;                                            \
        if (cond) {                                          \
            printf("ok  : %s\n", msg);                       \
        } else {                                             \
            printf("FAIL: %s  (line %d)\n", msg, __LINE__); \
            ++failures;                                      \
        }                                                    \
    } while (0)

int main() {
    const uint32_t MAX = 20000;
    PinTestCmd c;

    CHECK(parsePinTest("TEST:3000:2000", MAX, &c) && c.motorMs == 3000 && c.buzzerMs == 2000 && !c.buzzerDc,
          "basic command parses, tone by default");
    CHECK(parsePinTest("test:500:0", MAX, &c) && c.motorMs == 500 && c.buzzerMs == 0,
          "case-insensitive, zero leaves buzzer off");
    CHECK(parsePinTest("TEST:0:1000:DC", MAX, &c) && c.buzzerDc && c.buzzerMs == 1000,
          "DC suffix selects steady-high buzzer pin");
    CHECK(parsePinTest("TEST:0:1000:dc", MAX, &c) && c.buzzerDc, "DC suffix is case-insensitive");
    CHECK(parsePinTest("TEST:1000:1000:TONE", MAX, &c) && !c.buzzerDc, "explicit TONE suffix");
    CHECK(parsePinTest("TEST:999999:60000", MAX, &c) && c.motorMs == MAX && c.buzzerMs == MAX,
          "durations clamp to max_ms");

    CHECK(!parsePinTest("TEST:abc:100", MAX, &c), "rejects non-numeric motor duration");
    CHECK(!parsePinTest("TEST:100", MAX, &c), "rejects missing buzzer duration");
    CHECK(!parsePinTest("TEST:100:", MAX, &c), "rejects empty buzzer duration");
    CHECK(!parsePinTest("TEST:-5:100", MAX, &c), "rejects negative duration");
    CHECK(!parsePinTest("TEST:100:100:LOUD", MAX, &c), "rejects unknown mode");
    CHECK(!parsePinTest("TEST:100:100:DCX", MAX, &c), "rejects trailing junk after mode");
    CHECK(!parsePinTest("TEST:100:100x", MAX, &c), "rejects trailing junk after duration");
    CHECK(!parsePinTest("TEST:99999999999:1", MAX, &c), "rejects 32-bit overflow");
    CHECK(!parsePinTest("DROWSY", MAX, &c), "ignores ordinary results");
    CHECK(!parsePinTest("TES", MAX, &c), "rejects short prefix");
    CHECK(!parsePinTest(nullptr, MAX, &c), "null input is rejected");

    PinTestCmd untouched;
    untouched.motorMs = 42;
    parsePinTest("TEST:bad:1", MAX, &untouched);
    CHECK(untouched.motorMs == 42, "failed parse leaves output untouched");

    printf("\n%d checks, %d failures\n", checks, failures);
    if (failures == 0) printf("ALL PASS\n");
    return failures == 0 ? 0 : 1;
}
