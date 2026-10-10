# Connect a Telegram channel (free)

This bot **posts to your channel**. It is a different bot from the optional one you chat with Hermes
through. Use two bots so each token can do only its own job.

1. In Telegram, talk to **@BotFather** → `/newbot` → copy the token.
2. Open your channel → **Administrators → Add admin** → your new bot. Allow **Post messages**.
3. In the notebook:

```python
social("auth", "telegram")
```

Enter the token (hidden), then the channel: `@yourchannel`, or the `-100...` id of a private channel.
The command checks that the bot is an admin that can post.

## What you can post

| Target | Notes |
|---|---|
| `telegram:text` | up to 4,096 characters |
| `telegram:photo`, `telegram:album` | 1 or 2-10 photos; the caption goes on the first |
| `telegram:video` | compressed to fit the Bot API's 50 MB upload limit; plays inline |

Captions with media are limited to 1,024 characters. Public channels get a `t.me/yourchannel/123` link
for each post.

The Bot API has no view counts for channel posts, so Telegram does not take part in the
best-time learning. It uses its heuristic windows (08:00, 12:00 and 18:00-21:00 local time).
