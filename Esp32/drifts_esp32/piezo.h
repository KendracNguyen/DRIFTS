#pragma once

// Passive piezo LS1, driven DIFFERENTIALLY between two GPIOs for loudness.
//
//   GPIO 11 (PIEZO_A_PIN) -> R6 100 R -> LS1 pin 1
//   GPIO 18 (PIEZO_B_PIN) -> R7 100 R -> LS1 pin 2      (neither leg to GND)
//
// Both pins carry the SAME LEDC channel; pin B's output is inverted in the
// GPIO matrix. The two square waves are therefore locked exactly 180 degrees
// apart and the piezo sees +/-3.3 V = 6.6 Vpp, about +6 dB over driving one
// leg against ground. The two GPIOs act as a tiny H-bridge. This replaces
// the old low-side MOSFET (Q2/R3/R4), which was quiet because an open-drain
// switch cannot actively discharge a capacitive load.
//
// OFF MUST GO THROUGH piezoOff(), NEVER "duty 0". With pin B inverted, a duty
// of 0 leaves pin B HIGH and holds a DC 3.3 V across the piezo. piezoOff()
// detaches both pins from LEDC and drives both LOW: 0 V across the element.
//
// LEDC use: channel PIEZO_LEDC_CH, PIEZO_FREQ_HZ at PIEZO_LEDC_RES bits. The
// core shares a timer between channels with the same frequency and
// resolution, so nothing else may run at 5 kHz / 8-bit (the status LEDs use
// 1 kHz for that reason). Everything here runs from loop(); nothing blocks.

#include <stdint.h>

void piezoBegin();                               // both pins OUTPUT, LOW
void piezoOn(uint32_t freqHz);                   // start (or retune) the tone
void piezoOff();                                 // stop; both pins LOW
void piezoBeep(uint32_t freqHz, uint32_t ms);    // non-blocking timed tone
void piezoUpdate();                              // call every loop()
bool piezoIsOn();

// TEST:...:DC -- pin A held HIGH, pin B LOW: a steady 3.3 V across LS1. A
// passive piezo stays silent (one click); an ACTIVE buzzer would beep. Also
// lets a meter read the drive. piezoDc(false) returns to off.
void piezoDc(bool on);

// SWEEP command (and PIEZO_SWEEP_TEST at boot): 4000 -> 6000 Hz in 100 Hz
// steps, 300 ms each, printing each frequency. Listen for the loudest step
// to confirm 5 kHz sits near the element's resonance. piezoOff() cancels it.
void piezoSweepStart();
bool piezoSweepActive();
