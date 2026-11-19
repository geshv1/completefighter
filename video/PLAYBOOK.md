# Video playbook: "Complete Fighter" style (v2: darker)

This file records how the launch video was made and how to make the next ones.
To start a new video, paste one of the prompts at the bottom into Claude Code, in this repository.

---

## 1. What worked in v1 (keep)

- **Format:** 1440×1080 (4:3) at 30 fps, exported as a 9:16 TikTok version with bars above and below. 30–45 s long.
- **Structure:** a hook in the first second, then problem, then a reframe, then the product, what's inside, proof, a choice, and a call to action. That's 10–12 scenes, one idea per scene.
- **Captions:** word by word, synced to the voice. Alice (Google Fonts serif), uppercase, 2–4 words on screen. The key word is in the accent color, slightly bigger, with an underline that draws itself.
- **Layout:** captions on the left half and animation on the right. The hook, title and call to action use the full width.
- **Every line gets its own geometric metaphor** (a ball and slope = effort, dots snapping to a grid = structure, a split path = results or excuses).
- **A stickman acts out the line** (pushing, collapsing, boxing, climbing, pull-ups, running, pointing at the link).
- **Something changes every 2–3 s:** a background switch with a wipe, a zoom punch and ring burst on key words, a scene counter and a progress bar.
- **Voice:** ElevenLabs, read straight through with no pauses. The video is fitted to the real recording (`align.py` then `warp.py`), so every caption word lands within about 0.1 s.
- **Safe area:** text and key shapes stay ≥ 90 px from every edge.

## 2. What changes in v2 (darker, more motivational)

**Mood:** night training, cold focus, pressure. Less "clean brand", more "3 a.m., nobody watching".

**Palette (dark only, no bone-white backgrounds):**

| Role | Color |
|---|---|
| Base background | `#050505` (near black) |
| Second background | `#111010` (charcoal) |
| Third background | `#3A0507` (deep blood red, used sparingly) |
| Text and lines | `#E8E3DA` (bone) |
| Key words and key shapes | `#D3141B` (bright red) |
| Secondary lines | `#6E6A63` (dim grey) |
| Rare highlight | `#B08D57` (cold gold), only for the final promise or price |

**Light and texture:** light film grain (2–3%), a strong vignette, and a slow light sweep across the frame. Occasional red rim light on shapes (a glow stroke). No flat white frames.

**Motion:** heavier and more deliberate. Slow-in and fast-out on impacts, a 1–2 frame hit on key words (a quick flash of the line, not the whole screen), and a subtle camera shake only on impact words (FIGHT, PRESSURE, NOW). Transitions are cuts on the beat or shape morphs, plus dark wipes: a blade slash, iris close or shutter bars.

**Tone of copy:** short, hard sentences in the second person ("you"). One uncomfortable truth, one reframe, one promise. End on identity ("Become the one who shows up."), not on features.

**Voice:** deep male narrator (for example ElevenLabs Brian, Daniel or Adam). Stability about 55%, Style 0–15%, speed 1.0. No pause tags.

## 3. Geometric animation library (pick a different one for each line)

| Idea | Animation |
|---|---|
| Effort / struggle | Ball up a slope; a weight bar bending; a line pushing against a wall |
| Breaking / doubt | A circle cracking into shards; glass-like triangles shattering; static dots |
| Habits / loops | A circular arrow; an orbiting dot stuck in a loop; a clock spiral |
| Excuses | A tower of squares knocked over; a fading grey path; dots dissolving |
| Structure / discipline | Dots snapping to a grid; a hexagon lattice locking; a metronome |
| Focus | A tunnel of concentric squares zooming; an iris closing; a target reticle locking |
| Strength / growth | A staircase; a rising bar chart; a pyramid built brick by brick |
| Pressure | Walls closing in; a compressed spring; a triangle holding its shape |
| Fear | A trembling shape; a long shadow growing; a flickering outline |
| Ego | An inflating shape; a mirror reflection splitting; a crown shape shattering |
| Time | A sweeping clock hand; an hourglass of particles; a ticking segment ring |
| Momentum | A pendulum gaining swing; dominoes falling; a wave building |
| Identity / becoming | A silhouette assembling from lines; an outline filling in red |
| Choice | One line splitting into two paths; a fork of light and shadow |
| Fight / impact | Two shapes colliding with a shockwave ring; a punch trail; a bell ring burst |
| Victory / CTA | An octagon (the cage) drawing in red; an arrow rising; a light beam |

Each animation follows this rhythm: build (it draws itself), hit (on the key word), hold (subtle motion) and exit (it collapses or morphs into the next scene's shape).

## 4. Stickman library

He walks, runs, climbs, pushes, strains, collapses, sits, lies down, holds his head, shadowboxes, punches, trembles, puffs up, braces, meditates, hangs, does pull-ups, stands tall and points up.
New in v2: push-ups, jump rope, kneeling then rising, carrying a weight, a silhouette lit from behind, and a shadow version of himself (the "old you") that he walks away from.

## 5. Technical pipeline (already in this repo)

1. `video/render.py`: the scenes, captions, themes (`SCHEDULE`) and stickman.
2. Write the script, generate the voice in ElevenLabs and save the MP3 into `video/voice_src/`.
3. `video/align.py` finds the word times, `video/warp.py` builds the time map, and `WARP=out/warp.json python3 video/render.py` renders the fitted video.
4. Mux the audio, export the 9:16 version, check every frame (margins, contact sheets) and send the files.

---

## 6. Prompts to paste next time

### A. New video from a topic (Claude writes everything)

```
New TikTok video in the "Complete Fighter" style, v2 darker. Follow video/PLAYBOOK.md.
Topic: [WHAT THE VIDEO IS ABOUT]
Product / offer: [NAME, PRICE, DEADLINE, "link in bio"]
Length: 30–40 s.
1) Write a 10–12 scene script (hook in the first second, hard short lines, ends on identity + CTA).
   Show me the script plus, for each line, the geometric animation and stickman action you'd use.
2) After I approve, give me the ElevenLabs script (no pause tags) and voice settings.
3) When I send the MP3, fit the video to it, render, check frame by frame, and send me the 9:16 file.
```

### B. New video from my own script

```
Make a TikTok video in the "Complete Fighter" style, v2 darker (see video/PLAYBOOK.md) from this script.
Mark the key word of each line in red. Pick a different geometric animation and a stickman action
for every line, and change the background every 2–3 s. I'll send the ElevenLabs MP3 next.

SCRIPT:
[PASTE SCRIPT]
```

### C. Only the hook / idea stage

```
Give me 10 hooks for a dark, motivational TikTok about [TOPIC] in the Complete Fighter style.
Max 6 words each, second person, an uncomfortable truth. For the best 3, describe the opening
geometric animation and stickman action for the first 2 seconds.
```

### D. Voice file is ready

```
Here is the ElevenLabs voice for the video [attach MP3]. Fit the video and captions to it,
keep the structure, render the 9:16 version, check it, and send me the files.
```

### E. Changes after watching

```
Changes: [scene number or time] → [what to change].
Examples: "scene 4 too fast", "make the stickman bigger", "less red at the end", "harder hit on FIGHT".
```
