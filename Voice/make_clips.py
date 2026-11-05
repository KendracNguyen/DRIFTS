"""Generate the DRIFTS voice clips from phrases.json with Piper (offline TTS).

    pip install piper-tts                    # once, on a laptop (not needed on the Pis)
    python -m piper.download_voices en_US-libritts_r-medium
    python Voice/make_clips.py [--voice-dir DIR]

Writes Voice/clips/NN_NAME.wav. The first model plays them on the Pi Zero's
Whisplay HAT speaker (drifts/whisplay_ui.py) and Tokyo on its USB speaker,
both with aplay. Never renumber a phrase; add new ones at the end.
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def wav_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as w:
        return round(1000 * w.getnframes() / w.getframerate())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--voice-dir", default=".", help="folder holding the downloaded .onnx voice")
    args = ap.parse_args(argv)

    cfg = json.loads((HERE / "phrases.json").read_text())
    v, phrases = cfg["voice"], cfg["phrases"]
    ids = [p["id"] for p in phrases]
    if len(set(ids)) != len(ids) or min(ids) < 1 or max(ids) > 255:
        sys.exit("phrase ids must be unique, 1-255")

    from piper import PiperVoice, SynthesisConfig
    model = Path(args.voice_dir) / f"{v['model']}.onnx"
    voice = PiperVoice.load(str(model))
    syn = SynthesisConfig(speaker_id=v.get("speaker"), length_scale=v.get("length_scale", 1.0))

    clips = HERE / "clips"
    clips.mkdir(exist_ok=True)
    for old in clips.glob("*.wav"):
        old.unlink()
    for p in phrases:
        out = clips / f"{p['id']:02d}_{p['name']}.wav"
        with wave.open(str(out), "wb") as wf:
            voice.synthesize_wav(p["text"], wf, syn_config=syn)
        print(f"  {p['id']:2d} {p['name']:<12} {wav_ms(out):5d} ms  {p['text']}")
    print(f"wrote {clips.relative_to(REPO)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
