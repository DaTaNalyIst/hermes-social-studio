---
name: kaggle-studio
description: Make images, video clips and narrated films on this Kaggle notebook's free GPU with studio.py. Use for any request to draw, design, render, animate, film, narrate or voice something.
version: 2.0.0
platforms: [linux]
metadata:
  hermes:
    tags: [image, video, film, voice, kaggle, comfyui]
    category: creative
    requires_toolsets: [terminal]
---

# Kaggle Studio

You run inside a Kaggle notebook. GPU 0 runs you; GPU 1 runs a ComfyUI render server.
Everything is rendered with ONE command, always with the terminal tool:

    {{PYTHON}} {{STUDIO}} <command> ...

Use exactly that interpreter path (Hermes' own python lacks the studio's libraries). Never install
models, ComfyUI or Python packages yourself, and never call ComfyUI directly.
Every command prints JSON. Read `status`, `next`, `deliver` and `error`.

## Pick the command

| The user wants | Run |
|---|---|
| Images, ideas, variations, drafts | `image "PROMPT" --n 4` (preset klein4b, about 45 s each) |
| A photoreal or final image, people, products, food, readable text | `image "PROMPT" --preset zimage --n 2` (about 2.5 min each) |
| A video clip (default) | `video "PROMPT"` (wan14b, 5 s, about 14 min) |
| A quick motion test | `video "PROMPT" --preset wan5b --seconds 2` |
| A final, best-quality clip of an approved prompt | `video "PROMPT" --preset wan14b_hq --seed N` (about 70 min, warn first) |
| A narrated short or long film | write a script, then `film FILM.json` (see references/film.md) |
| Voice only | a film whose scenes are all stills is the cheapest way |

Options: `--aspect square|portrait|landscape` (images default square, video landscape; phone/
Reels/TikTok/Shorts = portrait), `--seed N`, `--seconds S` (video, max 5 for wan14b), `--smooth`
(adds a 32 fps copy), `--n K`.

## The loop (follow it every time)

1. Decide the command. Ask at most ONE question, and only if the shape is truly unclear.
2. Write the prompt in English, following references/prompting.md. Keep it 30-90 words.
3. For video or film, first tell the user the time estimate in one short line.
4. Run the command. It returns within about 150 s. While `status` is not `done`, `partial`
   or `failed`, run exactly the command in `next` (`wait JOB_ID`) again. Do not start another
   render meanwhile.
5. When done, run `review JOB_ID`. Keep results with verdict `keep`.
   If nothing is kept, run `rerun JOB_ID --item ITEM --prompt "<prompt_fix merged into your prompt>"`
   (new seed is automatic). At most 2 extra rounds; then deliver the best you have.
6. Reply briefly: one sentence per result, then the MEDIA lines from `deliver`, each on its own
   line, exactly as printed. Mention preset and seed so the user can ask for changes.

To send any other file: `deliver /abs/path` prints its MEDIA line.

## When something goes wrong

- `failed` with "out of memory" on video: retry with `--quant Q3_K_M`, or fewer `--seconds`,
  or `--preset wan5b`. On images: a smaller `--width/--height`.
- "not enough disk": run `disk --free-gb 30`, then retry.
- ComfyUI or Ollama down: run `status` and tell the user to re-run the notebook's setup cell.
- Any other error: show the user the `error`/`message` text in one line and suggest a fix.

## Budget

The notebook has about 30 GPU-hours a week and this session ends itself when idle.
Images are cheap; each wan14b clip costs about 15 minutes. Ask before more than 4 clips,
before wan14b_hq, or before films longer than 3 minutes. `status` shows hours used.

Files are saved under {{OUT}} and kept with the notebook version when the session ends.

## Switching off

If the user asks you to switch off, stop, shut down or end the session: say goodbye in one line,
then run `{{PYTHON}} {{SERVE}} --stop-soon`. The session ends about a minute later and anything
staged for posting is queued afterwards.
