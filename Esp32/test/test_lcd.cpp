// Host tests for the 16x2 screen content and the minimal-update planner
// (lcd_screen.h). No Arduino, no display.
#include <stdio.h>
#include <string.h>

#include "../drifts_esp32/lcd_screen.h"

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

static char L1[LCD_COLS + 1], L2[LCD_COLS + 1];

static void show(const ScreenView &v) {
    composeScreen(v, L1, L2);
    printf("      |%s|\n      |%s|\n", L1, L2);
}

static bool is16(const char *s) { return strlen(s) == LCD_COLS; }

static ScreenView live(float score, AlertLevel lvl) {
    ScreenView v;
    v.sinceBootMs = LCD_WELCOME_MS + LCD_DRIVE_SAFE_MS + 10000;
    v.score = score;
    v.level = lvl;
    return v;
}

int main() {
    // ---------------- boot sequence ----------------
    ScreenView v;
    v.sinceBootMs = 0;
    show(v);
    CHECK(!strcmp(L1, "   Welcome to   ") && !strcmp(L2, "     DRIFTS     "), "boot: Welcome to / DRIFTS");
    v.sinceBootMs = LCD_WELCOME_MS - 1;
    composeScreen(v, L1, L2);
    CHECK(!strcmp(L2, "     DRIFTS     "), "welcome holds for LCD_WELCOME_MS");
    v.sinceBootMs = LCD_WELCOME_MS;
    show(v);
    CHECK(!strcmp(L1, "   Drive Safe   ") && !strcmp(L2, "                "), "then: Drive Safe");
    v.sinceBootMs = LCD_WELCOME_MS + LCD_DRIVE_SAFE_MS;
    composeScreen(v, L1, L2);
    CHECK(strstr(L1, "/100") != nullptr, "then the live score screen");

    // ---------------- live screens ----------------
    show(live(87, AlertLevel::Ok));
    CHECK(!strcmp(L1, "ALERT     87/100") && !strcmp(L2, "   Drive Safe   "), "OK: ALERT 87/100 / Drive Safe");

    // Intervention screens: line 2 rotates meaning -> score (-> PULL OVER SAFELY).
    ScreenView i1 = live(62, AlertLevel::Caution);
    i1.sinceBootMs -= i1.sinceBootMs % (2 * LCD_SWAP_MS);           // frame 0
    show(i1);
    CHECK(!strcmp(L1, " INTERVENTION 1 ") && !strcmp(L2, "Signs of Fatigue"), "Intervention 1: Signs of Fatigue");
    i1.sinceBootMs += LCD_SWAP_MS;                                   // frame 1
    show(i1);
    CHECK(!strcmp(L2, "  Score 62/100  "), "Intervention 1: then the score");
    i1.sinceBootMs += LCD_SWAP_MS;
    composeScreen(i1, L1, L2);
    CHECK(!strcmp(L2, "Signs of Fatigue"), "no alert: two frames only");

    ScreenView i2 = live(38, AlertLevel::Low);
    i2.sinceBootMs -= i2.sinceBootMs % (2 * LCD_SWAP_MS);
    show(i2);
    CHECK(!strcmp(L1, " INTERVENTION 2 ") && !strcmp(L2, "Worsening Signs "), "Intervention 2: Worsening Signs");

    ScreenView p = live(41.6f, AlertLevel::Low);
    p.partial = true;
    p.sinceBootMs -= p.sinceBootMs % (2 * LCD_SWAP_MS);
    p.sinceBootMs += LCD_SWAP_MS;
    show(p);
    CHECK(!strcmp(L2, " Score 42/100*  "), "partial score marked with *, rounded");
    ScreenView po = live(87.4f, AlertLevel::Ok);
    po.partial = true;
    composeScreen(po, L1, L2);
    CHECK(!strcmp(L1, "ALERT    87/100*"), "partial on the ALERT screen");

    show(live(100, AlertLevel::Ok));
    CHECK(!strcmp(L1, "ALERT    100/100"), "100 fits");
    ScreenView full = live(100, AlertLevel::Ok);
    full.partial = true;
    composeScreen(full, L1, L2);
    CHECK(!strcmp(L1, "ALERT   100/100*") && is16(L1), "100 with * still fits 16");
    ScreenView zero = live(0, AlertLevel::Low);
    zero.sinceBootMs -= zero.sinceBootMs % (2 * LCD_SWAP_MS);
    zero.sinceBootMs += LCD_SWAP_MS;
    composeScreen(zero, L1, L2);
    CHECK(!strcmp(L2, "  Score 0/100   "), "0 fits");
    zero.score = -5;
    composeScreen(zero, L1, L2);
    CHECK(!strcmp(L2, "  Score 0/100   "), "out-of-range score clamped to 0");
    composeScreen(live(130, AlertLevel::Ok), L1, L2);
    CHECK(!strcmp(L1, "ALERT    100/100"), "out-of-range score clamped to 100");

    // ---------------- alert: third frame ----------------
    ScreenView a = live(30, AlertLevel::Low);
    a.alertActive = true;
    a.sinceBootMs -= a.sinceBootMs % (3 * LCD_SWAP_MS);
    composeScreen(a, L1, L2);
    CHECK(!strcmp(L2, "Worsening Signs "), "alert frame 0: meaning");
    a.sinceBootMs += LCD_SWAP_MS;
    composeScreen(a, L1, L2);
    CHECK(!strcmp(L2, "  Score 30/100  "), "alert frame 1: score");
    a.sinceBootMs += LCD_SWAP_MS;
    show(a);
    CHECK(!strcmp(L2, "PULL OVER SAFELY"), "alert frame 2: PULL OVER SAFELY");

    ScreenView early = live(85, AlertLevel::Ok);
    early.alertActive = true;
    early.sinceBootMs -= early.sinceBootMs % (3 * LCD_SWAP_MS);
    show(early);
    CHECK(!strcmp(L1, " INTERVENTION 2 ") && strcmp(L2, "   Drive Safe   ") != 0,
          "alert while score is OK shows Intervention 2 (buzzer + motor are firing), never 'Drive Safe'");

    // ---------------- every screen is exactly 16 chars ----------------
    bool all16 = true;
    for (int s = -10; s <= 110; s += 7)
        for (int lvl = 0; lvl < 3; lvl++)
            for (int part = 0; part < 2; part++)
                for (int al = 0; al < 2; al++)
                    for (uint32_t t = 0; t < 12000; t += 700) {
                        ScreenView x;
                        x.sinceBootMs = t;
                        x.score = (float)s;
                        x.level = (AlertLevel)lvl;
                        x.partial = part;
                        x.alertActive = al;
                        composeScreen(x, L1, L2);
                        all16 = all16 && is16(L1) && is16(L2);
                    }
    CHECK(all16, "every combination renders exactly 16 + 16 characters");

    // ---------------- minimal-update planner ----------------
    {
        LcdFrame f;
        LcdWrite w[32];
        composeScreen(live(87, AlertLevel::Ok), L1, L2);
        int total = 0, n;
        int rounds = 0;
        while ((n = f.plan(L1, L2, w, 4)) > 0) { total += n; rounds++; CHECK(n <= 4, "never more than the budget"); }
        CHECK(memcmp(f.shown[0], L1, 16) == 0 && memcmp(f.shown[1], L2, 16) == 0, "planner converges to the target");
        CHECK(total == 20, "only non-blank characters are written on a blank glass (20 here)");

        composeScreen(live(86, AlertLevel::Ok), L1, L2);
        n = f.plan(L1, L2, w, 32);
        CHECK(n == 1 && w[0].row == 0 && w[0].col == 11 && w[0].ch == '6', "87 -> 86 rewrites one character");
        CHECK(f.plan(L1, L2, w, 32) == 0, "no change, nothing written");

        f.invalidate();
        n = f.plan(L1, L2, w, 32);
        CHECK(n == 32, "after a re-init every cell is rewritten");
    }

    printf("\n%d checks, %d failures\n", checks, failures);
    if (failures == 0) printf("ALL PASS\n");
    return failures == 0 ? 0 : 1;
}
