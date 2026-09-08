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

# Read on every call, never captured at import. Two reasons, and the second
# is the one that bit:
#
#   - Import order stops mattering. A module-level read freezes whatever the
#     environment held the first time anything imported this file, so a test
#     that sets CREDENTIAL_KEY in setUp got the frozen empty string if some
#     earlier test module had already pulled the vault in. Fourteen tests
#     passed alone and failed in the suite for exactly that reason.
#   - It is honest about configuration. "The key is whatever is set now" is
#     a rule you can state; "the key is whatever was set at the moment some
#     unrelated module was first imported" is not.
def _key() -> str:
    return (os.environ.get("CREDENTIAL_KEY") or "").strip()


# Short enough to be a guessable passphrase rather than a random
# value. Refused outright: a weak key here is worse than no feature,
# because it looks like encryption and is not.
MIN_KEY_LENGTH = 24


class VaultError(RuntimeError):
    pass


def available() -> bool:
    """Whether credentials can be stored at all. The UI asks before offering
    automatic sending, so the answer is a missing feature rather than a
    server error."""
    return bool(_key()) and _fernet() is not None


def new_key() -> str:
    """A fresh key, for the setup instructions to print."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


# Building a Fernet is not free - the stretched path runs a KDF - so the
# result is still cached. It is cached AGAINST THE KEY IT WAS BUILT FROM
# rather than against "have we tried yet", so changing CREDENTIAL_KEY
# rebuilds instead of silently serving a cipher for the old key.
_cached = None
_cached_for = None


def _fernet():
    global _cached, _cached_for
    key = _key()
    if _cached_for == key:
        return _cached
    _cached_for = key
    _cached = None
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        try:
            # A real Fernet key, from Fernet.generate_key(). Used as-is.
            _cached = Fernet(key.encode())
        except Exception:
            # Anything else long enough gets stretched into one. This exists
            # so a host's own "generate a random value" button works: those
            # produce a long random string, not the 32 url-safe base64 bytes
            # Fernet demands, and telling somebody to run Python to make a key
            # is a step they may have no way to take.
            #
            # HKDF rather than a plain hash: it is built for turning one
            # secret into a key of an exact length, and the fixed salt is fine
            # because the input is expected to be random rather than a
            # remembered password.
            if len(key) < MIN_KEY_LENGTH:
                _cached = None
            else:
                _cached = Fernet(_derive(key))
    except Exception:
        # A malformed key is a configuration mistake, not a runtime one. Fail
        # closed: the feature is unavailable, the app still boots, and the
        # settings screen explains it.
        _cached = None
    return _cached


def _derive(secret: str) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    # DO NOT CHANGE THIS SALT, INCLUDING TO MATCH A RENAME.
    #
    # It is an input to the key derivation, not a label. A different salt
    # derives a different key from the same passphrase, so every credential
    # already stored through this path becomes undecryptable - and the
    # failure is quiet: users still look connected, and every send raises
    # "reconnect your mailbox" instead.
    #
    # It says recruited because that is what the product was called when
    # the first key was derived. That is the only thing it has to match.
    raw = HKDF(algorithm=hashes.SHA256(), length=32,
               salt=b"job-machine/credential-key",
               info=b"mail credentials v1").derive(secret.encode())
    return base64.urlsafe_b64encode(raw)


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
