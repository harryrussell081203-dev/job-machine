"""
Build cv/Harry_Russell_CV.pdf from cv/Harry_Russell_CV.docx.

The pipeline attaches a PDF, and the only CV in the repo is a .docx, so this
re-typesets it. Run it after editing the .docx:

    pip install reportlab
    python tools/build_cv_pdf.py

If you would rather use Word's own "Save as PDF" output, just drop that file in
cv/ instead - job_machine.py picks up whatever *.pdf it finds there.
"""
import os
import re
import zipfile
from xml.etree import ElementTree as ET

from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (ListFlowable, ListItem, Paragraph,
                                SimpleDocTemplate, Spacer)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCX = os.path.join(ROOT, "cv", "Harry_Russell_CV.docx")
PDF = os.path.join(ROOT, "cv", "Harry_Russell_CV.pdf")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
INK = "#111111"
RULE = "#888888"


def read_docx(path):
    """Yield (text, is_bullet) for every non-empty paragraph in the document."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    for p in root.iter(W + "p"):
        text = "".join(t.text or "" for t in p.iter(W + "t")).strip()
        if not text:
            continue
        bullet = p.find(f"{W}pPr/{W}numPr") is not None
        yield re.sub(r"\s+", " ", text), bullet


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main():
    styles = {
        "name": ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=19,
                               leading=22, textColor=INK, spaceAfter=2),
        "contact": ParagraphStyle("contact", fontName="Helvetica", fontSize=9,
                                  leading=12, textColor=INK, spaceAfter=10),
        "section": ParagraphStyle("section", fontName="Helvetica-Bold", fontSize=10,
                                  leading=13, textColor=INK, spaceBefore=10,
                                  spaceAfter=4, borderWidth=0),
        "role": ParagraphStyle("role", fontName="Helvetica-Bold", fontSize=9.5,
                               leading=12, textColor=INK, spaceBefore=6, spaceAfter=2),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9,
                               leading=12.5, textColor=INK, alignment=TA_JUSTIFY,
                               spaceAfter=3),
        "bullet": ParagraphStyle("bullet", fontName="Helvetica", fontSize=9,
                                 leading=12.5, textColor=INK, spaceAfter=1),
    }

    paragraphs = list(read_docx(DOCX))
    story = []
    pending_bullets = []

    def flush():
        if pending_bullets:
            story.append(ListFlowable(
                [ListItem(Paragraph(esc(b), styles["bullet"]), leftIndent=10)
                 for b in pending_bullets],
                bulletType="bullet", bulletFontSize=6, bulletOffsetY=1,
                leftIndent=10, spaceAfter=2,
            ))
            pending_bullets.clear()

    for i, (text, is_bullet) in enumerate(paragraphs):
        if is_bullet:
            pending_bullets.append(text)
            continue
        flush()
        if i == 0:
            story.append(Paragraph(esc(text), styles["name"]))
        elif i == 1:
            story.append(Paragraph(esc(text), styles["contact"]))
            story.append(Spacer(1, 1))
        elif text.isupper():
            story.append(Paragraph(
                f'<font color="{INK}">{esc(text)}</font>'
                f'<br/><font color="{RULE}" size="1"> </font>', styles["section"]))
        elif re.search(r"\b(19|20)\d{2}\b\s*$", text) or (
                " | " in text and re.search(r"\b(19|20)\d{2}\b", text)):
            story.append(Paragraph(esc(text), styles["role"]))
        else:
            story.append(Paragraph(esc(text), styles["body"]))
    flush()

    doc = SimpleDocTemplate(
        PDF, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title="Harry Russell CV", author="Harry Russell",
    )
    doc.build(story)
    print(f"wrote {PDF} ({os.path.getsize(PDF)} bytes, {doc.page} pages)")


if __name__ == "__main__":
    main()
