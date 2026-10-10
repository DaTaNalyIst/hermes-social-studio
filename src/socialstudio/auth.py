"""Connect accounts. Run these yourself (Kaggle notebook cell, Codespaces or any terminal),
never through Hermes: secrets are typed into hidden prompts and go straight into the vault.

Every flow works without a public web server. Where an OAuth redirect is needed, your browser
lands on a page that does not load (http://127.0.0.1 or https://localhost); you copy the address
from the address bar and paste it back here. PKCE and a random `state` protect that hand-over.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import secrets
import time
import urllib.parse

from .http import ApiError, Http
from .vault import Vault


class AuthError(RuntimeError):
    pass


def default_ask(prompt: str, secret: bool = False) -> str:
    return (getpass.getpass(prompt) if secret else input(prompt)).strip()


def _code_from_redirect(pasted: str, state: str) -> str:
    q = urllib.parse.parse_qs(urllib.parse.urlparse(pasted.strip()).query)
    if "error" in q:
        raise AuthError(f"authorization refused: {q['error'][0]}")
    if q.get("state", [None])[0] != state:
        raise AuthError("state mismatch: paste the address from THIS login attempt")
    if "code" not in q:
        raise AuthError("no ?code= in that address")
    return q["code"][0].split("#")[0]


# ---------------------------------------------------------------------------
def facebook(vault: Vault, http: Http, ask=default_ask, out=print, version="v25.0"):
    out("Facebook Page + Instagram. You need: a Meta app (type Business) where you are admin, and a\n"
        "user token from the Graph API Explorer with the permissions listed in docs/setup-facebook-instagram.md.")
    app_id = ask("Meta App ID: ")
    app_secret = ask("Meta App Secret (hidden): ", secret=True)
    short = ask("User access token from Graph API Explorer (hidden): ", secret=True)
    g = f"https://graph.facebook.com/{version}"
    try:
        long = http.request("GET", f"{g}/oauth/access_token", platform="facebook", params={
            "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
            "fb_exchange_token": short})["access_token"]
        pages = http.request("GET", f"{g}/me/accounts", platform="facebook", params={
            "access_token": long, "fields": "id,name,access_token,instagram_business_account{id,username}"})
    except ApiError as e:
        raise AuthError(f"Meta refused: {e}") from None
    pages = pages.get("data", [])
    if not pages:
        raise AuthError("this token manages no Pages; grant pages_show_list and pick your Page in the Explorer")
    for i, p in enumerate(pages):
        ig = p.get("instagram_business_account") or {}
        out(f"  [{i}] {p['name']}" + (f"  (Instagram @{ig.get('username')})" if ig else "  (no Instagram linked)"))
    idx = int(ask("Which Page? number: ") or 0) if len(pages) > 1 else 0
    page = pages[idx]
    vault.set("facebook.app_id", app_id)
    vault.set("facebook.app_secret", app_secret)
    vault.set("facebook.page_id", page["id"])
    vault.set("facebook.page_name", page["name"])
    vault.set("facebook.page_token", page["access_token"])
    ig = page.get("instagram_business_account")
    if ig:
        vault.set("instagram.user_id", ig["id"])
        vault.set("instagram.username", ig.get("username", ""))
    try:
        dbg = http.request("GET", f"{g}/debug_token", platform="facebook", params={
            "input_token": page["access_token"], "access_token": f"{app_id}|{app_secret}"}).get("data", {})
        exp = dbg.get("expires_at", 0)
        out("Page token: " + ("never expires" if not exp else time.strftime("expires %Y-%m-%d", time.gmtime(exp))))
    except ApiError:
        pass
    out(f"Connected Page '{page['name']}'" + (f" and Instagram @{ig.get('username')}" if ig else ""))
    return ["facebook"] + (["instagram"] if ig else [])


def threads(vault: Vault, http: Http, ask=default_ask, out=print, host="https://graph.threads.net"):
    out("Threads. You need a Meta app with the 'Access the Threads API' use case (docs/setup-threads.md).")
    app_id = ask("Threads App ID: ")
    app_secret = ask("Threads App Secret (hidden): ", secret=True)
    mode = ask("Do you already have a Threads user token from the App Dashboard? [y/N]: ").lower()
    try:
        if mode.startswith("y"):
            short = ask("Threads user token (hidden): ", secret=True)
        else:
            redirect = ask("Redirect URI exactly as registered in the app [https://localhost/]: ") or "https://localhost/"
            state = secrets.token_urlsafe(16)
            url = "https://threads.com/oauth/authorize?" + urllib.parse.urlencode({
                "client_id": app_id, "redirect_uri": redirect, "response_type": "code", "state": state,
                "scope": "threads_basic,threads_content_publish,threads_manage_insights"})
            out("\n1. Open this link and allow access:\n" + url +
                "\n2. The browser then shows an error page. Copy the FULL address from the address bar.\n")
            code = _code_from_redirect(ask("Paste that address (hidden): ", secret=True), state)
            r = http.request("POST", f"{host}/oauth/access_token", platform="threads", data={
                "client_id": app_id, "client_secret": app_secret, "code": code,
                "grant_type": "authorization_code", "redirect_uri": redirect})
            short = r["access_token"]
        long = http.request("GET", f"{host}/access_token", platform="threads", params={
            "grant_type": "th_exchange_token", "client_secret": app_secret, "access_token": short})
        token = long.get("access_token", short)
        me = http.request("GET", f"{host}/v1.0/me", platform="threads",
                          params={"fields": "id,username", "access_token": token})
    except ApiError as e:
        raise AuthError(f"Threads refused: {e}") from None
    vault.set("threads.app_id", app_id)
    vault.set("threads.app_secret", app_secret)
    vault.set("threads.token", token)
    vault.set("threads.token_created", str(int(time.time())))
    vault.set("threads.user_id", me["id"])
    vault.set("threads.username", me.get("username", ""))
    out(f"Connected Threads @{me.get('username')}. The publisher renews the token weekly (it lasts 60 days).")
    return ["threads"]


def youtube(vault: Vault, http: Http, ask=default_ask, out=print):
    out("YouTube. You need a Google Cloud OAuth client of type 'Desktop app' (docs/setup-youtube.md).")
    cid = ask("OAuth client ID: ")
    csec = ask("OAuth client secret (hidden): ", secret=True)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    redirect = "http://127.0.0.1:8765/"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "state": state,
        "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
        "access_type": "offline", "prompt": "consent", "code_challenge": challenge,
        "code_challenge_method": "S256"})
    out("\n1. Open this link, pick your channel's account and allow access:\n" + url +
        "\n2. The browser then shows 'This site can't be reached' at 127.0.0.1. That is expected:"
        "\n   copy the FULL address from the address bar.\n")
    code = _code_from_redirect(ask("Paste that address (hidden): ", secret=True), state)
    try:
        tok = http.request("POST", "https://oauth2.googleapis.com/token", platform="youtube", data={
            "client_id": cid, "client_secret": csec, "code": code, "code_verifier": verifier,
            "redirect_uri": redirect, "grant_type": "authorization_code"})
        if not tok.get("refresh_token"):
            raise AuthError("Google returned no refresh token; remove the app's access at "
                            "https://myaccount.google.com/permissions and try again")
        ch = http.request("GET", "https://www.googleapis.com/youtube/v3/channels", platform="youtube",
                          params={"part": "snippet", "mine": "true"},
                          headers={"Authorization": f"Bearer {tok['access_token']}"})
    except ApiError as e:
        raise AuthError(f"Google refused: {e}") from None
    item = (ch.get("items") or [{}])[0]
    vault.set("youtube.client_id", cid)
    vault.set("youtube.client_secret", csec)
    vault.set("youtube.refresh_token", tok["refresh_token"])
    vault.set("youtube.channel_id", item.get("id", ""))
    vault.set("youtube.channel_title", item.get("snippet", {}).get("title", ""))
    vault.set("youtube.authorized_at", str(int(time.time())))
    out(f"Connected YouTube channel '{item.get('snippet', {}).get('title')}'.")
    out("If the OAuth app is still in 'Testing', Google expires this login after 7 days; see docs/setup-youtube.md.")
    return ["youtube"]


def bluesky(vault: Vault, http: Http, ask=default_ask, out=print, pds="https://bsky.social"):
    out("Bluesky. Create an App Password in Bluesky: Settings > Privacy and security > App passwords.")
    handle = ask("Handle (e.g. you.bsky.social): ").lstrip("@")
    pw = ask("App password (hidden): ", secret=True)
    pds = ask(f"PDS URL [{pds}]: ") or pds
    try:
        s = http.request("POST", f"{pds.rstrip('/')}/xrpc/com.atproto.server.createSession", platform="bluesky",
                         json_body={"identifier": handle, "password": pw})
    except ApiError as e:
        raise AuthError(f"Bluesky refused: {e}") from None
    if s.get("emailConfirmed") is False:
        out("Note: confirm your Bluesky email address, or video uploads will be refused.")
    vault.set("bluesky.handle", s.get("handle", handle))
    vault.set("bluesky.app_password", pw)
    vault.set("bluesky.pds", pds.rstrip("/"))
    out(f"Connected Bluesky @{s.get('handle', handle)}.")
    return ["bluesky"]


def telegram(vault: Vault, http: Http, ask=default_ask, out=print):
    out("Telegram channel. Create a bot with @BotFather and add it to your channel as an admin "
        "allowed to post messages.")
    tok = ask("Bot token (hidden): ", secret=True)
    chat = ask("Channel (@username, or -100... id for private channels): ")
    base = f"https://api.telegram.org/bot{tok}"
    try:
        me = http.request("POST", f"{base}/getMe", platform="telegram")["result"]
        info = http.request("POST", f"{base}/getChat", data={"chat_id": chat}, platform="telegram")["result"]
        member = http.request("POST", f"{base}/getChatMember", data={"chat_id": chat, "user_id": me["id"]},
                              platform="telegram")["result"]
    except (ApiError, KeyError) as e:
        raise AuthError(f"Telegram refused: {e}") from None
    if member.get("status") != "administrator" or member.get("can_post_messages") is False:
        raise AuthError("the bot is not an admin that can post in this channel")
    vault.set("telegram.bot_token", tok)
    vault.set("telegram.chat_id", str(info["id"]))
    vault.set("telegram.chat_username", info.get("username", ""))
    out(f"Connected bot @{me.get('username')} to '{info.get('title')}'.")
    return ["telegram"]


def hosting_s3(vault: Vault, http: Http, ask=default_ask, out=print):
    out("S3-compatible bucket for public media links (Backblaze B2's free 10 GB works; docs/setup-hosting.md).\n"
        "Endpoint, region and bucket go in studio.toml [hosting]; the keys go in the vault.")
    vault.set("hosting.key_id", ask("Access key ID (hidden): ", secret=True))
    vault.set("hosting.secret", ask("Secret access key (hidden): ", secret=True))
    return ["hosting"]


def control(vault: Vault, http: Http, ask=default_ask, out=print):
    out("Control bot: you send it /studio, /autopilot, /status and approve posts with its buttons.\n"
        "Make one with @BotFather (it may be the same bot that posts to your channel, NOT your Hermes chat bot).")
    tok = ask("Control bot token (hidden): ", secret=True)
    users = ask("Your Telegram user id (from @userinfobot; comma-separate several): ").replace(" ", "")
    try:
        me = http.request("POST", f"https://api.telegram.org/bot{tok}/getMe", platform="telegram")["result"]
    except (ApiError, KeyError) as e:
        raise AuthError(f"Telegram refused the token: {e}") from None
    if not all(u.lstrip("-").isdigit() for u in users.split(",") if u):
        raise AuthError("user ids are numbers, e.g. 123456789")
    mode = (ask("Instant replies through a free Cloudflare Worker (w), or replies within ~30 min with no extra "
                "account (p)? [w/p]: ") or "w").lower()[:1]
    vault.set("control.bot_token", tok)
    vault.set("control.allowed_users", users)
    vault.set("control.mode", "webhook" if mode == "w" else "poll")
    if mode == "w":
        secret = secrets.token_urlsafe(32)
        vault.set("control.webhook_secret", secret)
        out("\nWEBHOOK_SECRET for the Cloudflare Worker (copy it now, then clear this cell's output):\n"
            f"    {secret}\n")
    out(f"Control bot @{me.get('username')} saved. Next: docs/telegram-control.md")
    return ["control"]


def hermes_chat(vault: Vault, http: Http, ask=default_ask, out=print):
    out("Your Hermes chat bot (the one you already talk to Hermes through). Sessions started from\n"
        "Telegram or GitHub need its token; it is copied into a private Kaggle dataset by the\n"
        "`studio` workflow (mode: keys).")
    tok = ask("Hermes chat bot token (hidden): ", secret=True)
    users = ask("Your Telegram user id(s), comma-separated: ").replace(" ", "")
    try:
        http.request("POST", f"https://api.telegram.org/bot{tok}/getMe", platform="telegram")
    except ApiError as e:
        raise AuthError(f"Telegram refused the token: {e}") from None
    vault.set("hermes.chat_bot_token", tok)
    vault.set("hermes.chat_allowed_users", users)
    out("Saved. Now run the 'studio' workflow on GitHub with mode = keys once.")
    return ["hermes-chat"]


FLOWS = {"facebook": facebook, "instagram": facebook, "threads": threads, "youtube": youtube,
         "bluesky": bluesky, "telegram": telegram, "hosting": hosting_s3, "control": control,
         "hermes-chat": hermes_chat}
