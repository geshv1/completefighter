# Launch video: The Complete Fighter

- `out/launch_tiktok_vertical.mp4`: ready to upload to TikTok (9:16, voice included)
- `out/launch_with_voice.mp4`: 1440×1080 with the voice, 38.5 s (the last frame holds for 1 s)
- `out/launch_no_audio.mp4`: the same video without sound
- `out/voiceover.wav`: the voice on its own
- `out/cover.png`: TikTok cover (scene 1 with the hook)
- `out/voiceover.txt` / `out/voice_timings.json`: when each line and word is spoken

Voice: ElevenLabs recording in `voice_src/elevenlabs.mp3`, 38.5 s video.
`align.py` finds when each word is spoken (silences + Whisper + matching against a Kokoro
reference), `warp.py` builds a smooth time map so every caption word lands on the voice,
then `WARP=out/warp.json python3 render.py` renders the fitted video.
(`voice.py` made the earlier Kokoro voice and is still used as the timing reference.)

Re-render: `pip install pycairo && python3 video/render.py` (needs ffmpeg).
Single stills for checking: `python3 video/render.py --frames 1.4 17.2 47.9`.
Every timing lives at the top of each scene in `render.py`. To re-sync to a real voice track,
change the word times in the `Wd(...)` captions and the matching animation times.

Look: backgrounds switch between black, bone and red every 2-4 s with geometric wipes and moving
patterns; a stickman acts out most scenes; key words get a zoom punch and ring burst; a scene
counter and progress bar run along the top. Theme schedule: `SCHEDULE` in render.py.
