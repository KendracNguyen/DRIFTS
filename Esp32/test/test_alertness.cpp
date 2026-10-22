// Host tests for the alert score (alertness.h) and the LED decisions
// (status_leds.h). No Arduino, no hardware.
#include <math.h>
#include <stdio.h>

#include "../drifts_esp32/alertness.h"
#include "../drifts_esp32/status_leds.h"

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

static bool near(float a, float b, float tol = 0.6f) { return fabsf(a - b) <= tol; }

int main() {
    const DrivingRatios calm;                         // all zero

    // ---------------- driving score ----------------
    {
        AlertScore s;
        CHECK(near(s.drivingScore(calm), 100), "calm driving scores 100");
        DrivingRatios d; d.steer = 1.0f;
        CHECK(near(s.drivingScore(d), 60), "one signal at its threshold costs 40");
        d.brake = 1.0f;
        CHECK(near(s.drivingScore(d), 20), "two signals at threshold cost 80");
        DrivingRatios big; big.steer = 5.0f;
        CHECK(near(s.drivingScore(big), 40), "one signal is capped at 1.5x threshold (60 points)");
        DrivingRatios all; all.steer = all.brake = all.rpm = all.speed = all.throttle = 1.5f;
        CHECK(near(s.drivingScore(all), 0), "driving score floors at 0");
    }

    // ---------------- weighting and partial ----------------
    {
        AlertScore s;
        s.update(calm, 1000);
        CHECK(s.partial(), "no eye data yet -> partial");
        CHECK(near(s.score(), 100), "partial score = driving score");

        s.setEye(50, 2000);
        s.update(calm, 2000);
        CHECK(!s.partial(), "eye data present -> not partial");
        CHECK(near(s.score(), 0.7f * 50 + 0.3f * 100), "score = 0.7 eye + 0.3 driving (65)");

        DrivingRatios d; d.steer = 1.0f;              // driving 60
        s.update(d, 3000);
        CHECK(near(s.score(), 0.7f * 50 + 0.3f * 60), "driving drop moves score by 30 % of it (53)");

        s.update(calm, 2000 + 121000);
        CHECK(s.partial(), "eye data older than 2 min -> partial again");
        CHECK(near(s.score(), 100), "stale eye data is ignored, driving only");
    }

    // ---------------- Pi binary result mapped to eye ----------------
    {
        AlertScore s;
        s.onPiResult(true, 10000);
        s.update(calm, 10000);
        CHECK(near(s.eye(), 10) && s.eyeEstimated(), "DROWSY maps eye to 10 (estimated)");
        CHECK(near(s.score(), 0.7f * 10 + 30), "DROWSY with calm driving -> 37");
        CHECK(s.level() == AlertLevel::Low, "37 is LOW");
        for (uint32_t t = 11000; t <= 40000; t += 1000) s.update(calm, t);
        CHECK(s.eye() > 45 && s.eye() < 55, "estimated eye recovers ~halfway in 30 s");
        for (uint32_t t = 41000; t <= 80000; t += 1000) s.update(calm, t);
        CHECK(near(s.eye(), 90), "estimated eye recovers to 90 and stops");

        s.onPiResult(false, 90000);
        s.update(calm, 90000);
        CHECK(near(s.eye(), 90), "NOT_DROWSY maps eye to 90");
    }
    {
        AlertScore s;
        s.setEye(20, 1000);
        for (uint32_t t = 1000; t <= 60000; t += 1000) s.update(calm, t);
        CHECK(near(s.eye(), 20), "a measured EYE value is held, not eased");
    }

    // ---------------- bands and hysteresis (OK >=70, CAUTION 50-69, LOW <50) ----------------
    {
        AlertScore s;
        s.force(72, 0, 100000); s.update(calm, 1000);
        CHECK(s.level() == AlertLevel::Ok, "72 -> OK");
        s.force(69, 1000, 100000); s.update(calm, 2000);
        CHECK(s.level() == AlertLevel::Caution, "69 -> CAUTION");
        s.force(72, 2000, 100000); s.update(calm, 3000);
        CHECK(s.level() == AlertLevel::Caution, "back to 72 stays CAUTION (needs 75)");
        s.force(75, 3000, 100000); s.update(calm, 4000);
        CHECK(s.level() == AlertLevel::Ok, "75 -> OK");
        s.force(49, 4000, 100000); s.update(calm, 5000);
        CHECK(s.level() == AlertLevel::Low, "49 -> LOW");
        s.force(52, 5000, 100000); s.update(calm, 6000);
        CHECK(s.level() == AlertLevel::Low, "back to 52 stays LOW (needs 55)");
        s.force(55, 6000, 100000); s.update(calm, 7000);
        CHECK(s.level() == AlertLevel::Caution, "55 -> CAUTION");
        s.force(80, 7000, 100000); s.update(calm, 8000);
        s.force(30, 8000, 100000); s.update(calm, 9000);
        CHECK(s.level() == AlertLevel::Low, "OK straight to LOW when the score drops below 50");
    }

    // ---------------- SCORE override ----------------
    {
        AlertScore s;
        s.force(55, 1000, 30000);
        s.update(calm, 2000);
        CHECK(s.forced() && near(s.score(), 55) && !s.partial(), "SCORE override pins the score");
        s.update(calm, 31001);
        CHECK(!s.forced() && near(s.score(), 100), "override expires after its duration");
        s.force(10, 40000, 30000); s.clearForce(); s.update(calm, 41000);
        CHECK(near(s.score(), 100), "SCORE OFF ends the override");
    }

    // ---------------- message parsers ----------------
    {
        int e = -1;
        CHECK(parseEye("EYE:72", &e) && e == 72, "EYE:72 parses");
        CHECK(parseEye("eye:0", &e) && e == 0, "eye:0 parses (case-insensitive)");
        CHECK(!parseEye("EYE:101", &e), "EYE above 100 rejected");
        CHECK(!parseEye("EYE:-5", &e) && !parseEye("EYE:", &e) && !parseEye("EYE:7x", &e),
              "bad EYE values rejected");
        DrvSample d;
        CHECK(parseDrv("DRV:-12.5:30:0", &d) && near(d.steerDeg, -12.5f, 0.01f) &&
              near(d.throttlePct, 30, 0.01f) && near(d.brakePct, 0, 0.01f), "DRV sample parses");
        CHECK(!parseDrv("DRV:1:150:0", &d), "DRV throttle over 100 % rejected");
        CHECK(!parseDrv("DRV:1:2", &d) && !parseDrv("DRV:a:1:1", &d), "malformed DRV rejected");
        CHECK(sizeof("DRV:-450.0:100.0:100.0") <= 24, "worst-case DRV fits the 24-byte BLE result buffer");
    }

    // ---------------- LED decisions ----------------
    {
        LedInputs in;
        in.dongleOk = in.piOk = true;
        LedState s = decideLeds(in);
        CHECK(s.green == LedPattern::Solid && s.yellow == LedPattern::Off && s.red == LedPattern::Off,
              "all well: green solid, yellow and red off");

        in.piOk = false;
        CHECK(decideLeds(in).green == LedPattern::SlowBlink, "Pi missing: green slow blink");
        in.piOk = true; in.dongleOk = false;
        CHECK(decideLeds(in).green == LedPattern::SlowBlink, "dongle missing: green slow blink");
        in.dongleOk = true;

        in.level = AlertLevel::Caution;
        s = decideLeds(in);
        CHECK(s.yellow == LedPattern::Solid && s.red == LedPattern::Off, "CAUTION (50-69): yellow solid");

        in.level = AlertLevel::Low;
        s = decideLeds(in);
        CHECK(s.red == LedPattern::FastBlink && s.yellow == LedPattern::Off, "LOW (<50): red fast blink");

        in.level = AlertLevel::Ok; in.alertActive = true;
        CHECK(decideLeds(in).red == LedPattern::FastBlink, "alert active: red fast blink even at a good score");
        in.alertActive = false;

        in.systemIssue = true;
        CHECK(decideLeds(in).yellow == LedPattern::SlowBlink, "system issue: yellow slow blink");
        in.level = AlertLevel::Caution;
        CHECK(decideLeds(in).yellow == LedPattern::Solid, "caution wins yellow over a system issue");
        in.level = AlertLevel::Low;
        s = decideLeds(in);
        CHECK(s.red == LedPattern::FastBlink && s.yellow == LedPattern::SlowBlink,
              "drowsy + system issue: red fast, yellow still shows the system issue");
    }

    // ---------------- blink timing ----------------
    {
        int slowOn = 0, fastOn = 0, fastEdges = 0;
        bool prev = false;
        for (uint32_t t = 0; t < 10000; t++) {
            slowOn += ledPatternOn(LedPattern::SlowBlink, t);
            const bool f = ledPatternOn(LedPattern::FastBlink, t);
            fastOn += f;
            if (f && !prev) fastEdges++;
            prev = f;
        }
        CHECK(slowOn == 5000, "slow blink: 50 % duty");
        CHECK(fastOn == 5000, "fast blink: 50 % duty");
        CHECK(fastEdges == 40, "fast blink is 4 Hz (40 flashes in 10 s), nothing faster");
        CHECK(ledPatternOn(LedPattern::Solid, 123) && !ledPatternOn(LedPattern::Off, 123), "solid on, off off");
    }

    printf("\n%d checks, %d failures\n", checks, failures);
    if (failures == 0) printf("ALL PASS\n");
    return failures == 0 ? 0 : 1;
}
