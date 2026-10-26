"""Recognize compact JWT/JWE candidates without asserting authenticity or weakness."""

import base64
import binascii
import html
import json
import re
from urllib.parse import unquote

TOKEN = re.compile(r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{2,8192}(?:\.[A-Za-z0-9_-]{0,8192}){2,4})(?![A-Za-z0-9_.-])")


def detect(text: str) -> list[str]:
    for match in TOKEN.finditer(html.unescape(unquote(text))):
        parts = match[1].split(".")
        if len(parts) not in {3, 5}:
            continue
        try:
            header = json.loads(base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4)))
            if not isinstance(header, dict) or not isinstance(header.get("alg"), str):
                continue
            if len(parts) == 5:
                if isinstance(header.get("enc"), str):
                    return ["http.jwt"]
            else:
                payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
                if isinstance(payload, dict):
                    return ["http.jwt"]
        except (ValueError, UnicodeError, binascii.Error):
            continue
    return []


def sanitize(text: str) -> str:
    return TOKEN.sub("<compact-token>", html.unescape(unquote(text)))
