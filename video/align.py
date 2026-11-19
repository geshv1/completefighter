#!/usr/bin/env python3
"""Find when each script word is spoken in a recorded voiceover.

Phrase starts come from the silences in the recording (exact). Words inside a
phrase are placed by matching the recording against the same phrase spoken by
the local Kokoro model, whose word times are known (MFCC + DTW).

Usage: KOKORO_DIR=... python3 video/align.py video/voice_src/elevenlabs.mp3
Writes video/voice_src/aligned_words.json: [[word, seconds], ...]
"""
import json, os, subprocess, sys

import librosa
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voice as V  # noqa: E402
from kokoro_onnx import Kokoro  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 16000
HOP = 160

# index of the script word that starts each phrase in the recording
PHRASE_STARTS = [0, 3, 7, 14, 17, 20, 23, 27, 30, 35, 38, 44, 49, 50, 52, 58, 63, 67, 69, 72, 74, 84, 88, 93, 96, 98, 101]


def load(path, sr=SR):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def env_db(a):
    e = np.array([np.sqrt((a[i:i + 320] ** 2).mean()) for i in range(0, max(1, len(a) - 320), HOP)])
    return 20 * np.log10(e + 1e-6)


def onsets_after_silence(a):
    db = env_db(a)
    q = db < db.max() - 38
    out, run = [], 0
    for i, v in enumerate(q):
        if v:
            run += 1
        else:
            if run >= 8 or (i == run):   # at least 80 ms of silence (or the file start)
                out.append(i * HOP / SR)
            run = 0
    return out, db


def feats(y):
    m = librosa.feature.mfcc(y=y, sr=SR, n_mfcc=20, hop_length=HOP, n_fft=512)[1:]
    return (m - m.mean(1, keepdims=True)) / (m.std(1, keepdims=True) + 1e-6)


def main(path):
    words = [w for p in json.load(open(os.path.join(HERE, "out", "voice_timings.json"))) for w, _ in p["words"]]
    a = load(path)
    ons, db = onsets_after_silence(a)
    whisper = json.load(open(os.path.join(HERE, "voice_src", "elevenlabs_words_medium.json")))
    k = Kokoro(os.path.join(V.KDIR, "model_ts.onnx"), os.path.join(V.KDIR, "voices.npz"))

    # phrase start times: the silence onset nearest to the expected start
    rough = []   # whisper start for each script word (whisper splits a few words)
    wi = 0
    for w in whisper:
        t = w[0].lower()
        if t in ("-up.", "weight."):
            continue
        rough.append(w[1])
    assert len(rough) == len(words), (len(rough), len(words))
    starts = []
    for n, idx in enumerate(PHRASE_STARTS):
        lo = starts[-1] + 0.2 if starts else 0
        cand = [o for o in ons if o > lo and abs(o - rough[idx]) < 0.6]
        starts.append(min(cand, key=lambda o: abs(o - rough[idx])) if cand else rough[idx])

    out = []
    for n, idx in enumerate(PHRASE_STARTS):
        end_idx = PHRASE_STARTS[n + 1] if n + 1 < len(PHRASE_STARTS) else len(words)
        t0 = starts[n]
        t1 = starts[n + 1] - 0.02 if n + 1 < len(starts) else len(a) / SR
        seg = a[int(t0 * SR):int(t1 * SR)]
        # trim trailing silence
        sdb = env_db(seg)
        loud = np.nonzero(sdb > db.max() - 38)[0]
        seg = seg[: int((loud[-1] + 3) * HOP)] if len(loud) else seg
        text = " ".join(words[idx:end_idx])
        kw, kon, _ = V.synth(k, text)
        kw = librosa.resample(kw.astype(np.float32), orig_sr=V.SR, target_sr=SR)
        if len(kon) != end_idx - idx:
            raise SystemExit(f"word split mismatch in: {text}")
        D, wp = librosa.sequence.dtw(X=feats(kw), Y=feats(seg), metric="euclidean")
        wp = wp[::-1]
        for j, o in enumerate(kon):
            f = int(round(o * SR / HOP))
            ys = wp[wp[:, 0] >= f]
            t = t0 + (ys[0, 1] * HOP / SR if len(ys) else 0)
            if j == 0:
                t = t0
            out.append([words[idx + j], round(t, 3)])
    for (w, t), r in zip(out, rough):
        flag = "  <-- differs" if abs(t - r) > 0.12 else ""
        print(f"{w:14s} aligned {t:6.2f}   whisper {r:6.2f}{flag}")
    json.dump(out, open(os.path.join(HERE, "voice_src", "aligned_words.json"), "w"), indent=0)


if __name__ == "__main__":
    main(sys.argv[1])
