# Connect a Facebook Page and Instagram (free)

One Meta app and one token cover both: the Page you manage and the Instagram professional account
linked to it. The app can stay in **Development mode**: as its admin you may use these permissions on
your own Page and Instagram account without App Review.

## Before you start

- A **Facebook Page** you are an admin of.
- An **Instagram professional account** (Business or Creator: Instagram app → Settings → Account type
  and tools) **linked to that Page** (Page settings → Linked accounts → Instagram, or Meta Business Suite).

## 1. Create the Meta app

1. <https://developers.facebook.com/apps> → **Create app**.
2. Use case: **Other** → app type **Business** → name it, e.g. `Social Studio`.
3. In the app dashboard add the products **Facebook Login for Business** and **Instagram**
   (choose *API setup with Facebook login*). Facebook Login for Business is what Instagram's binary
   video upload for Reels and Stories requires.
4. **App settings → Basic**: note the **App ID** and **App Secret**.

## 2. Get a user token in the Graph API Explorer

1. <https://developers.facebook.com/tools/explorer> → choose your app (top right).
2. **Add permissions:** `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
   `read_insights`, `business_management`, `instagram_basic`, `instagram_content_publish`,
   `instagram_manage_insights`.
3. **Generate Access Token**, log in, and in the dialog select your Page and Instagram account.
4. Copy the token. It lasts about an hour, which is enough.

## 3. Connect in the notebook

```python
social("auth", "facebook")
```

Enter the App ID, then (hidden) the App Secret and the token. The command:

1. exchanges the token for a long-lived one;
2. lists your Pages, and you pick one;
3. stores the Page token (it does not expire) and the linked Instagram account in the vault;
4. turns on `facebook` and `instagram` in `studio.toml`.

Check it with `social("doctor", "--online")`: you should see your Page name and `@instagram` with the
number of posts published in the last 24 hours (the limit is 100).

## What you can post

| Target | Notes |
|---|---|
| `facebook:post` | text, optional `link` |
| `facebook:photo`, `facebook:album` | 1 or 2-10 images |
| `facebook:video` | any length the Page allows |
| `facebook:reel` | 3-90 s, converted to 1080x1920 |
| `facebook:story` | an image, or a video of 3-60 s |
| `instagram:image` | aspect is cropped into 4:5 to 1.91:1 |
| `instagram:carousel` | 2-10 images, cropped square so all match |
| `instagram:reel` | 3 s to 15 min, 1080x1920, shared to the feed |
| `instagram:story` | an image or a video of 3-60 s |

Instagram images need a public link. By default the publisher uploads the image to your Page as an
*unpublished* photo, hands its link to Instagram, and deletes it right after; nothing appears on the
Page. You can switch to a bucket instead ([setup-hosting.md](setup-hosting.md)).

## If it stops working

- **Error 190 (token invalid):** happens after a Facebook password change, or if you removed the app.
  Run `social("auth", "facebook")` again.
- **Error 10 or 200 (permission):** a permission was not granted in step 2. Generate a new token with
  all of them.
- **Instagram "Media download failed":** the image link was not reachable. Check `[hosting]`.
