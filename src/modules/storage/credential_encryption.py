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
_PREVIOUS_KEYS_ENVIRONMENT_VARIABLE = "CYBER_CREDENTIAL_STORE_PREVIOUS_KEYS"
_ENVELOPE_PREFIX = "enc:v1:"
_NONCE_BYTES = 12
_KEY_BYTES = 32


class CredentialEncryptionError(ValueError):
    """Raised when a credential payload cannot be safely encrypted or decrypted."""


class CredentialPayloadCipher:
    """Encrypt credential payload JSON with a deployment-supplied AES-256-GCM key."""

    def __init__(self, key: bytes, previous_keys: tuple[bytes, ...] = ()):
        if len(key) != _KEY_BYTES:
            raise CredentialEncryptionError(
                f"{_KEY_ENVIRONMENT_VARIABLE} must decode to exactly {_KEY_BYTES} bytes"
            )
        self._cipher = AESGCM(key)
        if any(len(previous_key) != _KEY_BYTES for previous_key in previous_keys):
            raise CredentialEncryptionError(
                f"{_PREVIOUS_KEYS_ENVIRONMENT_VARIABLE} entries must decode to exactly {_KEY_BYTES} bytes"
            )
        self._ciphers = (self._cipher, *(AESGCM(previous_key) for previous_key in previous_keys))

    @classmethod
    def from_environment(cls) -> CredentialPayloadCipher | None:
        """Build a cipher when the optional credential-store key is configured."""

        encoded_primary_key = os.environ.get(_KEY_ENVIRONMENT_VARIABLE, "").strip()
        if not encoded_primary_key:
            return None
        primary_key = cls._decode_key(encoded_primary_key, _KEY_ENVIRONMENT_VARIABLE)
        raw_previous_keys = os.environ.get(_PREVIOUS_KEYS_ENVIRONMENT_VARIABLE, "").strip()
        if not raw_previous_keys:
            return cls(primary_key)
        encoded_previous_keys = tuple(item.strip() for item in raw_previous_keys.split(","))
        if any(not item for item in encoded_previous_keys):
            raise CredentialEncryptionError(
                f"{_PREVIOUS_KEYS_ENVIRONMENT_VARIABLE} must be a comma-separated list of base64 keys"
            )
        return cls(
            primary_key,
            tuple(cls._decode_key(item, _PREVIOUS_KEYS_ENVIRONMENT_VARIABLE) for item in encoded_previous_keys),
        )

    @staticmethod
    def _decode_key(encoded_key: str, environment_variable: str) -> bytes:
        """Decode one environment-supplied AES-256 key without echoing its value."""

        try:
            padded_key = encoded_key + "=" * (-len(encoded_key) % 4)
            key = base64.b64decode(padded_key.encode("ascii"), altchars=b"-_", validate=True)
        except (UnicodeEncodeError, binascii.Error) as error:
            raise CredentialEncryptionError(
                f"{environment_variable} must be URL-safe base64-encoded"
            ) from error
        if len(key) != _KEY_BYTES:
            raise CredentialEncryptionError(
                f"{environment_variable} must decode to exactly {_KEY_BYTES} bytes"
            )
        return key

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

        return self.decrypt_with_key_index(
            value,
            logical_target=logical_target,
            credential_id=credential_id,
        )[0]

    def decrypt_with_key_index(
        self,
        value: str,
        *,
        logical_target: str,
        credential_id: str,
    ) -> tuple[dict[str, Any], int]:
        """Decrypt one payload and identify whether the primary or a rotation key was used."""

        if not self.is_encrypted(value):
            raise CredentialEncryptionError("credential payload is not encrypted")
        try:
            encoded = value.removeprefix(_ENVELOPE_PREFIX)
            padded = encoded + "=" * (-len(encoded) % 4)
            encrypted = base64.urlsafe_b64decode(padded.encode("ascii"))
            nonce, ciphertext = encrypted[:_NONCE_BYTES], encrypted[_NONCE_BYTES:]
            if len(nonce) != _NONCE_BYTES or not ciphertext:
                raise ValueError("missing nonce or ciphertext")
            associated_data = self._associated_data(logical_target, credential_id)
            for key_index, cipher in enumerate(self._ciphers):
                try:
                    plaintext = cipher.decrypt(nonce, ciphertext, associated_data)
                except InvalidTag:
                    continue
                payload = json.loads(plaintext)
                if not isinstance(payload, dict):
                    raise CredentialEncryptionError("credential payload must decode to an object")
                return payload, key_index
        except CredentialEncryptionError:
            raise
        except (ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError) as error:
            raise CredentialEncryptionError(
                "credential payload cannot be decrypted with CYBER_CREDENTIAL_STORE_KEY"
            ) from error
        raise CredentialEncryptionError("credential payload cannot be decrypted with CYBER_CREDENTIAL_STORE_KEY")
