# Connect Threads (free)

## 1. Create a Threads app

1. <https://developers.facebook.com/apps> → **Create app** → use case **Access the Threads API**.
2. In the use case's **Settings**, note the **Threads App ID** and **Threads App Secret** (they differ
   from the main app ID).
3. Add the permissions `threads_basic`, `threads_content_publish` and `threads_manage_insights`.
4. Under **Redirect Callback URLs** add `https://localhost/`.
5. **App roles → Roles → Add people → Threads testers**, add your Threads username, then accept the
   invite in the Threads app (Settings → Account → Website permissions → Invites).

## 2. Connect in the notebook

```python
social("auth", "threads")
```

Enter the app ID and (hidden) secret. If the dashboard gave you a user token, answer `y` and paste it.
Otherwise:

1. Open the link it prints and allow access.
2. Your browser lands on `https://localhost/?code=...` and shows an error page. That is expected.
3. Copy the full address and paste it into the hidden prompt.

The command checks the `state` value, swaps the code for a 60-day token, and turns on `threads`.

The publisher renews the token every week, so it never reaches 60 days. If the studio is idle for
two months, run the command again.

## What you can post

| Target | Notes |
|---|---|
| `threads:text` | up to 500 characters; one topic tag; optional `link` |
| `threads:image`, `threads:carousel` | JPEG; carousels hold 2-20 images |
| `threads:video` | up to 5 minutes; needs `[hosting] provider = "s3"` |

Threads downloads every image and video from a public link. Images work with the default Facebook
hosting, as long as Facebook is connected. Videos need a bucket ([setup-hosting.md](setup-hosting.md)).
