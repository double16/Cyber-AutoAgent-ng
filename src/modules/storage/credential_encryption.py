"""Authenticated encryption for recoverable credential payloads in SQLite."""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KEY_ENVIRONMENT_VARIABLE = "CYBER_CREDENTIAL_STORE_KEY"
_ENVELOPE_PREFIX = "enc:v1:"
_NONCE_BYTES = 12
_KEY_BYTES = 32


class CredentialEncryptionError(ValueError):
    """Raised when a credential payload cannot be safely encrypted or decrypted."""


class CredentialPayloadCipher:
    """Encrypt credential payload JSON with a deployment-supplied AES-256-GCM key."""

    def __init__(self, key: bytes):
        if len(key) != _KEY_BYTES:
            raise CredentialEncryptionError(
                f"{_KEY_ENVIRONMENT_VARIABLE} must decode to exactly {_KEY_BYTES} bytes"
            )
        self._cipher = AESGCM(key)

    @classmethod
    def from_environment(cls) -> CredentialPayloadCipher | None:
        """Build a cipher when the optional credential-store key is configured."""

        encoded_key = os.environ.get(_KEY_ENVIRONMENT_VARIABLE, "").strip()
        if not encoded_key:
            return None
        try:
            padded_key = encoded_key + "=" * (-len(encoded_key) % 4)
            key = base64.b64decode(padded_key.encode("ascii"), altchars=b"-_", validate=True)
        except (UnicodeEncodeError, binascii.Error) as error:
            raise CredentialEncryptionError(
                f"{_KEY_ENVIRONMENT_VARIABLE} must be URL-safe base64-encoded"
            ) from error
        return cls(key)

    @staticmethod
    def is_encrypted(value: str) -> bool:
        """Return whether a database payload uses this encryption envelope."""

        return value.startswith(_ENVELOPE_PREFIX)

    @staticmethod
    def _associated_data(logical_target: str, credential_id: str) -> bytes:
        return f"{logical_target}\x00{credential_id}".encode()

    def encrypt(self, payload: dict[str, Any], *, logical_target: str, credential_id: str) -> str:
        """Serialize and encrypt one credential payload with target- and record-bound AAD."""

        plaintext = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        nonce = secrets.token_bytes(_NONCE_BYTES)
        ciphertext = self._cipher.encrypt(nonce, plaintext, self._associated_data(logical_target, credential_id))
        return _ENVELOPE_PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, value: str, *, logical_target: str, credential_id: str) -> dict[str, Any]:
        """Decrypt and validate one credential payload, failing closed on tampering or the wrong key."""

        if not self.is_encrypted(value):
            raise CredentialEncryptionError("credential payload is not encrypted")
        try:
            encoded = value.removeprefix(_ENVELOPE_PREFIX)
            padded = encoded + "=" * (-len(encoded) % 4)
            encrypted = base64.urlsafe_b64decode(padded.encode("ascii"))
            nonce, ciphertext = encrypted[:_NONCE_BYTES], encrypted[_NONCE_BYTES:]
            if len(nonce) != _NONCE_BYTES or not ciphertext:
                raise ValueError("missing nonce or ciphertext")
            plaintext = self._cipher.decrypt(nonce, ciphertext, self._associated_data(logical_target, credential_id))
            payload = json.loads(plaintext)
        except (ValueError, UnicodeDecodeError, binascii.Error, InvalidTag, json.JSONDecodeError) as error:
            raise CredentialEncryptionError(
                "credential payload cannot be decrypted with CYBER_CREDENTIAL_STORE_KEY"
            ) from error
        if not isinstance(payload, dict):
            raise CredentialEncryptionError("credential payload must decode to an object")
        return payload
