from __future__ import annotations

import base64
import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken

from bot.constants import SEAL_KEY_BYTES


def new_seal_key() -> str:
    return secrets.token_urlsafe(SEAL_KEY_BYTES)


def _derive_fernet(passphrase: str) -> Fernet:
    digest = hashlib.sha256(passphrase.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def seal_encrypt(text: str, passphrase: str) -> str:
    return _derive_fernet(passphrase).encrypt(text.encode("utf-8")).decode()


def seal_decrypt(ciphertext: str, passphrase: str) -> str | None:
    try:
        return _derive_fernet(passphrase).decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError):
        return None
