# The control bot: start the studio and approve posts from Telegram

You will have up to three bots. Keep them apart:

| Bot | Used for | Token lives in |
|---|---|---|
| **Hermes chat bot** (you already have it) | talking to Hermes while a Kaggle session runs | Kaggle secrets, plus a private Kaggle dataset for sessions started from Telegram |
| **Control bot** (new) | `/studio`, `/autopilot`, `/status`, and the Approve/Reject buttons | the encrypted vault |
| Channel bot | posting to your Telegram channel | the vault (it can be the same bot as the control bot) |

Commands:

| Command | What it does |
|---|---|
| `/studio` | Starts Hermes on Kaggle (about 10 minutes). Hermes messages you on the chat bot when it's ready and switches off after 30 quiet minutes, or when you tell it "switch off". |
| `/studio 60` | Same, with a 60-minute idle limit. |
| `/autopilot`, `/autopilot tomorrow` | Makes today's (or tomorrow's) planned posts now. |
| `/status` | Shows what's running, what's queued and the autopilot state. |
| `/queue` | Lists the next posts and their times. |
| `/pause`, `/resume` | Stops or restarts the daily autopilot. |
| Approve / Reject buttons | Under every new post. |

Posts you ask Hermes for in a session started this way are queued about 30 minutes after the
session ends. They then come to the control bot for approval.

## 1. Create the control bot and save it

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. In the notebook (or any terminal with the repo), run `social("auth", "control")`:
   - paste the token (hidden), then enter your user id (from **@userinfobot**);
   - choose **w** for instant replies through Cloudflare (recommended, steps 3-4), or **p** for
     replies within ~30 minutes with no extra account (then skip to step 5).
   - With **w**, it prints a `WEBHOOK_SECRET`. Copy it, then clear the cell's output.
3. Save your Hermes chat bot too, so sessions started from Telegram can use it:
   `social("auth", "hermes-chat")`. Then on GitHub run **Actions → studio → Run workflow** with mode
   **keys**. This copies the chat token into a **private** Kaggle dataset, `hermes-studio-keys`, which
   only your account can see. Launched sessions attach that dataset.

## 2. The Cloudflare Worker (free, instant replies)

1. **GitHub token for the Worker:** go to <https://github.com/settings/personal-access-tokens/new>.
   - Repository access: this repo only.
   - Permissions → **Actions: Read and write**. Nothing else.
2. **Create the Worker:** go to <https://dash.cloudflare.com> (a free account; no card needed for
   Workers).
   - Open **Workers & Pages → Create → Worker** (Hello World), name it e.g. `studio-relay`, and
     click **Deploy**.
   - Click **Edit code**, replace everything with the contents of `launcher/worker.js`, and click
     **Deploy**.
3. **Add the settings:** go to **Settings → Variables and Secrets** and add:

| Name | Type | Value |
|---|---|---|
| `WEBHOOK_SECRET` | Secret | from step 1.2 |
| `ALLOWED_USERS` | Text | your Telegram user id |
| `GITHUB_TOKEN` | Secret | the token from step 2.1 |
| `GITHUB_REPO` | Text | `you/hermes-social-studio` |

4. **Point the bot at the Worker:** on GitHub run **Actions → telegram → Run workflow**. Leave `update`
   empty and set `webhook_url` to your Worker's address, e.g.
   `https://studio-relay.you.workers.dev`.
5. **Test it:** send `/help` to your control bot. "On it..." comes back at once, then the full help
   about a minute later.

The Worker checks Telegram's secret header and your user id. It holds no bot token and can only
start the `telegram` workflow. That workflow checks your user id again against the vault.

Cloudflare's free plan allows 100,000 Worker requests a day, far more than you will use.

## 3. Without Cloudflare (poll mode)

If you chose **p**, the publisher reads your messages each time it runs (every 30 minutes). Replies
and launches happen then. Everything else works the same.

## Costs

- **GitHub Actions:** each command starts one short run of about a minute.
- **Kaggle:** a session started with `/studio` uses about 10 minutes of GPU time to start, then runs
  until it is idle.
