"""
Tests for the CV that gets attached to every application.

Offline. Reads the files in cv/ as they are.

The CV is the one artefact an employer definitely reads, and it is attached to
every single email this project sends. It claimed 'DV Cleared' in the contact
header, in the summary line, in a section heading and in a bullet of its own.
Harry's clearance lapsed after discharge, so all four were false - and no
amount of correcting the letters would have mattered while the attachment said
otherwise.
"""
import glob
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv_tailor  # noqa: E402

CLAIM = re.compile(r"\bDV\b|clear(?:ed|ance)|vetting", re.I)


def pdf_text(path):
    """The text an employer's PDF reader would show, out of reportlab's
    ASCII85-then-Flate streams."""
    import base64
    import zlib
    words = []
    raw = open(path, "rb").read()
    for block in re.findall(rb"stream\s*?\n(.*?)endstream", raw, re.S):
        data = block.strip()
        try:
            data = zlib.decompress(base64.a85decode(
                data, adobe=True, ignorechars=b" \t\r\n"))
        except Exception:
            try:
                data = zlib.decompress(data)
            except Exception:
                continue
        for m in re.finditer(rb"\(((?:[^()\\]|\\.)*)\)\s*Tj", data):
            words.append(m.group(1).decode("latin-1", "ignore"))
    return re.sub(r"\s+", " ", " ".join(words))


class TestTheMasterDocument(unittest.TestCase):
    def test_it_claims_no_security_clearance(self):
        for text, _bullet in cv_tailor.read_docx(cv_tailor.DOCX):
            with self.subTest(text=text[:60]):
                self.assertIsNone(CLAIM.search(text))

    def test_it_still_says_the_true_things(self):
        blob = " ".join(t for t, _ in cv_tailor.read_docx(cv_tailor.DOCX))
        for fact in ("Sonardyne", "Royal Navy", "IPC-A-610",
                     "HMS Westminster", "SCQF Level 7", "07398 530978"):
            with self.subTest(fact=fact):
                self.assertIn(fact, blob)


class TestEveryPdfThatCouldBeAttached(unittest.TestCase):
    """Checked on the rendered text rather than the source, because the source
    being right is not the same as the file an employer opens being right - the
    tailored PDFs are cached on disk and a stale one would still go out."""

    def pdfs(self):
        paths = ["cv/Harry_Russell_CV.pdf"] + sorted(glob.glob("cv/tailored/*.pdf"))
        return [p for p in paths if os.path.exists(p)]

    def test_there_is_something_to_check(self):
        self.assertTrue(self.pdfs(), "no CV PDFs found in cv/")

    def test_none_of_them_claims_a_clearance(self):
        for path in self.pdfs():
            with self.subTest(path=os.path.basename(path)):
                text = pdf_text(path)
                self.assertTrue(text, f"extracted no text from {path}")
                self.assertIsNone(CLAIM.search(text), CLAIM.search(text))

    def test_each_one_is_a_real_cv_and_not_an_empty_render(self):
        for path in self.pdfs():
            with self.subTest(path=os.path.basename(path)):
                text = pdf_text(path)
                self.assertIn("HARRY DEAN RUSSELL", text)
                self.assertIn("Sonardyne", text)

    def test_no_tailored_pdf_is_older_than_the_document_it_came_from(self):
        """A cached PDF built before the document was corrected would be
        attached to applications while every other check passed."""
        docx_at = os.path.getmtime(cv_tailor.DOCX)
        for path in self.pdfs():
            with self.subTest(path=os.path.basename(path)):
                self.assertGreaterEqual(os.path.getmtime(path), docx_at)


if __name__ == "__main__":
    unittest.main(verbosity=2)
