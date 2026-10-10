---
name: social-publisher
description: Queue finished images, videos and films for Facebook, Instagram, Threads, YouTube, Bluesky and Telegram at the best times, get the user's approval, and report results. Use whenever the user wants to post, schedule, publish or check social media.
version: 2.0.0
platforms: [linux]
metadata:
  hermes:
    tags: [social, publishing, schedule, facebook, instagram, threads, youtube, bluesky, telegram]
    category: creative
    requires_toolsets: [terminal, file]
---

# Social Publisher

Posting is done by a separate publisher on GitHub Actions. You only write a post file and run:

    {{SOCIAL}} --json <command> ...

Always use `--json` and read `ok`, `problems`, `targets` and `next`.

## Hard rules

- NEVER ask for, type, print or store passwords, tokens, app secrets or passphrases. Accounts are
  connected by the user in the notebook's "Connect accounts" cell. If something is not connected,
  say which account and point them to that cell.
- NEVER approve on the user's behalf. Add `--approve` or run `approve` only after the user clearly
  said yes to the plan you showed (or asked you to post without review).
- Only post media the user asked for. Mention that AI-generated media is labelled as such where
  platforms support it.

## The loop

1. Make the media first with the kaggle-studio skill. Note the absolute paths it delivered.
2. Write a post file with the file tool at `{{OUT}}/posts/<short-name>.json` (template below).
   Write a caption per platform in its own style and limits (table below). Captions in the
   language the user wants; hashtags without spaces.
3. Check it: `{{SOCIAL}} --json add {{OUT}}/posts/<name>.json --preview`
   If `problems` come back, fix the file and preview again.
4. Queue it: `{{SOCIAL}} --json add {{OUT}}/posts/<name>.json`
   This makes platform versions of the media, uploads them and picks the best time for each target.
   If the reply says `staged` (sessions started from Telegram or GitHub), the post is queued
   automatically after this session ends and the user gets it for approval on their control bot.
   Tell them that, and skip steps 5-6.
5. Show the user the plan in one short list: platform, format, local time, first line of caption.
   Ask "Approve?". They can also approve from their phone by commenting `/approve` on the GitHub issue.
6. On yes: `{{SOCIAL}} --json approve <post id>`; for some platforms only:
   `{{SOCIAL}} --json approve <post id> --only instagram,youtube`.
7. Later questions: `queue`, `show <id>`, `report --days 7`.

## Post file template

```json
{
  "title": "Kenyan coffee short",
  "language": "en",
  "media": ["/kaggle/working/outputs/films/kenyan-coffee/kenyan-coffee.mp4"],
  "caption": "Default caption used when a platform has none of its own.",
  "hashtags": ["kenyancoffee", "coffee", "kenya"],
  "targets": [
    {"platform": "instagram", "format": "reel", "caption": "From cherry to cup in 45 seconds. #kenyancoffee #coffee"},
    {"platform": "facebook", "format": "reel", "caption": "How Kenyan coffee is grown, in under a minute."},
    {"platform": "youtube", "format": "short", "title": "How Kenyan coffee is grown", "caption": "Seed to cup on Mount Kenya's slopes."},
    {"platform": "threads", "format": "text", "caption": "Ever wondered how Kenyan coffee gets to your cup? Short film coming up."},
    {"platform": "bluesky", "format": "video", "caption": "Kenyan coffee, seed to cup. #coffee"},
    {"platform": "telegram", "format": "video", "caption": "New short: how Kenyan coffee is grown."}
  ]
}
```

`"targets": "auto"` picks a sensible format on every connected platform. Per target you can add
`"at": "2026-10-09 19:00"` (local time, pins it), `"when": "asap"`, or `"media": [0, 2]`
(which media files to use).

## Formats and limits

| Platform | Formats | Caption limit | Hashtags |
|---|---|---|---|
| facebook | post, photo, album (2-10), video, reel (3-90 s), story | long is fine; first line matters | 0-3 |
| instagram | image, carousel (2-10 images), reel (3 s-15 min), story (≤60 s) | 2,200 chars | max 5 |
| threads | text, image, carousel, video (≤5 min) | 500 chars | max 1 |
| youtube | short (≤3 min, vertical), video | title ≤100 chars, description ≤5,000 bytes, no < > | in tags |
| bluesky | text, images (1-4), video (≤3 min) | 300 characters | 0-2 |
| telegram | text, photo, album, video | 1,024 chars with media | optional |

Landscape videos are padded to vertical automatically for reels, shorts and stories.

## Changing plans

- `{{SOCIAL}} --json queue` shows what is waiting, with local times and why each time was chosen.
- Move one target: `{{SOCIAL}} --json move <id> instagram:reel "2026-10-09 19:00"`
- Post at the next run (every 30 min): `{{SOCIAL}} --json now <id> [target]`
- Cancel: `{{SOCIAL}} --json cancel <id> [target]`; retry a failed one: `{{SOCIAL}} --json retry <id> <target>`
- Results and engagement: `{{SOCIAL}} --json report --days 7`

## Good to know (tell the user when relevant)

- The studio starts in dry-run mode: nothing posts until the user runs `{{SOCIAL}} mode live`
  themselves after checking a dry run.
- YouTube uploads stay private until Google audits the user's API project (free, docs/setup-youtube.md).
- Threads videos need the s3 hosting option; images work without it.
- The publisher checks every 30 minutes, so a post goes out within about half an hour of its time.
