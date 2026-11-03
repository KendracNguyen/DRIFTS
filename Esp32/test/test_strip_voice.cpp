// Host tests for the LED strip frames (led_strip.h) and the voice decisions
// and DFPlayer framing (voice.h). No Arduino, no hardware.
//
// The STRIP_TABLE below is also in Tokyo/tests/test_tokyo.py: both systems
// must light the same number of LEDs in the same colour for a score.
#include <stdio.h>
#include <string.h>

#include "../drifts_esp32/led_strip.h"
#include "../drifts_esp32/voice.h"

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

struct Row { int score; int lit; char colour; };   // g / y / r
static const Row STRIP_TABLE[] = {
    {100, 13, 'g'}, {85, 11, 'g'}, {70, 10, 'g'},
    {69, 10, 'y'}, {55, 8, 'y'}, {40, 7, 'y'},
    {39, 6, 'r'}, {25, 5, 'r'}, {10, 3, 'r'},
};

static char colourOf(Rgb c) {
    if (c == STRIP_GREEN) return 'g';
    if (c == STRIP_YELLOW) return 'y';
    if (c == STRIP_RED) return 'r';
    if (c == STRIP_OFF) return '.';
    return '?';
}

static int litCount(const Rgb *f) {
    int n = 0;
    for (int i = STRIP_BAR_START; i < STRIP_LEDS; i++) n += f[i] != STRIP_OFF;
    return n;
}

int main() {
    char msg[160];
    Rgb f[STRIP_LEDS];

    // ---------------- strip bands ----------------
    for (const Row &r : STRIP_TABLE) {
        StripInputs in;
        in.score = (float)r.score;
        renderStrip(in, 0, f);
        snprintf(msg, sizeof(msg), "score %d -> %d LEDs %c", r.score, r.lit, r.colour);
        CHECK(litCount(f) == r.lit && colourOf(f[STRIP_BAR_START]) == r.colour &&
              colourOf(f[STRIP_BAR_START + r.lit - 1]) == r.colour, msg);
    }
    {
        bool mono = true;
        for (int s = 10; s <= 100; s++) mono = mono && stripBarLit((float)s + 1) >= stripBarLit((float)s);
        CHECK(mono, "bar never shrinks as the score rises (10-100)");
        CHECK(stripBarLit(70) > stripBarLit(69) || stripBandColor(70) != stripBandColor(69),
              "70 and 69 differ in length or colour");
    }
    {
        StripInputs in;
        in.score = 5;
        renderStrip(in, 0, f);
        const int on = litCount(f);
        renderStrip(in, 130, f);
        CHECK(on == STRIP_BAR_LEN && litCount(f) == 0, "score below 10: whole bar blinks red at 4 Hz");
    }
    {
        StripInputs in;
        in.score = 85;
        in.alarm = true;
        in.alarmStartMs = 1000;
        renderStrip(in, 1500, f);
        const bool onFirst = litCount(f) == STRIP_BAR_LEN && f[STRIP_BAR_START] == STRIP_RED;
        renderStrip(in, 2500, f);
        CHECK(onFirst && litCount(f) == 0, "alarm: whole bar red 1 s, then off 1 s, from the alert start");
    }
    {
        StripInputs in;
        in.score = 85;
        in.partial = true;
        renderStrip(in, 0, f);
        CHECK(f[STRIP_BAR_START] == stripHalf(STRIP_GREEN) && litCount(f) == 11, "partial score: half brightness");
    }
    {
        StripInputs in;
        in.linksOk = false;
        in.systemIssue = true;
        renderStrip(in, 100, f);
        const bool on = f[0] == STRIP_RED;
        renderStrip(in, 600, f);
        CHECK(on && f[0] == STRIP_OFF, "connection failure: status pixel slow red blink, wins over system issue");
        in.linksOk = true;
        renderStrip(in, 100, f);
        CHECK(f[0] == STRIP_YELLOW, "system issue: status pixel yellow");
        in.systemIssue = false;
        renderStrip(in, 600, f);
        CHECK(f[0] == STRIP_GREEN, "all linked: status pixel solid green");
    }

    // ---------------- DFPlayer frames ----------------
    {
        uint8_t b[10];
        dfFrame(DF_CMD_PLAY_MP3, 4, b);
        const uint8_t want[10] = {0x7E, 0xFF, 0x06, 0x12, 0x00, 0x00, 0x04, 0xFE, 0xE5, 0xEF};
        CHECK(memcmp(b, want, 10) == 0, "play /mp3/0004: 7E FF 06 12 00 00 04 FE E5 EF");
        dfFrame(DF_CMD_VOLUME, 25, b);
        const uint8_t vol[10] = {0x7E, 0xFF, 0x06, 0x06, 0x00, 0x00, 0x19, 0xFE, 0xDC, 0xEF};
        CHECK(memcmp(b, vol, 10) == 0, "volume 25: 7E FF 06 06 00 00 19 FE DC EF");
        CHECK(dfFrameValid(b), "own frame validates");
        b[6] ^= 1;
        CHECK(!dfFrameValid(b), "corrupted frame rejected");
    }

    // ---------------- what to say ----------------
    {
        VoiceDirector d;
        VoiceInputs in;
        CHECK(d.update(in, 0, false) == VOICE_NONE, "nothing at boot");
        CHECK(d.update(in, VOICE_READY_DELAY_MS, false) == VOICE_READY, "READY after the start-up delay");
        CHECK(d.update(in, VOICE_READY_DELAY_MS + 10, false) == VOICE_NONE, "READY only once");

        uint32_t t = 10000;
        in.level = AlertLevel::Caution;
        CHECK(d.update(in, t, false) == VOICE_LEVEL1, "entering Intervention 1 says LEVEL1");
        in.level = AlertLevel::Low;
        CHECK(d.update(in, t += 100, true) == VOICE_NONE, "waits while a clip plays");
        CHECK(d.update(in, t += 3000, false) == VOICE_LEVEL2, "then LEVEL2");
        CHECK(d.update(in, t += 30000, false) == VOICE_NONE, "no repeat within a minute");
        CHECK(d.update(in, t += 30000, false) == VOICE_LEVEL2, "LEVEL2 repeats every minute");
        in.level = AlertLevel::Caution;
        CHECK(d.update(in, t += 1000, false) == VOICE_NONE, "Low -> Caution: nothing said");
        in.level = AlertLevel::Ok;
        CHECK(d.update(in, t += 1000, false) == VOICE_RECOVERED, "back to OK says RECOVERED");
    }
    {
        VoiceDirector d;
        VoiceInputs in;
        d.update(in, 0, false);
        d.update(in, VOICE_READY_DELAY_MS, false);
        uint32_t t = 5000;
        in.alertActive = true;
        CHECK(d.update(in, t, false) == VOICE_LEVEL2, "drowsy alert counts as Intervention 2");
        in.alertActive = false;
        in.level = AlertLevel::Caution;
        d.update(in, t += 100, true);      // Low -> Caution while playing: nothing queued
        in.level = AlertLevel::Low;
        d.update(in, t += 100, true);
        CHECK(d.pending() == VOICE_LEVEL2, "LEVEL2 waits behind the current clip");
        d.request(VOICE_TEST);
        CHECK(d.pending() == VOICE_LEVEL2, "a SAY request does not push out a waiting LEVEL2");
        CHECK(!d.request(0) && !d.request(VOICE_CLIP_MAX + 1), "unknown clip numbers refused");
    }
    {
        VoiceDirector d;
        VoiceInputs in;
        d.update(in, 0, false);
        d.update(in, VOICE_READY_DELAY_MS, false);
        uint32_t t = 4000;
        CHECK(d.update(in, t += VOICE_LINK_LOST_MS * 2, false) == VOICE_NONE,
              "never-connected links stay silent (bench)");
        in.dongleOk = true;
        d.update(in, t += 1000, false);
        in.dongleOk = false;
        CHECK(d.update(in, t += 1000, false) == VOICE_NONE, "short dongle drop: silent");
        CHECK(d.update(in, t += VOICE_LINK_LOST_MS, false) == VOICE_OBD_LOST, "dongle lost 5 s: OBD_LOST");
        CHECK(d.update(in, t += 60000, false) == VOICE_NONE, "said once per loss");
        in.piOk = true;
        d.update(in, t += 1000, false);
        in.piOk = false;
        d.update(in, t += 1000, false);
        CHECK(d.update(in, t += VOICE_LINK_LOST_MS, false) == VOICE_PI_LOST, "Pi lost 5 s: PI_LOST");
    }
    {
        int total = 0;
        for (int i = 1; i <= VOICE_CLIP_MAX; i++) total += VOICE_CLIP_MS[i] > 0;
        CHECK(total == VOICE_CLIP_MAX && VOICE_CLIP_MS[VOICE_LEVEL2] > 1000, "every clip has a length");
    }

    printf("\n%d checks, %d failures\n%s\n", checks, failures, failures ? "FAILED" : "ALL PASS");
    return failures ? 1 : 0;
}
