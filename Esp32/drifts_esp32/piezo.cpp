#include "piezo.h"
#include "config.h"

#include <Arduino.h>

static bool _attached = false;   // both pins on the LEDC channel
static uint32_t _freq = 0;
static bool _dc = false;

static bool _beeping = false;
static uint32_t _beepEnd = 0;

static constexpr uint32_t SWEEP_FROM_HZ = 4000;
static constexpr uint32_t SWEEP_TO_HZ = 6000;
static constexpr uint32_t SWEEP_STEP_HZ = 100;
static constexpr uint32_t SWEEP_STEP_MS = 300;
static bool _sweeping = false;
static uint32_t _sweepHz = 0;
static uint32_t _sweepStepStart = 0;

static void pinsLow() {
    pinMode(PIEZO_A_PIN, OUTPUT);
    pinMode(PIEZO_B_PIN, OUTPUT);
    digitalWrite(PIEZO_A_PIN, LOW);
    digitalWrite(PIEZO_B_PIN, LOW);
}

static void detachBoth() {
    if (!_attached) return;
    ledcDetach(PIEZO_A_PIN);
    ledcDetach(PIEZO_B_PIN);   // the channel is freed when its last pin leaves
    _attached = false;
    _freq = 0;
}

void piezoBegin() {
    pinsLow();
}

void piezoOn(uint32_t freqHz) {
    if (freqHz == 0) {
        piezoOff();
        return;
    }
    if (_dc) {
        _dc = false;
        pinsLow();
    }
    if (_attached) {
        if (freqHz != _freq) {
            // Both pins share the channel, so one retune moves both.
            ledcChangeFrequency(PIEZO_A_PIN, freqHz, PIEZO_LEDC_RES);
            _freq = freqHz;
        }
        return;
    }
    const bool ok = ledcAttachChannel(PIEZO_A_PIN, freqHz, PIEZO_LEDC_RES, PIEZO_LEDC_CH) &&
                    ledcAttachChannel(PIEZO_B_PIN, freqHz, PIEZO_LEDC_RES, PIEZO_LEDC_CH) &&
                    ledcOutputInvert(PIEZO_B_PIN, true);
    if (!ok) {
        // Leave the element at 0 V rather than half-driven.
        ledcDetach(PIEZO_A_PIN);
        ledcDetach(PIEZO_B_PIN);
        pinsLow();
        Serial.println("[PIEZO] LEDC attach failed; buzzer off");
        return;
    }
    _attached = true;
    _freq = freqHz;
    ledcWrite(PIEZO_A_PIN, 1u << (PIEZO_LEDC_RES - 1));   // 50 % duty, both pins
}

void piezoOff() {
    _beeping = false;
    _sweeping = false;
    _dc = false;
    detachBoth();
    pinsLow();
}

void piezoBeep(uint32_t freqHz, uint32_t ms) {
    piezoOn(freqHz);
    _beeping = true;
    _beepEnd = millis() + ms;
}

bool piezoIsOn() {
    return _attached;
}

void piezoDc(bool on) {
    piezoOff();
    if (on) {
        digitalWrite(PIEZO_A_PIN, HIGH);   // B stays LOW
        _dc = true;
    }
}

void piezoSweepStart() {
    piezoOff();
    _sweeping = true;
    _sweepHz = SWEEP_FROM_HZ;
    _sweepStepStart = millis();
    piezoOn(_sweepHz);
    Serial.printf("[PIEZO] Sweep %lu-%lu Hz, %lu ms per step. Note the loudest:\n",
                  (unsigned long)SWEEP_FROM_HZ, (unsigned long)SWEEP_TO_HZ,
                  (unsigned long)SWEEP_STEP_MS);
    Serial.printf("[PIEZO]   %lu Hz\n", (unsigned long)_sweepHz);
}

bool piezoSweepActive() {
    return _sweeping;
}

void piezoUpdate() {
    const uint32_t now = millis();
    // Signed difference, so the comparison survives millis() wrapping.
    if (_beeping && (int32_t)(now - _beepEnd) >= 0) piezoOff();

    if (_sweeping && now - _sweepStepStart >= SWEEP_STEP_MS) {
        _sweepStepStart = now;
        _sweepHz += SWEEP_STEP_HZ;
        if (_sweepHz > SWEEP_TO_HZ) {
            piezoOff();
            Serial.printf("[PIEZO] Sweep done. Alert tone is PIEZO_FREQ_HZ = %d Hz\n", PIEZO_FREQ_HZ);
            return;
        }
        piezoOn(_sweepHz);
        Serial.printf("[PIEZO]   %lu Hz\n", (unsigned long)_sweepHz);
    }
}
