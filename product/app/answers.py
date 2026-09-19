"""Pages that answer the question a jobseeker actually typed.

WHY THESE EXIST, AND WHY THEY ARE NOT MORE MARKETING PAGES.

An assistant that recommends something is not remembering it. ChatGPT, Perplexity
and Google's AI summaries run a search at the moment they answer and quote the
pages that come back, so the page that gets recommended is the page that answers
the literal question - not the page that describes a product.

Everything on these pages is already in PLAYBOOK.md. The playbook is 194 lines
rendered into a single <pre> block, which is a good document and an unquotable
one: no headings to anchor to, no separate address to cite, and one page cannot
rank for five different questions. So the findings are split out, one question
per page, each with its answer in the first paragraph where a machine reading
the page will find it.

The numbers are the point. Every commercial page competing for these searches
sells an email-finding API and tells the reader to guess an address pattern and
verify it. Nobody else has 86 cold emails to real UK employers broken down by
who received them, and first-party data with a source is what gets quoted.

Two rules for anything added here:

  - It must be true, checked, and caveated where the sample is thin. A page
    that gets cited is a page whose mistakes get repeated at scale.
  - `answer` is the paragraph that gets lifted and quoted somewhere this site
    cannot follow it. It has to stand on its own, with the figure in it, and
    still be honest with no page around it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Answer:
    slug: str
    # The heading, and the question in the structured data. Phrased the way
    # somebody types it, not the way a brochure would title it.
    question: str
    # The direct answer, first paragraph on the page and the one a machine
    # quotes. Self-contained: it makes sense with nothing around it.
    answer: str
    # One line on the index and in the meta description.
    blurb: str


ANSWERS: tuple[Answer, ...] = (
    Answer(
        slug="why-no-reply",
        question="Why do I never hear back from job applications?",
        answer=(
            "Because a portal application is filed, not delivered. Nobody at "
            "the company is told it arrived, and there is no thread for anyone "
            "to reply to. Emailing a real person at the same employer behaves "
            "completely differently: across 86 cold emails to UK employers, 22 "
            "came back, which is 26%. Cold email normally runs at 1% to 5%. "
            "The difference was almost entirely about who received it."),
        blurb="A portal application is filed, not delivered. What happens when "
              "you email a person instead.",
    ),
    Answer(
        slug="find-hiring-manager",
        question="How do I find the hiring manager's email address?",
        answer=(
            "Read the job advert to the very end first. When an employer "
            "prints an address in the advert, almost nobody uses it, because "
            "almost everybody clicks Apply. Of 9 emails sent to an address "
            "printed in the advert, 6 got a reply, which is 67% and the best "
            "of any source. Next is the company's own website: /contact, "
            "/careers, /about, /team. That found 77 addresses and replied at "
            "21%. Never guess a pattern like firstname.lastname@company.com. "
            "If no real address can be found, send nothing."),
        blurb="Read the advert to the end. 67% of the addresses printed there "
              "got a reply, and almost nobody uses them.",
    ),
    Answer(
        slug="email-directly",
        question="Is it worth emailing a company directly instead of applying?",
        answer=(
            "Yes, and who you reach decides most of it. Across 86 cold emails "
            "to UK employers, a named human replied 38% of the time and a "
            "hiring inbox such as careers@ or hr@ replied 50%, against 10% "
            "for a generic info@. Reaching a person or a hiring inbox rather "
            "than a generic one was worth roughly four times the reply rate. "
            "Applying through the portal as well costs nothing and is worth "
            "doing."),
        blurb="38% from a named human, 10% from info@. Who you reach is worth "
              "about four times what you write.",
    ),
    Answer(
        slug="cold-email-template",
        question="How do I write a cold email asking about a job?",
        answer=(
            "Keep the body between 60 and 90 words. Open by naming the exact "
            "role plus one concrete detail from that specific advert, which is "
            "what proves you read it. Then two or three numbered proof points "
            "that matter to this job, with figures. Then exactly one question, "
            "because two questions mean the reader has to compose a reply "
            "rather than answer one. Sign off the same way every time: name, "
            "phone number, CV attached."),
        blurb="60 to 90 words, one detail from the advert, one question. With "
              "a worked example.",
    ),
    Answer(
        slug="ai-sounding-words",
        question="What words make a job application look AI-written?",
        answer=(
            "23 phrases and three formatting habits give it away, and the list "
            "is stable: \"I hope this email finds you well\", "
            "passionate, leverage, delve, seamless, synergy, "
            "dynamic, thrilled, excited to apply, perfect fit, hit the ground "
            "running, fast-paced environment, proven track record, "
            "results-driven, detail-oriented, team player, I am writing to, "
            "utilize, spearheaded, esteemed, keen to, furthermore, moreover. "
            "Also exclamation marks, em dashes, and any markdown formatting. "
            "Asking a model to avoid them does not work, because it complies "
            "for two paragraphs and drifts back. Check the finished draft "
            "against the list and rewrite anything that hits."),
        blurb="The list, and why asking a model politely to avoid them does "
              "not work.",
    ),
)

BY_SLUG = {a.slug: a for a in ANSWERS}


def get(slug: str) -> Answer | None:
    return BY_SLUG.get(slug)


def paths() -> list[str]:
    """Every answer URL, for the sitemap."""
    return [f"/answers/{a.slug}" for a in ANSWERS]
