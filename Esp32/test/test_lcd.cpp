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

    show(live(62, AlertLevel::Caution));
    CHECK(!strcmp(L1, "LEVEL 1   62/100") && !strcmp(L2, "  Mild Fatigue  "), "Level 1: Mild Fatigue");

    show(live(38, AlertLevel::Low));
    CHECK(!strcmp(L1, "LEVEL 2   38/100") && !strcmp(L2, "Sev. Exhaustion "), "Level 2: Sev. Exhaustion");

    ScreenView p = live(41.6f, AlertLevel::Low);
    p.partial = true;
    show(p);
    CHECK(!strcmp(L1, "LEVEL 2  42/100*"), "partial score marked with *, rounded");

    show(live(100, AlertLevel::Ok));
    CHECK(!strcmp(L1, "ALERT    100/100"), "100 fits");
    ScreenView full = live(100, AlertLevel::Ok);
    full.partial = true;
    composeScreen(full, L1, L2);
    CHECK(!strcmp(L1, "ALERT   100/100*") && is16(L1), "100 with * still fits 16");
    show(live(0, AlertLevel::Low));
    CHECK(!strcmp(L1, "LEVEL 2    0/100"), "0 fits");
    composeScreen(live(-5, AlertLevel::Low), L1, L2);
    CHECK(!strcmp(L1, "LEVEL 2    0/100"), "out-of-range score clamped to 0");
    composeScreen(live(130, AlertLevel::Ok), L1, L2);
    CHECK(!strcmp(L1, "ALERT    100/100"), "out-of-range score clamped to 100");

    // ---------------- alert alternation ----------------
    ScreenView a = live(30, AlertLevel::Low);
    a.alertActive = true;
    a.sinceBootMs = 0 + LCD_WELCOME_MS + LCD_DRIVE_SAFE_MS + 3 * LCD_ALERT_SWAP_MS;  // odd slot
    a.sinceBootMs -= a.sinceBootMs % LCD_ALERT_SWAP_MS;
    if ((a.sinceBootMs / LCD_ALERT_SWAP_MS) % 2 == 0) a.sinceBootMs += LCD_ALERT_SWAP_MS;
    show(a);
    CHECK(!strcmp(L2, "PULL OVER SAFELY"), "alert: line 2 shows PULL OVER SAFELY in odd slots");
    a.sinceBootMs += LCD_ALERT_SWAP_MS;
    composeScreen(a, L1, L2);
    CHECK(!strcmp(L2, "Sev. Exhaustion "), "alert: and the level in even slots");

    ScreenView early = live(85, AlertLevel::Ok);
    early.alertActive = true;
    early.sinceBootMs -= early.sinceBootMs % (2 * LCD_ALERT_SWAP_MS);   // even slot
    show(early);
    CHECK(strcmp(L2, "   Drive Safe   ") != 0, "never 'Drive Safe' while an alert is running");

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
