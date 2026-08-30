"""Encryption for the one secret this app has no choice but to hold.

Automatic sending needs the user's own mail credentials, because the whole
value of these letters is that they arrive from a real person's address rather
than from a service. There is no way to send as somebody without something
that authorises it.

So this file exists to make holding that as survivable as possible:

  - **Encrypted at rest**, with a key that lives in the environment and never
    in the database. Someone who walks off with a database dump - the most
    likely breach by a distance - gets ciphertext.
  - **No key, no feature.** If CREDENTIAL_KEY is unset, automatic sending is
    unavailable and says so. It does not quietly fall back to storing the
    password in plain text, which is the failure nobody notices.
  - **Nothing here logs.** Not the plaintext, not the ciphertext, not on
    error. The exception messages below deliberately contain no values.

What this does NOT protect against: an attacker who has both the database and
the running environment. Nothing symmetric can. The realistic threat here is a
leaked backup, and that is the one this stops.

Why app passwords rather than the account password: a Gmail app password is
revocable on its own, without changing the account password or touching any
other device. Be straight with users that it is still broad access to that
mailbox - see the setup screen, which says so.
"""

from __future__ import annotations

import base64
import os

_KEY = (os.environ.get("CREDENTIAL_KEY") or "").strip()


class VaultError(RuntimeError):
    pass


def available() -> bool:
    """Whether credentials can be stored at all. The UI asks before offering
    automatic sending, so the answer is a missing feature rather than a
    server error."""
    return bool(_KEY) and _fernet() is not None


def new_key() -> str:
    """A fresh key, for the setup instructions to print."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


_cached = None
_tried = False


def _fernet():
    global _cached, _tried
    if _tried:
        return _cached
    _tried = True
    if not _KEY:
        return None
    try:
        from cryptography.fernet import Fernet
        # Accept a raw urlsafe-base64 key, which is what Fernet.generate_key
        # produces and what the setup instructions tell people to paste.
        _cached = Fernet(_KEY.encode())
    except Exception:
        # A malformed key is a configuration mistake, not a runtime one. Fail
        # closed: the feature is unavailable, the app still boots, and the
        # settings screen explains it.
        _cached = None
    return _cached


def encrypt(plaintext: str) -> str:
    f = _fernet()
    if f is None:
        raise VaultError(
            "CREDENTIAL_KEY is not set or is not a valid key, so mail "
            "credentials cannot be stored. Generate one with:\n"
            "  python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"")
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    f = _fernet()
    if f is None:
        raise VaultError("CREDENTIAL_KEY is not set, so stored mail "
                         "credentials cannot be read")
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except Exception as exc:
        # Almost always a rotated key against old ciphertext. Say that,
        # because the useful action is "ask the user to reconnect", not
        # "look for a bug".
        raise VaultError(
            "stored mail credentials could not be decrypted - this usually "
            "means CREDENTIAL_KEY changed since they were saved, and the "
            "user needs to reconnect their mail account") from exc


def fingerprint(ciphertext: str) -> str:
    """A short, non-reversible tag for telling two stored secrets apart in a
    log line without printing either of them."""
    import hashlib
    digest = hashlib.sha256((ciphertext or "").encode()).digest()[:6]
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")
