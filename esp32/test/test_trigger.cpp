#include "../drifts_esp32/trigger.h"
#include <cassert>
#include <cstdio>
#include <cmath>

static int failures = 0;
#define CHECK(cond, msg) do { if(!(cond)){ printf("FAIL: %s\n", msg); failures++; } else printf("ok  : %s\n", msg);} while(0)

// Fill a 30s window with highway speeds so the operating-domain gate opens.
static void fillGate(DrivingTrigger &t, uint32_t t0, float kph) {
    for (int i = 0; i < 60; i++) t.pushSpeed(kph, t0 + i * 500);
}

int main() {
    // 1. The old bug: a 30s window must actually span 30s of data.
    {
        DrivingTrigger t;
        uint32_t t0 = 100000;
        // 60 samples at 2 Hz = 30 s, the realistic dongle rate.
        for (int i = 0; i < 60; i++) t.pushRpm(2000.0f, t0 + i * 500);
        fillGate(t, t0, 100.0f);
        t.evaluate(t0 + 30000);
        CHECK(t.snapshot().rpm_samples == 60, "30s window holds 60 samples at 2 Hz");
    }

    // 2. Samples older than the window are excluded.
    {
        DrivingTrigger t;
        uint32_t t0 = 100000;
        for (int i = 0; i < 20; i++) t.pushRpm(2000.0f, t0 + i * 500);      // old
        for (int i = 0; i < 40; i++) t.pushRpm(2000.0f, t0 + 60000 + i*500); // recent
        fillGate(t, t0 + 60000, 100.0f);
        t.evaluate(t0 + 60000 + 20000);
        CHECK(t.snapshot().rpm_samples == 40, "stale samples fall out of the window");
    }

    // 3. Standard deviation is correct. Alternating 1800/2200 -> sd ~200.
    {
        DrivingTrigger t;
        uint32_t t0 = 100000;
        for (int i = 0; i < 60; i++) t.pushRpm(i % 2 ? 2200.0f : 1800.0f, t0 + i * 500);
        fillGate(t, t0, 100.0f);
        t.evaluate(t0 + 30000);
        float sd = t.snapshot().rpm_stddev;
        CHECK(fabsf(sd - 201.7f) < 3.0f, "stddev of alternating 1800/2200 is ~202");
    }

    // 4. The duplicate-push bug: constant value must give sd 0, and must
    //    NOT trigger. (Old code pushed duplicates every 10ms.)
    {
        DrivingTrigger t;
        uint32_t t0 = 100000;
        for (int i = 0; i < 60; i++) t.pushRpm(2000.0f, t0 + i * 500);
        fillGate(t, t0, 100.0f);
        bool fired = t.evaluate(t0 + 30000);
        CHECK(t.snapshot().rpm_stddev == 0.0f, "constant RPM gives zero stddev");
        CHECK(!fired, "steady driving does not trigger");
    }

    // 5. The operating-domain gate suppresses parking-lot speeds.
    {
        DrivingTrigger t;
        uint32_t t0 = 100000;
        for (int i = 0; i < 60; i++) t.pushRpm(i % 2 ? 3000.0f : 1000.0f, t0 + i * 500);
        fillGate(t, t0, 15.0f);   // parking lot
        bool fired = t.evaluate(t0 + 30000);
        CHECK(!t.snapshot().gate_open, "gate closed below 65 km/h");
        CHECK(!fired, "no trigger below the speed gate despite wild RPM");
    }

    // 6. Above the gate, wild RPM does trigger, once (rising edge).
    {
        DrivingTrigger t;
        uint32_t t0 = 100000;
        for (int i = 0; i < 60; i++) t.pushRpm(i % 2 ? 3000.0f : 1000.0f, t0 + i * 500);
        fillGate(t, t0, 100.0f);
        bool first = t.evaluate(t0 + 30000);
        bool second = t.evaluate(t0 + 30100);
        CHECK(t.snapshot().gate_open, "gate open at 100 km/h");
        CHECK(first, "RPM fluctuation triggers");
        CHECK(!second, "trigger is a rising edge, not repeated");
        CHECK(strcmp(t.snapshot().reason, "RPM_FLUCTUATION") == 0, "reason reported");
    }

    // 7. Brake events are ignored when no IMU is present.
    {
        DrivingTrigger t;
        uint32_t t0 = 100000;
        t.setAccelAvailable(false);
        for (int i = 0; i < 5; i++) t.pushBrakeEvent(t0 + i * 1000);
        fillGate(t, t0, 100.0f);
        bool fired = t.evaluate(t0 + 30000);
        CHECK(!fired, "brake condition disabled without an IMU");
    }

    // 8. With an IMU, three brake events in the window trigger.
    {
        DrivingTrigger t;
        uint32_t t0 = 100000;
        t.setAccelAvailable(true);
        for (int i = 0; i < 3; i++) t.pushBrakeEvent(t0 + i * 1000);
        fillGate(t, t0, 100.0f);
        bool fired = t.evaluate(t0 + 30000);
        CHECK(fired, "three harsh brakes trigger when an IMU is present");
        CHECK(strcmp(t.snapshot().reason, "HARD_BRAKES") == 0, "brake reason reported");
    }

    // 9. Cooldown suppresses a re-trigger.
    {
        DrivingTrigger t;
        uint32_t t0 = 100000;
        for (int i = 0; i < 60; i++) t.pushRpm(i % 2 ? 3000.0f : 1000.0f, t0 + i * 500);
        fillGate(t, t0, 100.0f);
        t.evaluate(t0 + 30000);
        for (int i = 0; i < 60; i++) t.pushRpm(i % 2 ? 3000.0f : 1000.0f, t0 + 31000 + i * 100);
        bool again = t.evaluate(t0 + 40000);   // 10s later, cooldown is 30s
        CHECK(!again, "cooldown blocks a re-trigger within 30s");
    }

    // 10. Too few samples must not produce a statistic.
    {
        DrivingTrigger t;
        uint32_t t0 = 100000;
        t.pushRpm(1000.0f, t0);
        t.pushRpm(3000.0f, t0 + 500);
        fillGate(t, t0, 100.0f);
        bool fired = t.evaluate(t0 + 1000);
        CHECK(!fired, "two samples do not trigger despite huge spread");
    }

    printf("\n%s\n", failures == 0 ? "ALL PASS" : "FAILURES PRESENT");
    return failures == 0 ? 0 : 1;
}
