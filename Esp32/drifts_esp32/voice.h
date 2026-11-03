#pragma once

// Spoken alerts through a DFPlayer Mini (MP3 player module with its own
// 3 W amplifier), driven over UART at 9600 baud.
//
//   ESP32 VOICE_TX_PIN -> 1 k -> DFPlayer RX     DFPlayer SPK1/SPK2 -> 4-8 ohm speaker
//   ESP32 VOICE_RX_PIN <-        DFPlayer TX     DFPlayer VCC 5 V, GND
//
// The clips are /mp3/0001.mp3 ... on the module's microSD card, generated
// from Voice/phrases.json by Voice/make_clips.py (ids and lengths in
// voice_clips.h). Tokyo (Pi 5) speaks the same clips under the same ids.
//
// What is said, and when (VoiceDirector, pure and host-tested):
//   READY        once, VOICE_READY_DELAY_MS after boot
//   LEVEL1       score enters Intervention 1 (50-69)
//   LEVEL2       score enters Intervention 2 (< 50) or the drowsy alert starts;
//                repeated every VOICE_REPEAT_MS while it lasts
//   RECOVERED    back to OK from either level
//   OBD_LOST     the dongle was polling, then stopped for VOICE_LINK_LOST_MS
//   PI_LOST      the Pi was connected, then dropped for VOICE_LINK_LOST_MS
//   SAY:<n>      any clip, from the serial console or the Pi over BLE (the Pi
//                uses it for CALIBRATED and CAMERA_LOST)
//
// One clip at a time. A waiting clip is replaced by a more urgent one, never
// by a less urgent one. While a clip plays during an alert the buzzer is held
// off (muteBuzzerFor) so the words are not drowned out; the motors keep going.

#include <stdint.h>

#include "alertness.h"
#include "voice_clips.h"

constexpr uint32_t VOICE_READY_DELAY_MS = 3000;   // DFPlayer needs ~1.5 s after power-up
constexpr uint32_t VOICE_REPEAT_MS = 60000;
constexpr uint32_t VOICE_LINK_LOST_MS = 5000;

// ---- DFPlayer serial frame: 7E FF 06 cmd 00 hi lo ckhi cklo EF ----
constexpr uint8_t DF_CMD_VOLUME = 0x06;    // 0-30
constexpr uint8_t DF_CMD_PLAY_MP3 = 0x12;  // /mp3/NNNN.mp3
constexpr uint8_t DF_CMD_STOP = 0x16;
constexpr uint8_t DF_CMD_QUERY_STATUS = 0x42;
constexpr uint8_t DF_REPLY_ERROR = 0x40;
constexpr uint8_t DF_REPLY_FINISHED_SD = 0x3D;

inline void dfFrame(uint8_t cmd, uint16_t param, uint8_t out[10]) {
    out[0] = 0x7E;
    out[1] = 0xFF;
    out[2] = 0x06;
    out[3] = cmd;
    out[4] = 0x00;   // no acknowledgement
    out[5] = (uint8_t)(param >> 8);
    out[6] = (uint8_t)(param & 0xFF);
    const uint16_t sum = (uint16_t)(0 - (out[1] + out[2] + out[3] + out[4] + out[5] + out[6]));
    out[7] = (uint8_t)(sum >> 8);
    out[8] = (uint8_t)(sum & 0xFF);
    out[9] = 0xEF;
}

// A received 10-byte frame: true if well formed (start, length, checksum, end).
inline bool dfFrameValid(const uint8_t f[10]) {
    if (f[0] != 0x7E || f[1] != 0xFF || f[2] != 0x06 || f[9] != 0xEF) return false;
    const uint16_t sum = (uint16_t)(0 - (f[1] + f[2] + f[3] + f[4] + f[5] + f[6]));
    return f[7] == (uint8_t)(sum >> 8) && f[8] == (uint8_t)(sum & 0xFF);
}

inline const char *dfErrorName(uint16_t code) {
    switch (code) {
        case 1: return "module busy";
        case 2: return "sleeping";
        case 3: return "serial receive error";
        case 4: return "checksum error";
        case 5: return "track out of range";
        case 6: return "track not found (check /mp3 on the SD card)";
        case 7: return "insertion error";
        case 8: return "SD card read failed";
        case 10: return "entered sleep";
    }
    return "unknown";
}

// ---- What to say ----
struct VoiceInputs {
    AlertLevel level = AlertLevel::Ok;
    bool alertActive = false;   // the drowsy alert counts as Intervention 2
    bool dongleOk = false;
    bool piOk = false;
};

inline uint8_t voicePriority(uint8_t clip) {
    switch (clip) {
        case VOICE_LEVEL2: return 4;
        case VOICE_LEVEL1: return 3;
        case VOICE_NONE: return 0;
    }
    return 2;   // status, recovery, SAY requests
}

class VoiceDirector {
public:
    // Call every loop. `playing` = a clip is still sounding. Returns the clip
    // to start now, or VOICE_NONE.
    uint8_t update(const VoiceInputs &in, uint32_t nowMs, bool playing) {
        if (!_started) {
            _started = true;
            _bootMs = nowMs;
        }
        if (!_readyDone && nowMs - _bootMs >= VOICE_READY_DELAY_MS) {
            _readyDone = true;
            queue(VOICE_READY);
        }

        const AlertLevel lvl = in.alertActive ? AlertLevel::Low : in.level;
        if (lvl != _level) {
            if (lvl == AlertLevel::Low) {
                queue(VOICE_LEVEL2);
                _lastLevel2Ms = nowMs;
            } else if (lvl == AlertLevel::Caution && _level == AlertLevel::Ok) {
                queue(VOICE_LEVEL1);
            } else if (lvl == AlertLevel::Ok) {
                if (_pending == VOICE_LEVEL1 || _pending == VOICE_LEVEL2) _pending = VOICE_NONE;
                queue(VOICE_RECOVERED);
            }
            _level = lvl;
        } else if (lvl == AlertLevel::Low && nowMs - _lastLevel2Ms >= VOICE_REPEAT_MS) {
            queue(VOICE_LEVEL2);
            _lastLevel2Ms = nowMs;
        }

        link(in.dongleOk, nowMs, _dongle, VOICE_OBD_LOST);
        link(in.piOk, nowMs, _pi, VOICE_PI_LOST);

        if (playing || _pending == VOICE_NONE) return VOICE_NONE;
        const uint8_t c = _pending;
        _pending = VOICE_NONE;
        return c;
    }

    // SAY:<n>. Returns false for an unknown clip.
    bool request(uint8_t clip) {
        if (clip == VOICE_NONE || clip > VOICE_CLIP_MAX || VOICE_CLIP_MS[clip] == 0) return false;
        queue(clip);
        return true;
    }

    uint8_t pending() const { return _pending; }

private:
    struct LinkWatch {
        bool seen = false;        // has been up at least once
        bool announced = false;
        uint32_t downSince = 0;
        bool down = false;
    };

    void queue(uint8_t clip) {
        if (voicePriority(clip) >= voicePriority(_pending)) _pending = clip;
    }

    void link(bool ok, uint32_t nowMs, LinkWatch &w, uint8_t clip) {
        if (ok) {
            w.seen = true;
            w.down = false;
            w.announced = false;
            return;
        }
        if (!w.seen || w.announced) return;
        if (!w.down) {
            w.down = true;
            w.downSince = nowMs;
        } else if (nowMs - w.downSince >= VOICE_LINK_LOST_MS) {
            w.announced = true;
            queue(clip);
        }
    }

    bool _started = false;
    bool _readyDone = false;
    uint32_t _bootMs = 0;
    AlertLevel _level = AlertLevel::Ok;
    uint32_t _lastLevel2Ms = 0;
    uint8_t _pending = VOICE_NONE;
    LinkWatch _dongle, _pi;
};

#ifdef ARDUINO
class VoiceOut {
public:
    void begin();
    void service(const VoiceInputs &in, uint32_t nowMs);
    bool say(uint8_t clip);          // SAY:<n>
    bool trySayCommand(const char *msg, const char *source);   // "SAY:<n>"
    void printStatus() const;
    bool present() const { return _present; }

private:
    void send(uint8_t cmd, uint16_t param);
    void play(uint8_t clip, uint32_t nowMs);
    void readReplies();

    VoiceDirector _dir;
    bool _configured = false;
    bool _present = false;           // the module has answered at least once
    uint32_t _bootMs = 0;
    uint32_t _playingUntil = 0;
    uint8_t _lastClip = VOICE_NONE;
    uint8_t _rx[10] = {};
    int _rxLen = 0;
    bool _alertActive = false;
};

extern VoiceOut Voice;
#endif
