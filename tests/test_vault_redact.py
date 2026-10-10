import json

import pytest

from socialstudio import redact
from socialstudio.vault import Vault, VaultError

from conftest import PASS


def test_roundtrip_and_names_are_encrypted(tmp_path):
    v = Vault(tmp_path / "vault.enc", PASS)
    v.create(PASS)
    v.set("facebook.page_token", "EAAsupersecrettokenvalue123456")
    v.save()
    raw = (tmp_path / "vault.enc").read_text()
    assert "facebook" not in raw and "EAAsuper" not in raw
    blob = json.loads(raw)
    assert blob["kdf"] == "scrypt" and blob["n"] == 2 ** 15
    v2 = Vault(tmp_path / "vault.enc", PASS)
    assert v2.get("facebook.page_token") == "EAAsupersecrettokenvalue123456"
    assert v2.names() == ["facebook.page_token"]


def test_wrong_passphrase(tmp_path):
    Vault(tmp_path / "v.enc", PASS).create(PASS)
    with pytest.raises(VaultError, match="wrong vault passphrase"):
        Vault(tmp_path / "v.enc", "not the right passphrase at all").load()


def test_weak_passphrase_refused(tmp_path):
    with pytest.raises(VaultError, match="weak"):
        Vault(tmp_path / "v.enc").create("short")


def test_merge_save_keeps_other_writers_changes(tmp_path):
    path = tmp_path / "v.enc"
    a = Vault(path, PASS)
    a.create(PASS)
    a.set("x", "1111111")
    a.save()
    b = Vault(path, PASS)
    b.load()
    b.set("threads.token", "THnewtoken" + "z" * 40)       # e.g. the publisher refreshed a token
    c = Vault(path, PASS)
    c.set("telegram.bot_token", "123456789:" + "q" * 35)  # meanwhile the user connected Telegram
    c.save()
    assert b.merge_save()
    final = Vault(path, PASS)
    assert final.get("telegram.bot_token") and final.get("threads.token").startswith("THnew")
    assert final.get("x") == "1111111"


def test_registered_secrets_are_masked(tmp_path):
    v = Vault(tmp_path / "v.enc", PASS)
    v.create(PASS)
    v.set("bluesky.app_password", "abcd-efgh-ijkl-mnop")
    assert "abcd-efgh" not in redact.redact("login failed for abcd-efgh-ijkl-mnop")


@pytest.mark.parametrize("text", [
    "token EAAB" + "x" * 40,
    "https://api.telegram.org/bot123456789:AAEexampleexampleexampleexample123/sendMessage",
    "Authorization: Bearer ya29.a0AfH6SMexampleexampleexample",
    "refresh 1//0gexampleexampleexampleexample",
    "client GOCSPX-abcdefghijklmnop",
    "url ...&X-Amz-Signature=" + "a" * 64,
    "github_pat_" + "A" * 40,
    "?access_token=abcdef123456&x=1",
])
def test_patterns(text):
    out = redact.redact(text)
    assert "[REDACTED]" in out
    for secret in ("EAAB" + "x" * 40, "AAEexample", "ya29.a0", "1//0g", "GOCSPX-abc", "a" * 64, "A" * 40, "abcdef123456"):
        assert secret not in out
