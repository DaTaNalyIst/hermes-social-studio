from .base import Platform, PublishError
from .bluesky import Bluesky
from .meta import Facebook, Instagram
from .telegram import Telegram
from .threads import Threads
from .youtube import YouTube

ADAPTERS = {"facebook": Facebook, "instagram": Instagram, "threads": Threads, "youtube": YouTube,
            "bluesky": Bluesky, "telegram": Telegram}


def adapter(name, cfg, vault, http, hosting=None, sleep=None):
    kw = {"sleep": sleep} if sleep else {}
    return ADAPTERS[name](cfg, vault, http, hosting, **kw)


__all__ = ["ADAPTERS", "adapter", "Platform", "PublishError"]
