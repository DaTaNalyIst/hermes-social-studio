# Prompt rules per model

Write every prompt in English, even when the user writes in Swahili or another language.
Describe what the camera sees. No lists of tags, no "masterpiece, 8k". Negative prompts are
ignored by klein4b, zimage and wan14b (they run at CFG 1), so say what you want instead.

## Images: klein4b and zimage

Order: subject and what it is doing -> setting -> light and time of day -> camera and lens ->
style or medium -> colour palette.

- Name concrete things: "a Maasai woman in a red shuka", not "a person".
- Text in the image: put the exact words in double quotes and say where they go:
  `a poster with the headline "NAIROBI NIGHTS" in bold white letters at the top`.
  Keep text short (1-4 words). zimage spells better than klein4b.
- Photos: give a lens and light: "85mm portrait, soft window light, shallow depth of field".
- Illustration: name the medium: "flat vector illustration", "watercolour on rough paper".
- Same seed + small wording changes = controlled edits. New seed = new composition.

Example (klein4b, landscape):
`A rooftop cafe in Nairobi at dusk, string lights above wooden tables, people chatting, the city
skyline glowing orange behind them, warm evening light, wide 24mm photo, slight film grain,
teal and amber colours.`

## Video: wan14b, wan14b_hq, wan5b

One shot, one main action, one camera move per clip (5 s). Order: shot size and camera move ->
subject -> action with a verb you can see -> setting -> light -> style.

- Say how things move: "walks slowly toward the camera", "steam rises from the cup".
- Camera words that work: static shot, slow push-in, pull-back, tracking shot, pan left,
  orbit, low angle, aerial drone shot, handheld.
- Avoid: scene cuts, several actions, text on screen, tiny details that must stay stable.
- Longer stories = several clips, each its own prompt.

Example (wan14b, landscape):
`Medium tracking shot. A young Maasai runner in a red shuka runs across golden savanna grass at
sunrise, dust rising behind his feet, acacia trees in the background, warm low sunlight with
long shadows, cinematic, shallow depth of field.`

## Fixing a weak result

Read `problems` and `prompt_fix` from the review. Change ONE thing at a time:
- wrong subject or missing detail -> move it to the start of the prompt and make it concrete;
- bad hands/faces -> make them smaller in frame or describe the pose simply;
- misspelled text -> fewer words, switch to zimage;
- no or wrong motion -> one clearer verb and one camera move.
