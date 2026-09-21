"""Keep the people in state.json out of a public repository.

WHY THIS EXISTS.

data/state.json holds 579 email addresses and 141 phone numbers belonging to
named people at UK employers, the 176 letters written to them, and the 33
replies they sent back. That is other people's personal data. The repository
is public, so it cannot sit there in the clear.

WHY THE OBVIOUS VERSION OF THIS DOES NOT WORK, AND THIS ONE DOES.

Encrypting the whole file was the first idea and it fails on arithmetic. The
file is 18MB and the machine commits it around ten times a weekday. Plaintext
JSON delta-compresses, which is how 123 versions of it fit in a 315MB .git;
an encrypted blob neither compresses nor deltas, so every commit would add a
fresh 18MB and the repository would pass GitHub's 5GB limit inside a
fortnight.

Encrypting FIELDS rather than the file fixes that, on one condition: the
encryption has to be deterministic. Fernet and anything else randomised
produces a different ciphertext for the same address every single save, so
every field in the file changes on every commit and the delta problem comes
straight back. AES-SIV is deterministic and authenticated - the same address
always encrypts to the same bytes - so an unchanged field is an unchanged
line, and a commit adds only the jobs that are actually new.

The cost of determinism, stated plainly: equal plaintexts are visible as
equal ciphertexts. Somebody reading the repository can tell that two jobs
went to the same address without learning what it is. For hiding contacts
from a public repository that is an acceptable trade; for anything where the
fact of a repeat is itself the secret, it would not be.

WHAT IS AND IS NOT COVERED.

Explicit paths rather than a set of field names matched anywhere. A global
rule on "name" would encrypt company names too, and the machine matches on
those - the failure would be silent and would look like a machine that had
forgotten every employer it had written to.

FAILING LOUDLY IS THE WHOLE SAFETY PROPERTY.

If the key is missing, this raises. It must never decide that an encrypted
field is simply absent, because the caller would then read a state with no
contacts in it, conclude nobody has been written to, and write to all 135
employers a second time. A run that stops is a bad afternoon; a run that
proceeds half-blind is an apology to 135 companies.
"""

from __future__ import annotations

import base64
import json
import os

MARKER = "enc.v1:"

KEY_ENV = "STATE_KEY"

# Where the people are. Each entry is (container, field names) and the
# container is walked one level: state["jobs"] is a dict of job records, so
# every record's contact_email is covered without naming the jobs.
SEALED = {
    "jobs": ("contact_email", "contact_phone", "contact_name", "sent_to",
             "sent_body", "followup_body", "message_id", "stakeholder_email",
             "other_contacts", "reply_excerpt", "sent_subject",
             "portal_filled", "captcha_answers",
             # The advert text, because a third of adverts print a contact
             # address in the small print - which is the whole finding this
             # project is built on, and it means the descriptions carry
             # addresses no named field does. Sealing costs nothing in
             # behaviour: the machine decrypts on load and sees exactly what
             # it saw before. Only the file on disk changes.
             "description"),
    "inbox": ("address", "who", "subject"),
    "contact_numbers": ("name", "numbers", "source"),
    "support_asked": ("address", "email", "contact"),
    "agency_meeting_asked": ("email", "emails", "name", "contact"),
    "agency_registered": ("email", "emails", "name", "contact"),
    "agency_tickets_asked": ("email", "emails", "name", "contact"),
    # The biggest single container after jobs, and the one that was missed
    # first time round: 277 addresses, one per employer ever written to.
    "companies_contacted": ("email", "emails", "name", "contact", "phone"),
}


class KeyMissing(RuntimeError):
    """No key, and there is sealed data that needs one."""


def make_key() -> str:
    """A new key, printable, for pasting into a repository secret."""
    from cryptography.hazmat.primitives.ciphers.aead import AESSIV
    return base64.urlsafe_b64encode(AESSIV.generate_key(256)).decode()


def _cipher(key: str | None):
    from cryptography.hazmat.primitives.ciphers.aead import AESSIV
    raw = key or os.environ.get(KEY_ENV, "")
    if not raw:
        return None
    return AESSIV(base64.urlsafe_b64decode(raw))


def _seal_value(cipher, value):
    """One value, encrypted, or returned untouched if already sealed.

    Whole JSON values rather than strings only, because contact_numbers holds
    a list of numbers and half-encrypting a record is worse than not.
    """
    if isinstance(value, str) and value.startswith(MARKER):
        return value
    if value is None or value == "" or value == []:
        return value
    blob = cipher.encrypt(json.dumps(value, sort_keys=True).encode("utf-8"), None)
    return MARKER + base64.urlsafe_b64encode(blob).decode()


def _unseal_value(cipher, value):
    if not (isinstance(value, str) and value.startswith(MARKER)):
        return value
    blob = base64.urlsafe_b64decode(value[len(MARKER):])
    return json.loads(cipher.decrypt(blob, None).decode("utf-8"))


def _walk(state: dict, cipher, fn) -> dict:
    for container, fields in SEALED.items():
        records = state.get(container)
        if not isinstance(records, dict):
            continue
        for record in records.values():
            if not isinstance(record, dict):
                continue
            for field in fields:
                if field in record:
                    record[field] = fn(cipher, record[field])
    return state


def has_sealed(state: dict) -> bool:
    """Whether anything in here needs a key to read."""
    for container, fields in SEALED.items():
        records = state.get(container)
        if not isinstance(records, dict):
            continue
        for record in records.values():
            if not isinstance(record, dict):
                continue
            for field in fields:
                v = record.get(field)
                if isinstance(v, str) and v.startswith(MARKER):
                    return True
    return False


def seal(state: dict, key: str | None = None) -> dict:
    """Encrypt in place, ready to be written to a public repository.

    No key configured means the state is written as it always was. That is
    deliberate for local work on a machine where the file never leaves - the
    loud failure belongs on the READ side, where getting it wrong means
    writing to somebody twice.
    """
    cipher = _cipher(key)
    if cipher is None:
        return state
    return _walk(state, cipher, _seal_value)


def unseal(state: dict, key: str | None = None) -> dict:
    """Decrypt in place. Raises rather than returning a half-read state."""
    if not has_sealed(state):
        return state
    cipher = _cipher(key)
    if cipher is None:
        raise KeyMissing(
            f"data/state.json is encrypted and {KEY_ENV} is not set. "
            "Refusing to continue: a state read without its contacts looks "
            "exactly like a state where nobody has been written to, and the "
            "next run would write to every employer a second time.")
    return _walk(state, cipher, _unseal_value)


if __name__ == "__main__":
    print(make_key())
