import urllib.parse

import pytest

from socialstudio import auth
from socialstudio.vault import Vault

from conftest import PASS
from fakehttp import FakeHttp


def answers(*vals):
    it = iter(vals)
    seen = []

    def ask(prompt, secret=False):
        seen.append((prompt, secret))
        return next(it)
    ask.seen = seen
    return ask


@pytest.fixture
def vault(tmp_path):
    v = Vault(tmp_path / "v.enc", PASS)
    v.create(PASS)
    return v


def test_facebook_exchanges_token_and_picks_page(vault):
    h = FakeHttp()
    h.on("GET", r"/oauth/access_token$", {"access_token": "EAAlonglived" + "x" * 20})
    h.on("GET", r"/me/accounts$", {"data": [
        {"id": "1", "name": "Other", "access_token": "EAApage1" + "y" * 20},
        {"id": "2", "name": "Coffee Studio", "access_token": "EAApage2" + "z" * 20,
         "instagram_business_account": {"id": "178", "username": "coffeestudio"}}]})
    h.on("GET", r"/debug_token$", {"data": {"expires_at": 0}})
    ask = answers("123", "appsecret", "EAAshort" + "q" * 20, "1")
    out = []
    enabled = auth.facebook(vault, h, ask, out.append)
    assert enabled == ["facebook", "instagram"]
    assert vault.get("facebook.page_id") == "2" and vault.get("instagram.user_id") == "178"
    assert vault.get("facebook.page_token").startswith("EAApage2")
    ex = h.find("GET", "oauth/access_token")[0]["params"]
    assert ex["grant_type"] == "fb_exchange_token" and ex["client_secret"] == "appsecret"
    assert [s for _, s in ask.seen] == [False, True, True, False]  # secrets are hidden prompts
    assert any("never expires" in o for o in out)


def test_youtube_pkce_and_state_check(vault):
    h = FakeHttp()
    h.on("POST", r"oauth2\.googleapis\.com/token", {"access_token": "ya29.x", "refresh_token": "1//refresh" + "r" * 20})
    h.on("GET", r"youtube/v3/channels", {"items": [{"id": "UC1", "snippet": {"title": "Coffee"}}]})
    printed = []

    def ask(prompt, secret=False):
        if "client ID" in prompt:
            return "cid"
        if "client secret" in prompt:
            return "csec"
        url = next(p for p in printed if "accounts.google.com" in p).split("\n")[2]
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert q["code_challenge_method"] == ["S256"] and q["access_type"] == ["offline"]
        return f"http://127.0.0.1:8765/?state={q['state'][0]}&code=4/abc&scope=x"

    auth.youtube(vault, h, ask, printed.append)
    tok = h.find("POST", "oauth2")[0]["data"]
    assert tok["code"] == "4/abc" and len(tok["code_verifier"]) >= 43
    assert vault.get("youtube.refresh_token").startswith("1//refresh")


def test_redirect_with_wrong_state_is_refused():
    with pytest.raises(auth.AuthError, match="state mismatch"):
        auth._code_from_redirect("http://127.0.0.1:8765/?state=evil&code=x", "good")


def test_telegram_requires_admin_bot(vault):
    h = FakeHttp()
    h.on("POST", r"/getMe$", {"ok": True, "result": {"id": 5, "username": "poster_bot"}})
    h.on("POST", r"/getChat$", {"ok": True, "result": {"id": -1001, "title": "Coffee", "username": "coffeech"}})
    h.on("POST", r"/getChatMember$", {"ok": True, "result": {"status": "member"}})
    with pytest.raises(auth.AuthError, match="not an admin"):
        auth.telegram(vault, h, answers("123456789:" + "a" * 35, "@coffeech"), lambda *_: None)
