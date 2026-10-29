#include "lcd_i2c.h"
#include "config.h"

#include <Wire.h>

LcdI2c Lcd;

// PCF8574 -> HD44780 wiring used by the common blue backpacks:
// P0 = RS, P1 = RW (held low: write only), P2 = E, P3 = backlight, P4-P7 = D4-D7.
static constexpr uint8_t PIN_RS = 0x01;
static constexpr uint8_t PIN_EN = 0x04;
static constexpr uint8_t PIN_BL = 0x08;

static constexpr uint8_t LCD_ADDRS[] = {0x27, 0x3F};

static bool i2cPresent(uint8_t addr) {
    Wire.beginTransmission(addr);
    return Wire.endTransmission() == 0;
}

// One nibble = two bus writes (E high, then E low), sent in one transaction.
// At 100 kHz each byte takes ~90 us, far longer than the 450 ns E pulse and
// the 37 us command time the HD44780 needs, so no extra delays are required.
void LcdI2c::sendNibble(uint8_t nibble, bool rs) {
    const uint8_t base = (uint8_t)((nibble & 0x0F) << 4) | (rs ? PIN_RS : 0) | (LCD_BACKLIGHT ? PIN_BL : 0);
    Wire.beginTransmission(_addr);
    Wire.write(base | PIN_EN);
    Wire.write(base);
    Wire.endTransmission();
}

void LcdI2c::sendByte(uint8_t value, bool rs) {
    const uint8_t bl = LCD_BACKLIGHT ? PIN_BL : 0;
    const uint8_t r = rs ? PIN_RS : 0;
    const uint8_t hi = (uint8_t)(value & 0xF0) | r | bl;
    const uint8_t lo = (uint8_t)((value << 4) & 0xF0) | r | bl;
    Wire.beginTransmission(_addr);
    Wire.write(hi | PIN_EN);
    Wire.write(hi);
    Wire.write(lo | PIN_EN);
    Wire.write(lo);
    Wire.endTransmission();
}

// HD44780 4-bit initialisation (datasheet figure 24). Blocking, ~60 ms, run
// once at boot or on the LCD command -- never from the normal loop path.
void LcdI2c::initPanel() {
    delay(50);                       // >40 ms after power reaches 2.7 V
    sendNibble(0x03, false); delay(5);
    sendNibble(0x03, false); delay(1);
    sendNibble(0x03, false); delay(1);
    sendNibble(0x02, false); delay(1);   // now in 4-bit mode
    command(0x28);                   // 4-bit, 2 lines, 5x8 font
    command(0x0C);                   // display on, cursor off, blink off
    command(0x06);                   // entry mode: increment, no shift
    command(0x01);                   // clear
    delay(2);                        // clear needs 1.52 ms
    _frame.invalidate();
    _cursorRow = _cursorCol = -1;
}

void LcdI2c::setCursor(uint8_t row, uint8_t col) {
    command((uint8_t)(0x80 | ((row ? 0x40 : 0x00) + col)));
    _cursorRow = row;
    _cursorCol = col;
}

bool LcdI2c::begin() {
    // The IMU also calls Wire.begin on the same pins; a second call is a no-op.
    Wire.begin(IMU_SDA_PIN, IMU_SCL_PIN);
    Wire.setClock(I2C_CLOCK_HZ);

    for (uint8_t a : LCD_ADDRS) {
        if (i2cPresent(a)) {
            _addr = a;
            break;
        }
    }
    if (_addr == 0) {
        Serial.println("[LCD] No display at 0x27 or 0x3F; continuing without it (I2C SCAN to check)");
        return false;
    }

    initPanel();
    _startedMs = millis();
    // Draw the welcome screen in full now: setup() blocks for a few seconds
    // (IMU calibration, BLE init) before loop() starts updating.
    ScreenView boot;
    composeScreen(boot, _line1, _line2);
    LcdWrite w[LCD_ROWS * LCD_COLS];
    const int n = _frame.plan(_line1, _line2, w, LCD_ROWS * LCD_COLS);
    for (int i = 0; i < n; i++) {
        if (w[i].row != _cursorRow || w[i].col != _cursorCol) setCursor(w[i].row, w[i].col);
        sendByte((uint8_t)w[i].ch, true);
        _cursorCol++;
    }
    Serial.printf("[LCD] 16x2 display at 0x%02X, I2C %lu kHz\n", _addr, (unsigned long)(I2C_CLOCK_HZ / 1000));
    return true;
}

void LcdI2c::reinit() {
    if (_addr == 0) {
        // Not found at boot (unplugged, or wired after power-up): look again.
        if (!begin()) return;
        _looping = false;   // replay Welcome / Drive Safe from the next loop
        return;
    }
    initPanel();
    Serial.printf("[LCD] Re-initialised at 0x%02X. Showing: |%s| |%s|\n", _addr, _line1, _line2);
}

void LcdI2c::service(const ScreenView &view, uint32_t nowMs) {
    if (_addr == 0) return;

    // The boot sequence is timed from the first loop, not from begin():
    // setup() blocks for seconds afterwards and would otherwise eat the
    // welcome screen's time. The welcome drawn in begin() stays up meanwhile.
    if (!_looping) {
        _looping = true;
        _startedMs = nowMs;
    }

    if (_lastComposeMs == 0 || nowMs - _lastComposeMs >= LCD_REFRESH_MS) {
        _lastComposeMs = nowMs;
        ScreenView v = view;
        v.sinceBootMs = nowMs - _startedMs;
        composeScreen(v, _line1, _line2);
    }

    LcdWrite w[LCD_CHARS_PER_LOOP];
    const int n = _frame.plan(_line1, _line2, w, LCD_CHARS_PER_LOOP);
    for (int i = 0; i < n; i++) {
        if (w[i].row != _cursorRow || w[i].col != _cursorCol) setCursor(w[i].row, w[i].col);
        sendByte((uint8_t)w[i].ch, true);
        _cursorCol++;                // the HD44780 advances the cursor itself
    }
}

void i2cScan() {
    Serial.println("[I2C] Scanning 0x08-0x77 ...");
    int found = 0;
    for (uint8_t a = 0x08; a <= 0x77; a++) {
        if (i2cPresent(a)) {
            const char *what = a == 0x68 ? "MPU-6050"
                             : (a == 0x27 || a == 0x3F) ? "LCD backpack (PCF8574)"
                             : "unknown";
            Serial.printf("[I2C]   0x%02X  %s\n", a, what);
            found++;
        }
    }
    Serial.printf("[I2C] %d device(s)\n", found);
}
