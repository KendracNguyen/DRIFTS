// Host tests for the steering-reversal detector (weave.h).
#include <math.h>
#include <stdio.h>

#include "../drifts_esp32/weave.h"

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

// Feed a sine-wave yaw rate at 50 Hz for `seconds`; return reversals counted.
static int feedSine(WeaveDetector &w, float amp, float periodS, float seconds, uint32_t t0 = 1000) {
    int n = 0;
    const int samples = (int)(seconds * 50);
    for (int i = 0; i < samples; i++) {
        const float t = i / 50.0f;
        const float yaw = amp * sinf(2.0f * 3.14159265f * t / periodS);
        if (w.feed(yaw, t0 + (uint32_t)(i * 20))) n++;
    }
    return n;
}

int main() {
    {
        WeaveDetector w(2.0f, 2000);
        const int n = feedSine(w, 4.0f, 3.0f, 30.0f);
        // 30 s of a 3 s weave = 10 cycles = ~20 reversals (first half-cycle only arms).
        CHECK(n >= 18 && n <= 20, "3 s weave at 4 dps: ~2 reversals per cycle");
    }
    {
        WeaveDetector w(2.0f, 2000);
        CHECK(feedSine(w, 1.2f, 3.0f, 30.0f) == 0, "lane-keeping wobble inside the band is ignored");
    }
    {
        WeaveDetector w(2.0f, 2000);
        CHECK(feedSine(w, 6.0f, 30.0f, 60.0f) == 0, "slow S-bends (30 s period) are not reversals");
    }
    {
        WeaveDetector w(2.0f, 2000);
        int n = 0;
        for (int i = 0; i < 500; i++) n += w.feed(8.0f, 1000 + i * 20);   // 10 s steady curve
        CHECK(n == 0, "a long steady curve is not a reversal");
    }
    {
        WeaveDetector w(2.0f, 2000);
        int n = 0;
        n += w.feed(3.0f, 1000);
        n += w.feed(-3.0f, 1500);
        CHECK(n == 1, "one fast left-right correction counts once");
        n += w.feed(-3.0f, 1600);
        n += w.feed(-3.5f, 1700);
        CHECK(n == 1, "staying on the same side does not count again");
        n += w.feed(2.5f, 2500);
        CHECK(n == 2, "and back again counts a second reversal");
    }
    {
        WeaveDetector w(2.0f, 2000);
        int n = 0;
        n += w.feed(3.0f, 1000);
        n += w.feed(0.0f, 3000);
        n += w.feed(-3.0f, 6000);
        CHECK(n == 0, "opposite side after more than maxSwing (2 s) is not a reversal");
    }
    {
        WeaveDetector w(2.0f, 2000);
        int n = 0;
        n += w.feed(-3.0f, 0xFFFFFF00u);
        n += w.feed(3.0f, 0x00000100u);           // millis() wrapped; 512 ms later
        CHECK(n == 1, "survives millis() wrap");
    }
    {
        WeaveDetector w(2.0f, 2000);
        w.feed(3.0f, 1000);
        w.reset();
        CHECK(!w.feed(-3.0f, 1200), "reset forgets the previous side");
    }

    printf("\n%d checks, %d failures\n", checks, failures);
    if (failures == 0) printf("ALL PASS\n");
    return failures == 0 ? 0 : 1;
}
