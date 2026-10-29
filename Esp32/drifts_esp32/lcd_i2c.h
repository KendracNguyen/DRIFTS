#pragma once

// HD44780 16x2 LCD behind a PCF8574 I2C backpack (the common blue 1602 +
// "IIC/I2C adapter" pair). Small built-in driver instead of a library so the
// updates stay non-blocking: only changed characters are sent, a few per loop.
//
// Shares the I2C bus with the MPU-6050 (GPIO 21 SDA / 22 SCL). The bus runs at
// I2C_CLOCK_HZ = 100 kHz because the PCF8574 is only rated to 100 kHz.
//
// Wiring and the 5 V caution: Docs/lcd-display.md. The backpack is a 5 V part;
// its SDA/SCL must reach the C6 through a level shifter.

#include <Arduino.h>
#include <stdint.h>

#include "lcd_screen.h"

class LcdI2c {
public:
    // Probes 0x27 then 0x3F (PCF8574 / PCF8574A), initialises the panel and
    // shows the welcome screen. Returns false if no backpack answers; the
    // rest of the firmware carries on without a display.
    bool begin();
    bool present() const { return _addr != 0; }
    uint8_t address() const { return _addr; }

    // Call every loop(): recomposes the screen at LCD_REFRESH_MS and sends
    // at most LCD_CHARS_PER_LOOP changed characters. view.sinceBootMs is
    // filled in here, from the first call.
    void service(const ScreenView &view, uint32_t nowMs);

    const char *line1() const { return _line1; }
    const char *line2() const { return _line2; }

    // LCD serial command: clear and redraw everything (after a loose wire).
    // If no display was found at boot, probes for it again first.
    void reinit();

private:
    void sendNibble(uint8_t nibble, bool rs);
    void sendByte(uint8_t value, bool rs);
    void command(uint8_t cmd) { sendByte(cmd, false); }
    void initPanel();
    void setCursor(uint8_t row, uint8_t col);

    uint8_t _addr = 0;
    uint32_t _startedMs = 0;
    bool _looping = false;
    uint32_t _lastComposeMs = 0;
    LcdFrame _frame;
    char _line1[LCD_COLS + 1] = {0};
    char _line2[LCD_COLS + 1] = {0};
    int _cursorRow = -1;
    int _cursorCol = -1;
};

extern LcdI2c Lcd;

// I2C SCAN serial command: prints every address that answers.
void i2cScan();
