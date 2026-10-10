"""Caption rules per platform: length limits, hashtag limits, and composition from a base caption."""

from __future__ import annotations

import re
import unicodedata

HASHTAG = re.compile(r"(?<![\w#])#([^\s#.,!?;:()\[\]{}\"'“”]+)", re.UNICODE)

LIMITS = {
    # platform: (max length, unit, max hashtags or None, media caption limit or None)
    "facebook": (63206, "chars", None, None),
    "instagram": (2200, "chars", 5, None),
    "threads": (500, "chars", 1, None),
    "youtube": (5000, "bytes", 15, None),
    "bluesky": (300, "graphemes", None, None),
    "telegram": (4096, "chars", None, 1024),
}
YOUTUBE_TITLE_MAX = 100


def graphemes(text: str) -> int:
    """Approximate user-perceived characters (what Bluesky counts): combining marks, variation
    selectors and joiner-glued emoji do not add a character; a flag (two regional indicators) is one."""
    n, prev_ri, joined = 0, False, False
    for ch in text:
        cat = unicodedata.category(ch)
        cp = ord(ch)
        if cat in ("Mn", "Me") or 0xFE00 <= cp <= 0xFE0F or 0x1F3FB <= cp <= 0x1F3FF or 0xE0020 <= cp <= 0xE007F:
            continue
        if cp == 0x200D:
            joined = True
            continue
        if joined:
            joined = False
            continue
        if 0x1F1E6 <= cp <= 0x1F1FF:
            if prev_ri:
                prev_ri = False
                continue
            prev_ri = True
        else:
            prev_ri = False
        n += 1
    return n


def length(text: str, unit: str) -> int:
    if unit == "bytes":
        return len(text.encode("utf-8"))
    if unit == "graphemes":
        return graphemes(text)
    return len(text)


def hashtags_in(text: str) -> list[str]:
    return HASHTAG.findall(text or "")


def compose(base: str, hashtags: list[str], platform: str, max_tags: int | None = None) -> str:
    """Default caption when Hermes gave none for this platform: base text + allowed hashtags."""
    lim = LIMITS[platform]
    allowed = max_tags if max_tags is not None else lim[2]
    tags = [t.lstrip("#") for t in hashtags if t.strip("#")]
    if allowed is not None:
        tags = tags[: max(0, allowed - len(hashtags_in(base)))]
    text = base.strip()
    if tags:
        text = f"{text}\n\n" + " ".join("#" + t for t in tags)
    return text


def problems(platform: str, text: str, has_media: bool = True, max_tags: int | None = None,
             title: str | None = None) -> list[str]:
    out = []
    max_len, unit, tag_cap, media_cap = LIMITS[platform]
    if platform == "telegram" and has_media:
        max_len = media_cap
    if max_tags is not None:
        tag_cap = max_tags
    n = length(text or "", unit)
    if n > max_len:
        out.append(f"{platform} caption is {n} {unit}; the limit is {max_len}")
    tags = hashtags_in(text or "")
    if tag_cap is not None and len(tags) > tag_cap:
        out.append(f"{platform} allows at most {tag_cap} hashtag(s); found {len(tags)}")
    if platform == "youtube":
        if not title:
            out.append("youtube needs a title")
        elif len(title) > YOUTUBE_TITLE_MAX:
            out.append(f"youtube title is {len(title)} chars; the limit is {YOUTUBE_TITLE_MAX}")
        if title and ("<" in title or ">" in title):
            out.append("youtube titles cannot contain < or >")
        if text and ("<" in text or ">" in text):
            out.append("youtube descriptions cannot contain < or >")
    if not (text or "").strip() and platform in ("threads", "bluesky", "telegram", "facebook") and not has_media:
        out.append(f"{platform} text post is empty")
    return out
