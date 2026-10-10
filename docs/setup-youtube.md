# Connect YouTube (free)

## 1. Google Cloud project and OAuth client

1. <https://console.cloud.google.com/> → **New project** (e.g. `social-studio`).
2. **APIs & Services → Library** → enable **YouTube Data API v3**.
3. **Google Auth Platform** (OAuth consent screen) → **Get started**:
   - App name: anything. User type: **External**. Contact email: yours.
   - **Audience → Test users:** add the Google account that owns your channel.
4. **Clients → Create client** → type **Desktop app**. Note the **client ID** and **client secret**.

## 2. Connect in the notebook

```python
social("auth", "youtube")
```

1. Enter the client ID, then the secret (hidden).
2. Open the printed link and choose the channel's account. Google warns that the app isn't verified:
   click *Continue*.
3. The browser ends on `http://127.0.0.1:8765/?...` and says the site can't be reached. That is
   expected. Copy the full address and paste it into the hidden prompt.

The command checks the PKCE challenge and `state`, stores a refresh token, and turns on `youtube`.

## 3. Two limits to know about

**Uploads are private until Google audits your project.** Google forces every upload from an unaudited
API project created after July 2020 to **private**. The publisher still uploads, and the result says
"uploaded as private". You can then make each video public in YouTube Studio (the phone app works). To
make uploads public automatically:

1. Apply for the free audit through YouTube's API audit and quota extension form, linked from the
   YouTube Data API docs.
2. Once approved, set `audited = true` under `[platforms.youtube]` in `studio.toml`.

**Logins expire after 7 days while the OAuth app is in "Testing".** Google gives refresh tokens from
Testing-mode apps with external users a 7-day life. Choose one of:

- re-run `social("auth", "youtube")` once a week (`doctor` warns after 5 days); or
- under **Audience**, click **Publish app**. Without Google's verification, users see the "unverified
  app" warning, which is fine for your own channel, and the login no longer expires weekly. Google may
  ask you to verify the app before it can be used more widely.

## What you can post

| Target | Notes |
|---|---|
| `youtube:short` | up to 3 minutes; padded to 9:16 if needed; `#Shorts` is added if missing |
| `youtube:video` | any length your channel allows |

Every upload declares `containsSyntheticMedia: true` (AI-generated content), sets
`selfDeclaredMadeForKids` from `studio.toml` (default false), and uses the target's `title`, `caption`
(description) and `tags`.
