"""Encrypted credential vault, committed to the repo as state/vault.enc.

File format (JSON): {"v": 1, "kdf": "scrypt", "n": 32768, "r": 8, "p": 1, "salt": b64, "data": fernet-token}
The whole secret map, names included, is inside the Fernet token. The key is derived from the
passphrase with scrypt. GitHub Actions and Kaggle read the passphrase from the
SOCIAL_VAULT_PASSPHRASE secret; interactively it is asked with a hidden prompt.
"""

from __future__ import annotations

import base64
import getpass
import json
import os
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import redact

N, R, P = 2 ** 15, 8, 1
MIN_PASSPHRASE = 16
ENV = "SOCIAL_VAULT_PASSPHRASE"


class VaultError(RuntimeError):
    pass


def _key(passphrase: str, salt: bytes, n=N, r=R, p=P) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=n, r=r, p=p)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))


def passphrase_strength_problem(pw: str) -> str | None:
    if len(pw) < MIN_PASSPHRASE:
        return f"use at least {MIN_PASSPHRASE} characters (four random words work well)"
    if len(set(pw)) < 8:
        return "too repetitive"
    return None


class Vault:
    def __init__(self, path: Path, passphrase: str | None = None):
        self.path = Path(path)
        self._pw = passphrase
        self._data: dict | None = None
        self.dirty = False
        self._changed: dict = {}

    # -- passphrase ---------------------------------------------------------
    def _passphrase(self) -> str:
        if self._pw:
            return self._pw
        pw = os.environ.get(ENV)
        if not pw:
            pw = getpass.getpass("Vault passphrase: ")
        if not pw:
            raise VaultError(f"no vault passphrase (set {ENV} or type it)")
        self._pw = pw
        return pw

    # -- io -----------------------------------------------------------------
    def exists(self) -> bool:
        return self.path.exists()

    def create(self, passphrase: str):
        if self.exists():
            raise VaultError(f"{self.path} already exists")
        problem = passphrase_strength_problem(passphrase)
        if problem:
            raise VaultError(f"weak passphrase: {problem}")
        self._pw = passphrase
        self._data = {"_meta": {"created": int(time.time())}}
        self.save(force=True)

    def load(self) -> dict:
        if self._data is not None:
            return self._data
        if not self.exists():
            raise VaultError(f"no vault at {self.path}; run `social init`")
        blob = json.loads(self.path.read_text())
        salt = base64.b64decode(blob["salt"])
        f = Fernet(_key(self._passphrase(), salt, blob.get("n", N), blob.get("r", R), blob.get("p", P)))
        try:
            self._data = json.loads(f.decrypt(blob["data"].encode()))
        except InvalidToken:
            raise VaultError("wrong vault passphrase (or the vault file is damaged)") from None
        for k, v in self._data.items():
            if not k.startswith("_") and isinstance(v, str):
                redact.register(v)
        return self._data

    def save(self, force: bool = False):
        if not (self.dirty or force):
            return False
        salt = os.urandom(16)
        token = Fernet(_key(self._passphrase(), salt)).encrypt(json.dumps(self._data).encode())
        blob = {"v": 1, "kdf": "scrypt", "n": N, "r": R, "p": P,
                "salt": base64.b64encode(salt).decode(), "data": token.decode()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(blob, f)
        os.replace(tmp, self.path)
        self.dirty = False
        return True

    # -- access -------------------------------------------------------------
    def get(self, name: str, default=None):
        v = self.load().get(name, default)
        if isinstance(v, str):
            redact.register(v)
        return v

    def require(self, name: str) -> str:
        v = self.get(name)
        if not v:
            raise VaultError(f"missing secret {name}; run the matching `social auth ...`")
        return v

    def set(self, name: str, value):
        if name.startswith("_"):
            raise VaultError("names starting with _ are reserved")
        data = self.load()
        if data.get(name) != value:
            data[name] = value
            self.dirty = True
            self._changed[name] = value
        if isinstance(value, str):
            redact.register(value)

    def delete(self, name: str):
        if self.load().pop(name, None) is not None:
            self.dirty = True
            self._changed[name] = None

    def merge_save(self) -> bool:
        """Re-read the vault file (it may have been replaced by a git reset), re-apply only the
        keys changed in this process, and save. Used inside gitsync.transact."""
        if not self._changed:
            return False
        changed = dict(self._changed)
        self._data = None
        data = self.load()
        for k, v in changed.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        self.dirty = True
        self.save()
        self._changed.clear()
        return True

    def names(self) -> list[str]:
        return sorted(k for k in self.load() if not k.startswith("_"))

    def change_passphrase(self, new: str):
        problem = passphrase_strength_problem(new)
        if problem:
            raise VaultError(f"weak passphrase: {problem}")
        self.load()
        self._pw = new
        self.save(force=True)
