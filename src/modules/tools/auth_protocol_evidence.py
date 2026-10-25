"""Structural authentication indicators, separate from validated authentication flows."""

import base64
import hashlib
import json
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree


class _PasswordFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_form = False
        self.password_form = False

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.in_form = True
        if tag == "input" and self.in_form and dict(attrs).get("type", "").lower() == "password":
            self.password_form = True

    def handle_endtag(self, tag):
        if tag == "form":
            self.in_form = False


def authentication_indicators(content: str, headers: dict, url: str, status_code: int = 200) -> list[dict]:
    """Observe protocol documents/messages and form/cookie indicators without validating login."""

    indicators = []

    def add(kind, indicator, fields, supports_mechanism=True):
        indicators.append({"type": kind, "indicator": indicator, "fields": fields,
                           "supports_mechanism": supports_mechanism})

    try:
        document = json.loads(content)
    except (ValueError, TypeError):
        document = None
    if isinstance(document, dict):
        oidc_fields = ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri")
        if all(isinstance(document.get(key), str) and urlparse(document[key]).scheme in {"http", "https"}
               and urlparse(document[key]).netloc for key in oidc_fields):
            add("OAuth", "oidc_discovery", list(oidc_fields))
        keys = document.get("keys")
        if isinstance(keys, list) and keys and all(
            isinstance(key, dict) and (
                (key.get("kty") == "RSA" and bool(key.get("n")) and bool(key.get("e")))
                or (key.get("kty") == "EC" and bool(key.get("crv")) and bool(key.get("x")) and bool(key.get("y")))
                or (key.get("kty") == "OKP" and bool(key.get("crv")) and bool(key.get("x")))
            ) for key in keys
        ):
            add("JWT", "jwks_document", ["keys", "kty", "public_key_material"])
    try:
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, ValueError):
        root = None
    if root is not None:
        namespace = "{urn:oasis:names:tc:SAML:2.0:metadata}"
        descriptors = [root] if root.tag == namespace + "EntityDescriptor" else list(
            root.iter(namespace + "EntityDescriptor")
        )
        if any(entity.get("entityID") and any(
            service.get("Binding") and service.get("Location")
            for idp in entity.findall(namespace + "IDPSSODescriptor")
            for service in idp.findall(namespace + "SingleSignOnService")
        ) for entity in descriptors):
            add("SAML", "saml_metadata", ["EntityDescriptor", "IDPSSODescriptor", "SingleSignOnService"])
        if (root.get("ID") and root.get("Version") == "2.0" and root.tag in {
            "{urn:oasis:names:tc:SAML:2.0:protocol}AuthnRequest", "{urn:oasis:names:tc:SAML:2.0:protocol}Response"}):
            add("SAML", "saml_message", [root.tag, "ID", "Version"])
    for token in re.findall(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", content):
        try:
            parts = token.split(".")
            decoded = [json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))) for part in parts[:2]]
            if isinstance(decoded[0], dict) and decoded[0].get("alg") and isinstance(decoded[1], dict) and decoded[1]:
                add("JWT", "jwt_structure", ["header.alg", "payload", "signature"])
                break
        except (ValueError, UnicodeError):
            continue
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    location = normalized_headers.get("location", "")
    query = parse_qs(urlparse(location).query)
    if 300 <= status_code < 400 and all(query.get(key) for key in ("client_id", "redirect_uri", "response_type")):
        add("OAuth", "authorization_redirect", ["client_id", "redirect_uri", "response_type"])
    parser = _PasswordFormParser()
    parser.feed(content)
    if parser.password_form:
        add("Session-based", "password_form", ["form", "input.type=password"], False)
    if normalized_headers.get("set-cookie"):
        add("Session-based", "cookie_set", ["Set-Cookie"], False)
    evidence = {"url": url, "body_sha256": hashlib.sha256(content.encode()).hexdigest()}
    return [{**item, "response_evidence": evidence} for item in indicators]
