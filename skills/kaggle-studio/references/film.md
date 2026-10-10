# Narrated films

## Steps

1. Ask for the length and format only if the user did not say: `short` = vertical 1080x1920
   (YouTube Shorts, Reels, TikTok; 20-60 s), `long` = 1920x1080 (2-15 min).
2. Write the script as scenes and show it to the user as a numbered list:
   narration line, what we see, still or video. Wait for "ok" or changes.
   - One scene = one narration sentence or two (about 6-15 seconds of speech).
   - Short films: up to 6 video scenes, the rest stills.
   - Long films: mostly stills; make about 1 scene in 5 a video.
   - Every scene's `visual` is a full prompt following prompting.md (stills: image rules,
     video scenes: video rules).
3. Write FILM.json with the file tool into {{OUT}}/films/ and run
   `{{PYTHON}} {{STUDIO}} film {{OUT}}/films/NAME.json`, then keep running `wait JOB_ID` until done.
4. Check the result: open the film's contact sheet (`sheet` in the output) with
   vision_analyze and look for weak scenes. To re-shoot only those: `film FILE.json --only s04,s09`
   (voice and the other scenes are reused, so this is quick).
5. Deliver the MEDIA line. Long films over the Telegram limit are re-encoded automatically;
   the full-quality MP4 and the .srt stay in the folder.

## FILM.json template

```json
{
  "title": "How Kenyan coffee is grown",
  "format": "short",
  "quality": "draft",
  "voice": {"engine": "turbo", "ref": null, "exaggeration": 0.5},
  "music": null,
  "subtitles": true,
  "scenes": [
    {"id": "s01", "narration": "High on the slopes of Mount Kenya, the morning mist hides a treasure.",
     "visual": "Aerial drone shot slowly gliding over green coffee terraces on a misty mountain slope at sunrise, soft golden light, cinematic",
     "type": "video"},
    {"id": "s02", "narration": "Every cherry is picked by hand, only when it turns deep red.",
     "visual": "Close-up photo of a farmer's hands holding ripe red coffee cherries, green leaves behind, soft morning light, 85mm, shallow depth of field",
     "type": "still"},
    {"id": "s03", "narration": "Kahawa bora huanza shambani.", "lang": "sw",
     "visual": "A steaming cup of coffee on a wooden table in a Nairobi cafe, warm window light, cosy, 50mm photo",
     "type": "still"}
  ]
}
```

Fields:
- `voice.engine`: `turbo` (English only, fastest, understands [laugh], [sigh]) or
  `multilingual` (23 languages incl. Swahili `sw`). Any scene with `lang` other than `en`
  switches the whole film to multilingual automatically.
- `voice.ref`: path to a 10-20 s WAV of a voice to clone (only with the owner's permission),
  e.g. a file the user attached as a Kaggle dataset under /kaggle/input/.
- `music`: path to an MP3/WAV the user owns; it is looped and ducked under the voice.
- `quality`: `draft` = klein4b stills (fast), `high` = zimage stills (slower, more realistic).
- `seed` per scene is optional.

Time guide: each still ~45 s (draft) or ~2.5 min (high), each video scene ~15 min,
voice ~1 min per minute of narration, cutting ~1-2 min per minute of film.
