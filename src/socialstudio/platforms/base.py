"""Common platform adapter interface."""

from __future__ import annotations

import time
from pathlib import Path

from ..config import Config
from ..http import ApiError, Http
from ..vault import Vault


class PublishError(RuntimeError):
    def __init__(self, msg: str, transient: bool = False):
        super().__init__(msg)
        self.transient = transient


class Platform:
    name = "base"

    def __init__(self, cfg: Config, vault: Vault, http: Http, hosting=None, sleep=time.sleep):
        self.cfg, self.vault, self.http, self.hosting, self.sleep = cfg, vault, http, hosting, sleep
        self.settings = cfg.platform(self.name)

    # Required secrets for doctor
    required: tuple[str, ...] = ()

    def missing(self) -> list[str]:
        return [k for k in self.required if not self.vault.get(k)]

    def refresh(self) -> bool:
        """Renew rotating tokens if needed. Returns True when the vault changed."""
        return False

    def check(self) -> str:
        """Online check; returns a short account description."""
        raise NotImplementedError

    def publish(self, target: dict, files: list[Path], post: dict) -> dict:
        raise NotImplementedError

    def metrics(self, target: dict) -> dict | None:
        return None

    def poll(self, fn, done, failed=lambda r: False, timeout=900, every=5, what="processing"):
        t0 = time.time()
        waited = 0.0
        while True:
            r = fn()
            if done(r):
                return r
            if failed(r):
                raise PublishError(f"{self.name}: {what} failed: {r}")
            if waited > timeout:
                raise PublishError(f"{self.name}: {what} did not finish in {timeout}s", transient=True)
            self.sleep(every)
            waited += every
            if time.time() - t0 > timeout * 4:  # guard for fake sleeps in tests
                raise PublishError(f"{self.name}: {what} timed out", transient=True)


def wrap_errors(name: str, e: Exception) -> PublishError:
    if isinstance(e, PublishError):
        return e
    if isinstance(e, ApiError):
        return PublishError(str(e), transient=e.transient)
    return PublishError(f"{name}: {e}")
