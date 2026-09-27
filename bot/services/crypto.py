from __future__ import annotations

import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken


class Cipher:
    def __init__(self, key: str):
        raw = key.encode() if isinstance(key, str) else key
        self.fernet = Fernet(raw)
        self._key = raw

    @property
    def secret_key(self) -> str:
        return self._key.decode() if isinstance(self._key, bytes) else str(self._key)

    def encrypt_sender(self, user_id: int) -> str:
        return self.fernet.encrypt(str(int(user_id)).encode()).decode()

    def decrypt_sender(self, token: str) -> int:
        try:
            return int(self.fernet.decrypt(token.encode()).decode())
        except (InvalidToken, ValueError) as exc:
            raise ValueError("cannot decrypt sender id") from exc

    def encrypt_secret(self, plain: str) -> str:
        return self.fernet.encrypt(plain.encode("utf-8")).decode()

    def decrypt_secret(self, token: str) -> str | None:
        try:
            return self.fernet.decrypt(token.encode()).decode()
        except (InvalidToken, ValueError):
            return None

    def hmac_sender(self, user_id: int) -> str:
        return hmac.new(self._key, str(int(user_id)).encode(), hashlib.sha256).hexdigest()

    def hmac_matches(self, user_id: int, digest: str | None) -> bool:
        if not digest:
            return False
        return hmac.compare_digest(self.hmac_sender(user_id), digest)


def resolve_sender_id(whisper, cipher: Cipher) -> int | None:
    if whisper.sender_id is not None:
        return int(whisper.sender_id)
    if whisper.sender_id_enc:
        try:
            return cipher.decrypt_sender(whisper.sender_id_enc)
        except ValueError:
            return None
    return None
