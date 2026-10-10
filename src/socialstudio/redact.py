"""Redaction: every secret the vault hands out is registered here, and all output is masked.

Besides exact secrets, anything shaped like a credential is masked too (Meta, Google, GitHub,
Telegram bot and S3-signature tokens, Bearer/OAuth headers, key=value pairs with secret-ish names).
"""

from __future__ import annotations

import logging
import re

_KNOWN: set[str] = set()
MASK = "[REDACTED]"

PATTERNS = [
    re.compile(r"EAA[A-Za-z0-9]{20,}"),                         # Meta user/page tokens
    re.compile(r"TH[A-Za-z0-9]{40,}"),                          # Threads tokens
    re.compile(r"ya29\.[A-Za-z0-9_\-]{20,}"),                   # Google access tokens
    re.compile(r"1//[A-Za-z0-9_\-]{20,}"),                      # Google refresh tokens
    re.compile(r"GOCSPX-[A-Za-z0-9_\-]{10,}"),                  # Google client secrets
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),                  # GitHub tokens
    re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),                # GitHub fine-grained PATs
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_\-]{30,}\b"),            # Telegram bot tokens
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWTs (Bluesky)
    re.compile(r"(?i)(X-Amz-Signature=)[0-9a-f]{32,}"),         # presigned URL signatures
    re.compile(r"(?i)(authorization:\s*(?:bearer|oauth)\s+)\S+"),
    re.compile(r"(?i)\b(access_token|refresh_token|client_secret|appsecret_proof|password|"
               r"app_password|secret|token|fb_exchange_token|code)=([^&\s\"']+)"),
]


def register(secret: str | None):
    if secret and len(secret) >= 6:
        _KNOWN.add(secret)


def redact(text) -> str:
    s = str(text)
    for k in sorted(_KNOWN, key=len, reverse=True):
        if k in s:
            s = s.replace(k, MASK)
    for p in PATTERNS:
        if p.groups == 2:
            s = p.sub(lambda m: m.group(1) + MASK, s)
        elif p.groups == 1:
            s = p.sub(lambda m: m.group(1) + MASK, s)
        else:
            s = p.sub(MASK, s)
    return s


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def install_logging(level=logging.INFO):
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
