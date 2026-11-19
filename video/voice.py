#!/usr/bin/env python3
"""Generate the voiceover with Kokoro-82M (Apache-2.0, runs locally) and
measure when every word is spoken, so the captions can sync to the voice.

Needs: pip install kokoro-onnx soundfile ; ffmpeg with rubberband
Model files (from huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX-timestamped):
  KOKORO_DIR/model_ts.onnx and KOKORO_DIR/voices.npz (voice .bin files packed with numpy)

Writes video/out/voiceover.wav and video/out/voice_timings.json
"""
import json, os, subprocess, sys

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro
from kokoro_onnx.sliding import token_edges

HERE = os.path.dirname(os.path.abspath(__file__))
KDIR = os.environ.get("KOKORO_DIR", os.path.join(HERE, "kokoro"))
SR = 24000
VOICE, SPEED, PITCH = "af_heart", 0.95, 0.94   # calm, slow, a touch lower
TOTAL = 49.7

# (anchor_time, anchor_word_index, text). The anchor word (the key word) lands
# exactly on its caption time; the other words fall where the voice says them.
PHRASES = [
    (0.95, 2, "You're not lazy.", 0.88),
    (3.47, 1, "Your mind is"),
    (4.30, 0, "untrained."),
    (7.35, 4, "Every day you lose the same fight."),
    (9.28, 2, "Five more minutes."),
    (10.40, 2, "One more video.", 1.05),
    (11.36, 2, "I'll start Monday.", 1.05),
    (12.20, 0, "Discipline isn't a feeling."),
    (14.45, 2, "It's a structure."),
    (15.92, 0, "That's why this was built.", 1.0),
    (17.95, 2, "The Complete Fighter."),
    (20.70, 5, "Six weeks to train your mind."),
    (24.60, 4, "Twenty rounds to handle fear,"),
    (25.50, 0, "ego,"),
    (26.55, 1, "and pressure."),
    (28.20, 0, "Twelve weeks to build real strength,"),
    (31.40, 4, "with nothing but your bodyweight."),
    (33.00, 0, "Every page is interactive.", 1.14),
    (34.60, 0, "Tick it."),
    (35.30, 0, "Write in it.", 1.05),
    (36.10, 0, "Track it."),
    (37.20, 0, "Written by a fighter who started with one messy pull-up."),
    (42.35, 6, "Twelve weeks from now, you'll have results", 1.05),
    (43.15, 1, "or excuses."),
    (44.15, 0, "The Complete Fighter."),
    (45.40, 0, "Twelve euros, this week only.", 1.1),
    (47.85, 2, "Link in bio."),
]


def synth(k, text, speed=SPEED):
    ph = " ".join(k.tokenizer.phonemize(text, "en-us").split())
    vocab = k.tokenizer.vocab
    toks, word_tok = [], []
    for i, ch in enumerate(ph):
        if ch not in vocab:
            continue
        if ch != " " and (i == 0 or ph[i - 1] == " "):
            word_tok.append(len(toks))
        toks.append(vocab[ch])
    style = k.voices[VOICE][len(toks) - 1].astype(np.float32)
    wav, dur = k.sess.run(None, {"input_ids": np.array([[0, *toks, 0]], np.int64), "style": style,
                                 "speed": np.array([speed], np.float32)})
    wav, dur = wav.ravel(), dur.ravel()
    edges = token_edges(dur, len(wav))
    onsets = [edges[t + 1] / SR for t in word_tok]
    # trim silence at both ends
    loud = np.nonzero(np.abs(wav) > 0.01)[0]
    a, b = max(loud[0] - int(0.03 * SR), 0), min(loud[-1] + int(0.08 * SR), len(wav))
    onsets = [max(o - a / SR, 0.0) for o in onsets]
    onsets[0] = 0.0
    return wav[a:b], onsets, ph


def main():
    k = Kokoro(os.path.join(KDIR, "model_ts.onnx"), os.path.join(KDIR, "voices.npz"))
    track = np.zeros(int(TOTAL * SR), np.float32)
    out, prev_end = [], 0.0
    for anchor, ai, text, *sp in PHRASES:
        wav, onsets, ph = synth(k, text, *sp)
        words = text.split()
        if len(onsets) != len(words):
            print("WORD COUNT MISMATCH", text, ph, file=sys.stderr)
        start = anchor - onsets[ai]
        end = start + len(wav) / SR
        flag = "  OVERLAP" if start < prev_end - 1e-3 else ""
        print(f"{start:6.2f}-{end:6.2f}  {text}{flag}")
        print("        " + "  ".join(f"{w}@{start + o:.2f}" for w, o in zip(words, onsets)))
        i0 = int(round(start * SR))
        track[i0:i0 + len(wav)] += wav[: len(track) - i0]
        out.append({"text": text, "start": round(start, 3), "end": round(end, 3),
                    "words": [[w, round(start + o, 3)] for w, o in zip(words, onsets)]})
        prev_end = end
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    raw = os.path.join(HERE, "out", "_voice_raw.wav")
    sf.write(raw, track / max(1e-6, np.abs(track).max()) * 0.89, SR)
    # lower the pitch slightly (duration unchanged), gentle EQ, broadcast loudness
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", raw, "-af",
                    f"rubberband=pitch={PITCH}:formant=preserved,highpass=f=70,"
                    "equalizer=f=180:t=q:w=1:g=2,loudnorm=I=-16:TP=-1.5:LRA=7",
                    "-ar", "48000", "-ac", "2", os.path.join(HERE, "out", "voiceover.wav")], check=True)
    os.remove(raw)
    json.dump(out, open(os.path.join(HERE, "out", "voice_timings.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
