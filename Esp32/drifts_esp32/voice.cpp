#include "voice.h"
#include "actuators.h"
#include "config.h"

#include <Arduino.h>
#include <stdlib.h>

VoiceOut Voice;

static constexpr uint32_t DF_BOOT_MS = 1500;   // module start-up before it takes commands
static constexpr uint32_t CLIP_MARGIN_MS = 250;

void VoiceOut::begin() {
    _bootMs = millis();
    if (!VOICE_ENABLED) {
        Serial.println("[VOICE] Disabled (VOICE_ENABLED = false)");
        return;
    }
    Serial1.begin(9600, SERIAL_8N1, VOICE_RX_PIN, VOICE_TX_PIN);
    Serial.printf("[VOICE] DFPlayer on UART1 TX %d / RX %d, volume %d/30\n", VOICE_TX_PIN, VOICE_RX_PIN,
                  VOICE_VOLUME);
}

void VoiceOut::send(uint8_t cmd, uint16_t param) {
    uint8_t f[10];
    dfFrame(cmd, param, f);
    Serial1.write(f, sizeof(f));
}

void VoiceOut::play(uint8_t clip, uint32_t nowMs) {
    send(DF_CMD_PLAY_MP3, clip);
    _lastClip = clip;
    const uint32_t len = VOICE_CLIP_MS[clip] + CLIP_MARGIN_MS;
    _playingUntil = nowMs + len;
    if (_alertActive) muteBuzzerFor(len);   // words over the alarm, not under it
    Serial.printf("[VOICE] Playing %u (%lu ms)%s\n", clip, (unsigned long)VOICE_CLIP_MS[clip],
                  _present ? "" : " -- no reply from the DFPlayer yet");
}

void VoiceOut::readReplies() {
    while (Serial1.available()) {
        const int b = Serial1.read();
        if (_rxLen == 0 && b != 0x7E) continue;
        _rx[_rxLen++] = (uint8_t)b;
        if (_rxLen < 10) continue;
        _rxLen = 0;
        if (!dfFrameValid(_rx)) continue;
        if (!_present) {
            _present = true;
            Serial.println("[VOICE] DFPlayer answered: module present");
        }
        const uint16_t param = (uint16_t)((_rx[5] << 8) | _rx[6]);
        if (_rx[3] == DF_REPLY_ERROR) {
            Serial.printf("[VOICE] DFPlayer error %u: %s\n", param, dfErrorName(param));
            _playingUntil = 0;
        } else if (_rx[3] == DF_REPLY_FINISHED_SD) {
            _playingUntil = 0;
        }
    }
}

void VoiceOut::service(const VoiceInputs &in, uint32_t nowMs) {
    if (!VOICE_ENABLED) return;
    readReplies();
    _alertActive = in.alertActive;
    if (!_configured) {
        if (nowMs - _bootMs < DF_BOOT_MS) return;
        _configured = true;
        send(DF_CMD_VOLUME, VOICE_VOLUME);
        delay(30);
        send(DF_CMD_QUERY_STATUS, 0);   // any valid reply proves the wiring
    }
    const bool playing = (int32_t)(_playingUntil - nowMs) > 0;
    const uint8_t clip = _dir.update(in, nowMs, playing);
    if (clip != VOICE_NONE) play(clip, nowMs);
}

bool VoiceOut::say(uint8_t clip) {
    return _dir.request(clip);
}

bool VoiceOut::trySayCommand(const char *msg, const char *source) {
    if (strncasecmp(msg, "SAY:", 4) != 0) return false;
    char *end = nullptr;
    const long n = strtol(msg + 4, &end, 10);
    if (end == msg + 4 || *end != '\0' || n < 1 || n > VOICE_CLIP_MAX || !say((uint8_t)n)) {
        Serial.printf("[VOICE] SAY from %s: clip must be 1-%u (Voice/phrases.json)\n", source, VOICE_CLIP_MAX);
        return true;
    }
    Serial.printf("[VOICE] SAY:%ld from %s queued\n", n, source);
    return true;
}

void VoiceOut::printStatus() const {
    Serial.printf("[VOICE] %s | module %s | last clip %u | %s\n",
                  VOICE_ENABLED ? "enabled" : "disabled",
                  _present ? "answered" : "no reply (check TX/RX, 1 k resistor, 5 V)", _lastClip,
                  (int32_t)(_playingUntil - millis()) > 0 ? "playing" : "idle");
}
