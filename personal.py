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


# --------------------------------------------------------------------------
# The small hand-maintained files in data/, which are a different problem.
#
# state.json is machine output: nobody reads it, so sealing all of it costs
# nothing. These are the opposite - a dozen small files that Harry edits by
# hand, each opening with a _README block explaining what it is for. Sealing
# them wholesale would hide the documentation along with the data and leave
# him editing ciphertext, which is how a config file stops being maintained.
#
# So: named fields only, and _README is never one of them.
#
# WHAT IS ACTUALLY IN THEM, HAVING LOOKED.
#
# Only two of the fourteen carry anything worth hiding. Every email address
# and phone number in agencies.json, support_orgs.json, ask_around.json,
# veteran_employers.json, funding_opportunities.json and networking_targets
# .json is a switchboard or an info@ that the organisation publishes on its
# own contact page - and the file records which page, because that was the
# rule. Encrypting a number off Texo's website protects nobody and costs the
# ability to read the file. They are deliberately left alone.
#
# The exceptions:
#
#   answers.json  Harry's own street address, postcode, phone and the email
#                 address whose local part is his date of birth. This is the
#                 real exposure in a public repository and it is his, not a
#                 third party's - which makes it his call and he has made it.
#
#   goals.json    One 'source' note recording where a fact came from, and one
#                 of them names a consultant and prints their direct line,
#                 taken off a reply signature. Sealing the note keeps the
#                 provenance rule - every line says where it came from - and
#                 stops that one line being a published phone number.
#
# City, county and country stay in the clear on purpose. "Aberdeen" is in
# every letter the machine sends, on the face of the CV and in the search
# configuration; hiding it identifies nobody and makes the file unreadable.
FILES = {
    # "" means the file's top-level object is itself the record. Anything
    # else names a container that is walked one level, exactly as SEALED does.
    "answers.json": {"": ("email", "phone", "address_line_1", "postcode",
                          "full_name")},
    "goals.json": {"goals": ("source",)},
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


def _records(data, container: str) -> list:
    """The dicts a container holds, whether it is a list, a map or the file.

    The small files are hand-written and their shapes differ - answers.json is
    one flat object, goals.json holds a list under "goals", others hold a map.
    Rather than three walkers, one that accepts all three and ignores anything
    it does not recognise, so a file whose shape changes goes unsealed and
    visibly rather than half-sealed and quietly.
    """
    if not isinstance(data, dict):
        return []
    if container == "":
        return [data]
    held = data.get(container)
    if isinstance(held, dict):
        held = list(held.values())
    if not isinstance(held, list):
        return []
    return [r for r in held if isinstance(r, dict)]


def _walk_file(name: str, data: dict, cipher, fn) -> dict:
    for container, fields in FILES.get(os.path.basename(name), {}).items():
        for record in _records(data, container):
            for field in fields:
                if field in record:
                    record[field] = fn(cipher, record[field])
    return data


def has_sealed_file(name: str, data: dict) -> bool:
    """Whether this file has fields that need a key to read."""
    for container, fields in FILES.get(os.path.basename(name), {}).items():
        for record in _records(data, container):
            for field in fields:
                v = record.get(field)
                if isinstance(v, str) and v.startswith(MARKER):
                    return True
    return False


def seal_file(name: str, data: dict, key: str | None = None) -> dict:
    """Seal one of the small data files. No key means leave it alone.

    Same asymmetry as seal(): quiet when there is no key to write with, loud
    when there is sealed data and no key to read it.
    """
    cipher = _cipher(key)
    if cipher is None:
        return data
    return _walk_file(name, data, cipher, _seal_value)


def unseal_file(name: str, data: dict, key: str | None = None) -> dict:
    """Read one of the small data files. Raises rather than half-reading it.

    Less dangerous than unseal() - nothing in the machine loads these, so a
    silent miss would not write to anybody twice - but the reason to raise is
    the same. A caller that gets "enc.v1:AAAA..." back as Harry's postcode and
    carries on has not failed, which is worse than failing.
    """
    if not has_sealed_file(name, data):
        return data
    cipher = _cipher(key)
    if cipher is None:
        raise KeyMissing(
            f"data/{os.path.basename(name)} has sealed fields and {KEY_ENV} "
            f"is not set. Run `python3 personal.py show data/"
            f"{os.path.basename(name)}` with the key in the environment.")
    return _walk_file(name, data, cipher, _unseal_value)


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


def _dump(data) -> str:
    """The exact shape these files are already written in.

    Two spaces, keys in the order they were written, trailing newline. NOT
    the sorted dump state.json gets, and that matters: these files are read
    top to bottom by a person and answers.json opens with name, then contact,
    then address for a reason. Sorting them would turn a five-line seal into a
    fifty-line diff and put "years_experience" above "address_line_1".
    """
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _cli(argv=None) -> int:
    """Read, seal and unseal the small files by hand.

    Sealing a file a person maintains is only acceptable if that person can
    still open it. These three verbs are that promise:

        python3 personal.py show   data/answers.json   # read it, unsealed
        python3 personal.py unseal data/answers.json   # open it to edit
        python3 personal.py seal   data/answers.json   # close it again

    `seal` is what Actions runs after every pipeline, so an edit left open by
    mistake is closed on the next run rather than sitting in the clear.
    """
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("verb", choices=("key", "show", "seal", "unseal"))
    ap.add_argument("paths", nargs="*", help="files under data/")
    args = ap.parse_args(argv)

    if args.verb == "key":
        print(make_key())
        return 0
    if not args.paths:
        print(f"{args.verb}: name at least one file", flush=True)
        return 2

    failed = 0
    for path in args.paths:
        name = os.path.basename(path)
        if name not in FILES:
            # Not an error. Most of data/ has nothing in it worth sealing and
            # saying so is more useful than refusing.
            print(f"{path}: nothing sealed in this file")
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        try:
            if args.verb == "show":
                print(_dump(unseal_file(name, data)), end="")
                continue
            was = _dump(data)
            fn = seal_file if args.verb == "seal" else unseal_file
            data = fn(name, data)
        except KeyMissing as e:
            print(f"{path}: {e}", flush=True)
            failed = 1
            continue
        now = _dump(data)
        if now == was:
            print(f"{path}: already {args.verb}ed")
            continue
        with open(path, "w", encoding="utf-8") as f:
            f.write(now)
        print(f"{path}: {args.verb}ed")
    return failed


if __name__ == "__main__":
    raise SystemExit(_cli())
