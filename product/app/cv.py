"""The user's CV: accepting it, reading it, and attaching it.

Two jobs, and the second is the one that makes onboarding bearable.

**Accepting it.** A CV arrives as an upload from a stranger, which is the most
hostile input this app takes. So: a size ceiling checked against the bytes
actually read rather than a header anyone can lie about, an extension
allow-list, and a content sniff that rejects a .pdf which is not a PDF.

**Reading it.** The profile form asks about twenty questions, and a person who
has just paid should not have to type what is already written in the document
they just handed over. The text goes to the model, which fills in what it can,
and the user corrects it. That is a much shorter job than starting from blank.

Nothing extracted here is ever trusted as final. Every field lands in the
form as a suggestion the user must look at, because a model reading a CV will
occasionally invent a qualification, and this product's one unforgivable
failure is claiming something the user does not hold.
"""

from __future__ import annotations

import json
import re

MAX_BYTES = 5 * 1024 * 1024          # 5MB - a CV that big is a portfolio
ALLOWED = {".pdf", ".docx", ".doc", ".txt", ".md", ".rtf"}

# What each format actually starts with, so a renamed executable is refused.
MAGIC = {
    b"%PDF-": "pdf",
    b"PK\x03\x04": "zip",            # .docx is a zip
    b"\xd0\xcf\x11\xe0": "ole",      # legacy .doc
    b"{\\rtf": "rtf",
}


class CVError(ValueError):
    pass


def extension(filename: str) -> str:
    name = (filename or "").lower().strip()
    dot = name.rfind(".")
    return name[dot:] if dot > 0 else ""


def check(filename: str, blob: bytes) -> None:
    """Refuse anything that should not be stored. Raises CVError."""
    if not blob:
        raise CVError("that file was empty")
    if len(blob) > MAX_BYTES:
        raise CVError(
            f"that file is {len(blob) / 1024 / 1024:.1f}MB and the limit is "
            f"{MAX_BYTES // 1024 // 1024}MB. A CV should be one or two pages.")
    ext = extension(filename)
    if ext not in ALLOWED:
        raise CVError(
            f"'{ext or 'that file'}' is not a CV format. Use PDF, Word or "
            "plain text - PDF is safest, because it looks the same on the "
            "employer's screen as it does on yours.")

    # Plain text has no signature, so there is nothing to check.
    if ext in (".txt", ".md"):
        return
    kind = next((k for sig, k in MAGIC.items() if blob.startswith(sig)), None)
    expected = {".pdf": "pdf", ".docx": "zip", ".doc": "ole", ".rtf": "rtf"}[ext]
    if kind != expected:
        raise CVError(
            f"that file is named {ext} but its contents are not {ext}. "
            "Re-save it from whatever wrote it and try again.")


def extract_text(filename: str, blob: bytes) -> str:
    """Whatever text can be read. Never raises - a CV that cannot be parsed is
    still a perfectly good attachment, and the user can answer the questions
    by hand."""
    ext = extension(filename)
    try:
        if ext in (".txt", ".md"):
            return blob.decode("utf-8", errors="replace")
        if ext == ".pdf":
            return _pdf_text(blob)
        if ext == ".docx":
            return _docx_text(blob)
    except Exception:
        return ""
    return ""


def _pdf_text(blob: bytes) -> str:
    import io
    import logging
    from pypdf import PdfReader
    # A malformed PDF is a user handing over a bad file, not a fault worth a
    # stack of warnings in the server log. extract_text() already treats a
    # failure as "no text", which is the right outcome either way.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(io.BytesIO(blob))
    parts = []
    for page in reader.pages[:10]:          # a CV is not 200 pages
        parts.append(page.extract_text() or "")
    return _tidy("\n".join(parts))


def _docx_text(blob: bytes) -> str:
    """Read a .docx without a Word library.

    A .docx is a zip with the text in word/document.xml. Pulling the runs out
    with a regex is crude, but it avoids a dependency for one screen and it
    cannot execute anything embedded in the file.
    """
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    import html
    return _tidy(html.unescape(text))


def _tidy(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ----------------------------------------------------------------------
# turning a CV into answers
# ----------------------------------------------------------------------
PROMPT = """Read this CV and fill in what it actually says. British English.

Return ONLY a JSON object with these keys:

  name              full name, or ""
  location          the town or city they live in, or ""
  phone             or ""
  email             or ""
  target_roles      list of job titles this person could apply for now, most
                    likely first, at most 6. Use the words a job advert would
                    use, not the words the CV uses.
  qualifications    list of qualifications, tickets and certificates the CV
                    states they HOLD. At most 12.
  history           list of at most 5 objects, most recent first, each with
                    "title", "org" and "detail". detail is one short factual
                    sentence about what they did there, with a number in it
                    if the CV gives one.

Rules that matter more than completeness:

  - If the CV does not say something, use "" or an empty list. Never guess.
  - qualifications is only things stated as held. A course in progress, a
    lapsed ticket or an expired clearance does NOT go in.
  - Do not infer a qualification from a job title. Someone who worked on a
    rig is not offshore-certified unless the CV says the certificate.

CV:
---
%s
---

JSON only. No explanation, no markdown fence."""


def suggest_profile(text: str, ai) -> dict:
    """Best-effort answers from a CV. Returns {} rather than raising - this is
    a convenience, and a failure here must not block somebody who has paid."""
    if not (text or "").strip():
        return {}
    from .ai import AIError
    try:
        raw = ai(PROMPT % text[:12000])
    except AIError:
        # The model was rate limited or unreachable. That says nothing about
        # the CV, and telling somebody their CV is unreadable sends them off
        # to retype a profile by hand over a quota that resets on its own.
        raise
    except Exception:
        return {}
    return parse_suggestion(raw)


def parse_suggestion(raw: str) -> dict:
    """Pull the JSON object out of a model reply and keep only what is safe.

    Whitelisted keys only. A model that returns min_salary_annual, or
    never_claim, is not allowed to set them: pay floors and the never-claim
    list are the two things a user must state deliberately, and a plausible
    guess at either is worse than a blank.
    """
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}

    out = {}
    for key in ("name", "location", "phone", "email"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()[:120]
    for key, cap in (("target_roles", 6), ("qualifications", 12)):
        value = data.get(key)
        if isinstance(value, list):
            items = [str(v).strip()[:120] for v in value if str(v).strip()]
            if items:
                out[key] = items[:cap]
    history = data.get("history")
    if isinstance(history, list):
        rows = []
        for item in history[:5]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()[:120]
            org = str(item.get("org") or "").strip()[:120]
            if title and org:
                rows.append({"title": title, "org": org,
                             "detail": str(item.get("detail") or "").strip()[:300]})
        if rows:
            out["history"] = rows
    return out
