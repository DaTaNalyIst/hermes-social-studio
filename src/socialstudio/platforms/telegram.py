"""Telegram channel via the Bot API: text, photo, album (media group) and video posts."""

from __future__ import annotations

import json
from pathlib import Path

from ..http import ApiError
from .base import Platform, PublishError

API = "https://api.telegram.org"


class Telegram(Platform):
    name = "telegram"
    required = ("telegram.bot_token", "telegram.chat_id")

    def call(self, method, data=None, files=None):
        tok = self.vault.require("telegram.bot_token")
        try:
            r = self.http.request("POST", f"{API}/bot{tok}/{method}", data=data, files=files, platform=self.name)
        except ApiError as e:
            raise PublishError(str(e), transient=e.transient) from None
        if not r.get("ok", False):
            raise PublishError(f"telegram: {r.get('description')}")
        return r["result"]

    @property
    def chat(self):
        return self.vault.require("telegram.chat_id")

    def check(self) -> str:
        me = self.call("getMe")
        chat = self.call("getChat", {"chat_id": self.chat})
        return f"bot @{me.get('username')} -> {chat.get('title')}"

    def _url(self, msg_id):
        user = self.vault.get("telegram.chat_username")
        return f"https://t.me/{user.lstrip('@')}/{msg_id}" if user else None

    def publish(self, target, files, post):
        fmt, cap = target["format"], target.get("caption", "")
        if fmt == "text":
            r = self.call("sendMessage", {"chat_id": self.chat, "text": cap})
        elif fmt == "photo":
            with open(files[0], "rb") as f:
                r = self.call("sendPhoto", {"chat_id": self.chat, "caption": cap},
                              {"photo": (Path(files[0]).name, f, "image/jpeg")})
        elif fmt == "video":
            a = (target.get("assets") or [{}])[0]
            with open(files[0], "rb") as f:
                r = self.call("sendVideo", {"chat_id": self.chat, "caption": cap, "supports_streaming": "true",
                                            "width": a.get("width", 0), "height": a.get("height", 0),
                                            "duration": int(a.get("duration", 0))},
                              {"video": (Path(files[0]).name, f, "video/mp4")})
        elif fmt == "album":
            handles = {f"f{i}": open(p, "rb") for i, p in enumerate(files)}
            try:
                media = [{"type": "photo", "media": f"attach://f{i}", **({"caption": cap} if i == 0 else {})}
                         for i in range(len(files))]
                msgs = self.call("sendMediaGroup", {"chat_id": self.chat, "media": json.dumps(media)},
                                 {k: (f"{k}.jpg", h, "image/jpeg") for k, h in handles.items()})
            finally:
                for h in handles.values():
                    h.close()
            r = msgs[0]
        else:
            raise PublishError(f"telegram: unknown format {fmt}")
        return {"id": str(r["message_id"]), "url": self._url(r["message_id"])}
