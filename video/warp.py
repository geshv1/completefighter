#!/usr/bin/env python3
"""Build the time map that fits the video to a recorded voiceover.

The video was designed on an "old" timeline (out/voice_timings.json). Each
captioned word (plus the start of a few caption-less lines that trigger
animations) becomes an anchor: old time -> time the word is spoken in the new
recording. A smooth monotone curve through the anchors maps every new frame
to an old time, so every scene, animation and caption keeps its structure and
each caption word lands on the voice.

Writes out/warp.json: {"audio_offset": s, "end": s, "anchors": [[new, old], ...]}
"""
import json, os, re, sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.argv = sys.argv[:1]
import render as R  # noqa: E402

LEAD_IN = 0.49        # first word lands where it did before

# exact starts of words that follow a short silence inside a phrase
SILENCE_ONSETS = {35 + 2: 13.42, 23 + 3: 9.25, 74 + 7: 29.26, 93 + 2: 33.56}


def main():
    old = [w for p in json.load(open(os.path.join(HERE, "out", "voice_timings.json"))) for w in p["words"]]
    aligned = json.load(open(os.path.join(HERE, "voice_src", "aligned_words.json")))
    whisper = [w for w in json.load(open(os.path.join(HERE, "voice_src", "elevenlabs_words_medium.json")))
               if w[0].lower() not in ("-up.", "weight.")]
    import align
    phrase_first = set(align.PHRASE_STARTS)

    new = []
    for i, ((w, ta), wh) in enumerate(zip(aligned, whisper)):
        if i in phrase_first:
            t = ta                         # silence onset: exact
        elif i in SILENCE_ONSETS:
            t = SILENCE_ONSETS[i]
        else:
            t = (ta + wh[1]) / 2           # two estimates agree to ~0.1 s; average
        new.append(t)
    for i in range(1, len(new)):          # keep order, minimum 60 ms apart
        new[i] = max(new[i], new[i - 1] + 0.06)

    # which old times are caption words (or start an animation)?
    cap_times = set()
    for name in dir(R):
        g = getattr(R, name)
        groups = g if isinstance(g, list) else [g]
        for gg in groups:
            if isinstance(gg, R.Group) and gg is not R.S10_QUOTE and gg is not R.S10_SIG:
                for line in gg.lines:
                    for wd in line:
                        cap_times.add(round(wd.t, 2))
    starters = {"That's", "Written", "Every"}

    offset = LEAD_IN - new[0]
    anchors = [[0.0, 0.0]]
    for (w, told), tnew in zip(old, new):
        if round(told, 2) in cap_times or w in starters:
            anchors.append([round(tnew + offset, 3), round(told, 3)])
    anchors.sort()
    # collapse duplicates (two caption words at the same old time, e.g. "WEEKS ·")
    clean = []
    for a in anchors:
        if clean and (a[0] <= clean[-1][0] + 1e-3 or a[1] <= clean[-1][1] + 1e-3):
            continue
        clean.append(a)
    last_new, last_old = clean[-1]
    end = last_new + (R.END - last_old)
    clean.append([round(end, 3), R.END])

    print(f"audio offset {offset:+.2f}s, new length {end:.2f}s (was {R.END:.2f}s)")
    for (n0, o0), (n1, o1) in zip(clean, clean[1:]):
        print(f"  new {n0:6.2f}->{n1:6.2f}  old {o0:6.2f}->{o1:6.2f}  speed x{(o1 - o0) / (n1 - n0):.2f}")
    json.dump({"audio_offset": round(offset, 3), "end": round(end, 3), "anchors": clean},
              open(os.path.join(HERE, "out", "warp.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
