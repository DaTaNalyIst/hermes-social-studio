# Autopilot: posts every day without you

Every day at `run_at`, the publisher on GitHub starts a Kaggle run. That run creates the posts in
your weekly plan. You get each one on your control bot with **Approve** and **Reject** buttons, and
each is posted at the best time that day.

```
06:00  GitHub starts a Kaggle run (autopilot-launch, inside the publish workflow)
06:10  Kaggle: studio ready -> Hermes makes today's posts -> stages them in the outbox -> run ends
~07:30 GitHub collects the run: makes platform versions, uploads media, plans the best times
       -> your control bot sends a preview of each post with Approve / Reject
       -> approval: "timeout" = approved automatically 3 h later unless you press Reject
12:00+ the publisher posts each target at its slot (within about 30 minutes)
03:23 UTC  insights: engagement from earlier posts improves future slot choices
```

The Kaggle run never receives your GitHub token or the vault passphrase. It carries a snapshot of the
repo (code, skills, settings, the queue and Hermes' memory, never the vault), and it hands the posts
back as files in its output. Hermes' memory is saved after each run and restored on the next one.

## 1. One-time setup

1. **Kaggle API token:** at <https://www.kaggle.com/settings> → *API* → create a token. Add it to
   your GitHub repo as the secret `KAGGLE_API_TOKEN` (*Settings → Secrets and variables → Actions*).
2. **studio.toml:** set `username` under `[kaggle]` to your Kaggle username.
3. **Control bot:** set it up as described in [telegram-control.md](telegram-control.md). It is how you
   approve posts and start sessions.
4. **The plan:** edit `autopilot.toml` (below) and set `enabled = true`. You can also send `/resume` to
   the control bot.
5. **Check:** run `social autopilot` in the notebook. It shows today's plan and any problems. To
   start a run right away instead of waiting until `run_at`, run the **studio** workflow with mode
   `autopilot`, or send `/autopilot` to the control bot.

## 2. The plan (`autopilot.toml`)

```toml
[autopilot]
enabled = true
run_at = "06:00"            # audience-local time the daily run starts
approval = "timeout"        # required | timeout | auto
approve_after_hours = 3
language = "en"
brand = "A Nairobi coffee roaster sharing coffee culture"
audience = "young professionals in East Africa who love coffee"
style = "warm, cinematic, natural light, authentic African settings"
voice = "friendly and curious"
avoid = ["politics", "real, identifiable people", "medical or financial claims"]
topics = ["how coffee cherries are picked", "a barista's morning routine"]

[[slot]]
id = "short"
days = ["mon", "wed", "fri"]
kind = "short"              # short | clip | image | carousel | text
targets = ["instagram:reel", "facebook:reel", "youtube:short", "telegram:video"]

[[slot]]
id = "image"
days = ["tue", "thu", "sat", "sun"]
kind = "image"
targets = ["instagram:image", "facebook:photo", "telegram:photo"]
```

| `kind` | What Hermes makes | GPU time per run (incl. ~10 min setup) |
|---|---|---|
| `short` | a narrated vertical film, 30-45 s, up to 2 video scenes | about 45-75 min |
| `clip` | one 5-second vertical video | about 25-35 min |
| `image` | 3 drafts, the best one re-rendered with Z-Image | about 15-20 min |
| `carousel` | 3-5 matching images | about 15-25 min |
| `text` | a text post, no media | about 12 min |

The example plan uses about 5 GPU-hours a week, out of Kaggle's ~30.

Several slots on the same day run back to back in one Kaggle session. Hermes picks a fresh topic
each time. It works from `topics` and the brand, and skips the last 30 topics it used, which are kept
in `state/autopilot_history.json`.

## 3. Approval modes

| `approval` | What happens |
|---|---|
| `required` | Nothing posts until you press **Approve**. |
| `timeout` (default) | You get the preview. If you don't press **Reject** within `approve_after_hours`, the post is approved. Its slots are always planned after that window ends. |
| `auto` | Posts are scheduled straight away; you get the preview for information. |

`general.mode = "dry-run"` in `studio.toml` still overrides everything: nothing is posted until you
switch to `live`.

## 4. When something goes wrong

The control bot tells you about each of these:

- **Daily run couldn't start:** for example, your weekly GPU quota is used up, or a run is still going.
- **Run finished with problems:** you get one line per slot that failed, plus a link to the Kaggle log.
- **A target failed to publish:** the message says how to retry.

Send `/status` at any time. A run that has been gone for 13 hours without finishing is marked lost.
