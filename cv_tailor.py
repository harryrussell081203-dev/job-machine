"""
Tailored CV builder - one PDF per role family, not one PDF for everything.

The summary paragraph is swapped for a variant that leads with what THIS kind
of job cares about, and the key-skills bullets are reordered the same way.
Every variant is built only from sentences that are true of the master CV in
cv/Harry_Russell_CV.docx - nothing is invented, nothing is added.

Falls back silently: if reportlab or the .docx is missing, callers get None and
attach the generic PDF instead.
"""
import os
import re
import zipfile
from xml.etree import ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCX = os.path.join(ROOT, "cv", "Harry_Russell_CV.docx")
OUT_DIR = os.path.join(ROOT, "cv", "tailored")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Summary per family. Same facts as the master CV, led differently.
#
# Each one opens on the fact that he is in work. He started as a technician in
# Aberdeen on 24 August 2026, and a CV that leads on a job he left reads like
# somebody who has been out of work since - the opposite of true, and the
# opposite of the position he is negotiating from.
SUMMARIES = {
    "communications": (
        "Communications and electronics technician, ex-Royal Navy, currently working "
        "as a technician in Aberdeen subsea engineering. Two years running "
        "secure and non-secure communications, networks and cryptographic material "
        "in the Royal Navy aboard a frontline Type 23 frigate, then three years "
        "building, testing and fault-finding precision subsea electronics at "
        "Sonardyne to IPC-A-610 Class 3. Time-served via a completed Engineering "
        "Modern Apprenticeship (SCQF Level 7). Used to safety-critical kit and "
        "environments where a fault has to be found and fixed with nobody else to "
        "call."),
    "electronics_technician": (
        "Engineering technician, currently in a technician role in Aberdeen subsea "
        "engineering, with three years before that building, testing and "
        "fault-finding precision subsea electronics at Sonardyne to IPC-A-610 "
        "Class 3, including component-level diagnosis, repair and the test records "
        "to match. Before that, two years maintaining safety-critical "
        "communications equipment in the Royal Navy aboard a frontline Type 23 "
        "frigate. Time-served via a completed Engineering Modern Apprenticeship "
        "(SCQF Level 7), with first-year BEng (Hons) study in Instrumentation, "
        "Measurement & Control."),
    "instrumentation_maintenance": (
        "Time-served engineering technician, currently working as a technician in "
        "Aberdeen subsea engineering: completed Engineering Modern "
        "Apprenticeship (SCQF Level 7, asset lifecycle and maintenance) and "
        "first-year BEng (Hons) in Instrumentation, Measurement & Control, with "
        "three years of hands-on testing, fault-finding and maintenance of subsea "
        "acoustic positioning systems at Sonardyne to IPC-A-610 Class 3. "
        "Ex-Royal Navy, and used to shift work, hard deadlines and "
        "safety-critical equipment."),
    "events_production": (
        "Working technician with a rare mix for the events industry: military-grade "
        "communications and electronics experience (two years Royal Navy, three "
        "years testing and fault-finding precision electronics at Sonardyne), and "
        "founder of Leads2Profit, a marketing systems business built exclusively "
        "for nightlife and events venues. Comfortable with AV, comms, cabling and "
        "fault-finding under time pressure, and used to working nights and "
        "unsociable hours."),
}

# Which key-skills bullets should lead, per family (matched by substring).
SKILL_PRIORITY = {
    "communications": ["secure radio", "fault diagnosis", "electronic assembly",
                       "operational discipline", "systems thinking", "autocad"],
    "electronics_technician": ["electronic assembly", "fault diagnosis",
                               "secure radio", "operational discipline",
                               "autocad", "systems thinking"],
    "instrumentation_maintenance": ["autocad", "electronic assembly",
                                    "fault diagnosis", "operational discipline",
                                    "secure radio", "systems thinking"],
    "events_production": ["systems thinking", "secure radio",
                          "electronic assembly", "fault diagnosis",
                          "operational discipline", "autocad"],
}


def read_docx(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    for p in root.iter(W + "p"):
        text = "".join(t.text or "" for t in p.iter(W + "t")).strip()
        if text:
            yield re.sub(r"\s+", " ", text), p.find(f"{W}pPr/{W}numPr") is not None


def tailor_paragraphs(paragraphs, family):
    """Swap the summary, reorder the key skills. Everything else untouched."""
    out, section = [], None
    skills, skills_at = [], None
    for text, bullet in paragraphs:
        if text.isupper():
            section = text
        if section == "PROFESSIONAL SUMMARY" and not bullet and not text.isupper() \
                and family in SUMMARIES:
            out.append((SUMMARIES[family], False))
            continue
        if section == "KEY SKILLS" and bullet:
            if skills_at is None:
                skills_at = len(out)
                out.append(None)  # placeholder
            skills.append(text)
            continue
        out.append((text, bullet))
    if skills_at is not None:
        order = SKILL_PRIORITY.get(family)
        if order:
            def rank(skill):
                low = skill.lower()
                for i, key in enumerate(order):
                    if key in low:
                        return i
                return len(order)
            skills = sorted(skills, key=rank)
        out[skills_at:skills_at + 1] = [(s, True) for s in skills]
    return out


def render(paragraphs, out_path):
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (ListFlowable, ListItem, Paragraph,
                                    SimpleDocTemplate, Spacer)

    styles = {
        "name": ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=19,
                               leading=22, spaceAfter=2),
        "contact": ParagraphStyle("contact", fontName="Helvetica", fontSize=9,
                                  leading=12, spaceAfter=10),
        "section": ParagraphStyle("section", fontName="Helvetica-Bold",
                                  fontSize=10, leading=13, spaceBefore=10,
                                  spaceAfter=4),
        "role": ParagraphStyle("role", fontName="Helvetica-Bold", fontSize=9.5,
                               leading=12, spaceBefore=6, spaceAfter=2),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9,
                               leading=12.5, alignment=TA_JUSTIFY, spaceAfter=3),
        "bullet": ParagraphStyle("bullet", fontName="Helvetica", fontSize=9,
                                 leading=12.5, spaceAfter=1),
    }

    def esc(t):
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    story, bullets = [], []

    def flush():
        if bullets:
            story.append(ListFlowable(
                [ListItem(Paragraph(esc(b), styles["bullet"]), leftIndent=10)
                 for b in bullets],
                bulletType="bullet", bulletFontSize=6, bulletOffsetY=1,
                leftIndent=10, spaceAfter=2))
            bullets.clear()

    for i, (text, is_bullet) in enumerate(paragraphs):
        if is_bullet:
            bullets.append(text)
            continue
        flush()
        if i == 0:
            story.append(Paragraph(esc(text), styles["name"]))
        elif i == 1:
            story.append(Paragraph(esc(text), styles["contact"]))
            story.append(Spacer(1, 1))
        elif text.isupper():
            story.append(Paragraph(esc(text), styles["section"]))
        elif re.search(r"\b(19|20)\d{2}\b\s*$", text) or (
                " | " in text and re.search(r"\b(19|20)\d{2}\b", text)):
            story.append(Paragraph(esc(text), styles["role"]))
        else:
            story.append(Paragraph(esc(text), styles["body"]))
    flush()

    SimpleDocTemplate(out_path, pagesize=A4, leftMargin=18 * mm,
                      rightMargin=18 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
                      title="Harry Russell CV", author="Harry Russell").build(story)


def build(family):
    """Path to the tailored PDF for a family, or None if it cannot be built.
    Cached per family - the content only depends on the family."""
    if family not in SUMMARIES:
        return None  # 'general' and unknowns use the master PDF
    out_path = os.path.join(OUT_DIR, f"Harry_Russell_CV_{family}.pdf")
    if os.path.exists(out_path) and os.path.getmtime(out_path) >= os.path.getmtime(DOCX):
        return out_path
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        paragraphs = tailor_paragraphs(list(read_docx(DOCX)), family)
        render(paragraphs, out_path)
        return out_path
    except Exception as e:  # reportlab missing, docx unreadable, disk trouble
        print(f"[cv] could not tailor for {family}: {e}")
        return None
