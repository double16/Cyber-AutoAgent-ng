"""Recognize protocol candidates without asserting authenticity or weakness."""

import base64
import binascii
import html
import json
import re
from urllib.parse import unquote

TOKEN = re.compile(r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{2,8192}(?:\.[A-Za-z0-9_-]{0,8192}){2,4})(?![A-Za-z0-9_.-])")

# These are syntax clues in captured text, not detectors of vulnerable behavior.
PROTOCOL_PATTERNS = {
    "http.oauth": re.compile(r"(?:[?&]|\b)(?:response_type|grant_type|code_challenge|code_verifier)=[^&\s]+"),
    "http.oidc": re.compile(r"(?:https?://[^\s\"<>]+/\.well-known/openid-configuration\b|\"id_token\"\s*:)"),
    "http.saml": re.compile(r"(?:[?&]|\b)SAML(?:Request|Response)=[^&\s]+|urn:oasis:names:tc:SAML:2\.0:"),
    "http.graphql": re.compile(r"\"query\"\s*:\s*\"\s*(?:(?:query|mutation|subscription)\b[^\"\n]*\{|\{)"),
    "http.websocket": re.compile(r"(?im)^(?:upgrade\s*:\s*websocket\s*$|sec-websocket-key\s*:\s*\S+)"),
    "http.xml": re.compile(r"(?im)^content-type\s*:\s*(?:application|text)/(?:[\w.-]+\+)?xml\b"),
    "http.multipart": re.compile(r"(?im)^content-type\s*:\s*multipart/form-data\s*;[^\r\n]*\bboundary="),
    "http.cors": re.compile(r"(?im)^access-control-allow-origin\s*:\s*\S+"),
}


def detect(text: str) -> list[str]:
    decoded = html.unescape(unquote(text))
    signals = []
    for match in TOKEN.finditer(decoded):
        parts = match[1].split(".")
        if len(parts) not in {3, 5}:
            continue
        try:
            header = json.loads(base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4)))
            if not isinstance(header, dict) or not isinstance(header.get("alg"), str):
                continue
            if len(parts) == 5:
                if isinstance(header.get("enc"), str):
                    signals.append("http.jwt")
                    break
            else:
                payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
                if isinstance(payload, dict):
                    signals.append("http.jwt")
                    break
        except (ValueError, UnicodeError, binascii.Error):
            continue
    signals.extend(name for name, pattern in PROTOCOL_PATTERNS.items() if pattern.search(decoded))
    return signals


def sanitize(text: str) -> str:
    return TOKEN.sub("<compact-token>", html.unescape(unquote(text)))
