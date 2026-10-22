#pragma once

// What the 16x2 LCD shows. Pure functions, no Arduino, so the host tests can
// check every screen is exactly 16 characters and says the right thing.
//
// Boot:   "   Welcome to   " / "     DRIFTS     "   then
//         "   Drive Safe   " / "                "   then the live screen.
//
// Live:   line 1  level + alert score     "ALERT     87/100"
//                                          "LEVEL 1   62/100"
//                                          "LEVEL 2   38/100"
//         line 2  what the level means     "Drive Safe"
//                                          "Mild Fatigue"
//                                          "Sev. Exhaustion"
//
// Levels follow the two steps in the EU driver drowsiness warning regulation
// (EU 2021/1341, Annex I 3.3.1): a system MAY warn at KSS 7 and MUST warn at
// KSS 8+. Level 1 (Mild Fatigue) is the "may warn" step, Level 2 (Severe
// Exhaustion) the "must warn" step. They use the same alert-score bands as
// the LEDs (alertness.h), so the screen and the LEDs never disagree.
// The score-to-KSS correspondence is a design mapping, not a validated one.
//
// "Severe Exhaustion" is 17 characters; the display has 16, so it reads
// "Sev. Exhaustion". A '*' after the score marks a PARTIAL score (no eye
// data). While the DROWSY alert runs, line 2 alternates with
// "PULL OVER SAFELY".

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "alertness.h"

constexpr int LCD_COLS = 16;
constexpr int LCD_ROWS = 2;
constexpr uint32_t LCD_WELCOME_MS = 2500;      // "Welcome to DRIFTS"
constexpr uint32_t LCD_DRIVE_SAFE_MS = 2500;   // then "Drive Safe"
constexpr uint32_t LCD_ALERT_SWAP_MS = 1500;   // alternate line 2 during an alert

struct ScreenView {
    uint32_t sinceBootMs = 0;   // time since the LCD started
    float score = 100.0f;
    AlertLevel level = AlertLevel::Ok;
    bool partial = false;
    bool alertActive = false;
};

// Writes `text` centred into a 16-char line (padded with spaces, no NUL inside).
inline void lcdCenter(char line[LCD_COLS + 1], const char *text) {
    memset(line, ' ', LCD_COLS);
    line[LCD_COLS] = '\0';
    int n = (int)strlen(text);
    if (n > LCD_COLS) n = LCD_COLS;
    memcpy(line + (LCD_COLS - n) / 2, text, (size_t)n);
}

inline void lcdLeft(char line[LCD_COLS + 1], const char *text) {
    memset(line, ' ', LCD_COLS);
    line[LCD_COLS] = '\0';
    int n = (int)strlen(text);
    if (n > LCD_COLS) n = LCD_COLS;
    memcpy(line, text, (size_t)n);
}

inline const char *levelTitle(AlertLevel l) {
    switch (l) {
        case AlertLevel::Ok:      return "ALERT";
        case AlertLevel::Caution: return "LEVEL 1";
        case AlertLevel::Low:     return "LEVEL 2";
    }
    return "?";
}

inline const char *levelMeaning(AlertLevel l) {
    switch (l) {
        case AlertLevel::Ok:      return "Drive Safe";
        case AlertLevel::Caution: return "Mild Fatigue";
        case AlertLevel::Low:     return "Sev. Exhaustion";
    }
    return "";
}

inline void composeScreen(const ScreenView &v, char line1[LCD_COLS + 1], char line2[LCD_COLS + 1]) {
    if (v.sinceBootMs < LCD_WELCOME_MS) {
        lcdCenter(line1, "Welcome to");
        lcdCenter(line2, "DRIFTS");
        return;
    }
    if (v.sinceBootMs < LCD_WELCOME_MS + LCD_DRIVE_SAFE_MS) {
        lcdCenter(line1, "Drive Safe");
        lcdCenter(line2, "");
        return;
    }

    // Line 1: "LEVEL 2   38/100", score right-aligned, '*' if partial.
    int score = (int)(v.score + 0.5f);
    if (score < 0) score = 0;
    if (score > 100) score = 100;
    char right[9];
    snprintf(right, sizeof(right), "%d/100%s", score, v.partial ? "*" : "");
    lcdLeft(line1, levelTitle(v.level));
    const int rn = (int)strlen(right);
    memcpy(line1 + LCD_COLS - rn, right, (size_t)rn);

    // Line 2: the level's meaning; during an alert, alternate with an instruction.
    const bool showAction = v.alertActive && ((v.sinceBootMs / LCD_ALERT_SWAP_MS) % 2 == 1);
    if (showAction) {
        lcdCenter(line2, "PULL OVER SAFELY");
    } else {
        // An alert can run while the score still reads OK (DROWSY from the Pi
        // before the score catches up): never show "Drive Safe" during one.
        const AlertLevel shown = v.alertActive && v.level == AlertLevel::Ok ? AlertLevel::Low : v.level;
        lcdCenter(line2, levelMeaning(shown));
    }
}

// --- Minimal-update planner -------------------------------------------------
// The display keeps what is on the glass in `shown`. Each call finds up to
// `budget` characters that differ from `target`, in reading order, and marks
// them written. Writing a few characters per loop keeps every I2C burst short
// on the single-core C6, which is also running three BLE links.
struct LcdWrite {
    uint8_t row;
    uint8_t col;
    char ch;
};

struct LcdFrame {
    char shown[LCD_ROWS][LCD_COLS];
    LcdFrame() { memset(shown, ' ', sizeof(shown)); }

    // Returns the number of writes placed in `out` (<= budget).
    int plan(const char *line1, const char *line2, LcdWrite *out, int budget) {
        const char *target[LCD_ROWS] = {line1, line2};
        int n = 0;
        for (int r = 0; r < LCD_ROWS && n < budget; r++) {
            for (int c = 0; c < LCD_COLS && n < budget; c++) {
                if (shown[r][c] != target[r][c]) {
                    out[n++] = LcdWrite{(uint8_t)r, (uint8_t)c, target[r][c]};
                    shown[r][c] = target[r][c];
                }
            }
        }
        return n;
    }

    // After a re-init the glass is blank; forget what we thought was there.
    void invalidate() { memset(shown, 0, sizeof(shown)); }
};
