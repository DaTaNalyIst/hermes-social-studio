# Connect Bluesky (free)

1. In Bluesky: **Settings → Privacy and security → App passwords → Add App Password**. Name it
   `social-studio` and copy it. Never use your main password.
2. Confirm your account's email address. Bluesky refuses video uploads from unconfirmed accounts.
3. In the notebook:

```python
social("auth", "bluesky")
```

Enter your handle (`you.bsky.social`), then the app password (hidden). Press Enter at the PDS question,
unless you host your own server.

## What you can post

| Target | Notes |
|---|---|
| `bluesky:text` | up to 300 characters; a `link` becomes a link card |
| `bluesky:images` | 1-4 images, each compressed under 1 MB |
| `bluesky:video` | up to 3 minutes and 100 MB |

Links and `#hashtags` in the text become real clickable facets. Videos go through Bluesky's video
service so they are ready the moment the post appears. If that service refuses, the video is uploaded
straight to your server instead.
