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
    Answer(
        slug="follow-up-job-application",
        question="How many times should I follow up on a job application?",
        answer=(
            "Three touches to one inbox, then stop. Three captures roughly "
            "93% of the replies a sequence will ever earn, and a fourth to "
            "the same person is pestering. Day 4 a short nudge on the same "
            "thread with no attachment, day 9 one last nudge on that thread, "
            "and on day 16 only if you know a second real address at that "
            "company, a fresh approach to them with the CV. Any reply stops "
            "everything, including an automated one. Keep follow-ups on the "
            "original thread, because a new thread reads as a new stranger."),
        blurb="Three touches earn about 93% of the replies. The fourth is "
              "pestering.",
    ),
    Answer(
        slug="best-time-to-send",
        question="What is the best time to send a job application email?",
        answer=(
            "It matters far less than everyone says. Sending inside office "
            "hours returned a reply 19.1% of the time and outside them 17.9%, "
            "across 166 applications. A gap of one point on that sample is "
            "noise, not an effect. What does matter is the age of the advert: "
            "anything over a week old already has a shortlist. If you want a "
            "rule, Tuesday to Thursday morning is marginally the best guess, "
            "but choosing the right recipient is worth several times more "
            "than choosing the right hour."),
        blurb="19.1% in office hours against 17.9% outside. The timing advice "
              "everyone repeats is mostly noise.",
    ),
    Answer(
        slug="how-many-applications-a-day",
        question="How many job applications should I send a day?",
        answer=(
            "Around twenty, and one per employer ever. Volume is not the "
            "lever here: across 86 cold emails the gap between a good "
            "recipient and a bad one was about four times the reply rate, "
            "which no amount of extra sending closes. Recruitment agencies "
            "are the one exception, because they carry many vacancies at "
            "once, so up to four is reasonable if each one is a genuinely "
            "different job. Sending the same employer a second letter about "
            "a second role is the fastest way to be remembered badly."),
        blurb="Twenty a day, one per employer ever. Volume is not the lever.",
    ),
    Answer(
        slug="info-at-address",
        question="Should I email info@ about a job?",
        answer=(
            "Only if there is genuinely nothing else. A generic inbox such as "
            "info@ or enquiries@ replied 4 times out of 42, which is 10%, "
            "against 38% for a named person. It is the worst-performing "
            "address of any kind, because nobody in particular is "
            "responsible for reading it. Spend ten more minutes on the "
            "company's own contact, careers, about or team pages first. If "
            "info@ is all that exists, it is still better than the Apply "
            "button, which has nobody behind it at all."),
        blurb="4 replies out of 42. The worst address of any kind, and why.",
    ),
    Answer(
        slug="no-contact-details",
        question="What do I do if a job advert has no contact details?",
        answer=(
            "Look at the company's own website before you give up, and give "
            "up properly if it is not there. Contact, careers, about, team "
            "and jobs pages produced 77 real addresses that were not in any "
            "advert, replying at 21%. If none of those has one, apply "
            "through the portal and move on. Of the listings processed for "
            "the figures published here, 516 ended with no address found, "
            "and sending nothing was the right answer every time. A guessed "
            "address that bounces costs you the deliverability of your next "
            "email, including the one to an employer who wanted to talk."),
        blurb="Check the company site first: 77 addresses came from there. "
              "Then stop rather than guess.",
    ),
    Answer(
        slug="ai-screening",
        question="Do employers use AI to screen job applications?",
        answer=(
            "Many do, and the more useful point is that it barely matters "
            "why your application went unanswered. A portal application is "
            "filed rather than delivered: nobody is told it arrived and there "
            "is no thread for anyone to reply to, screening software or not. "
            "Emailing a real person at the same employer behaves completely "
            "differently, because a message in somebody's inbox has to be "
            "dealt with by a human being. Across 86 such emails, 22 came "
            "back, which is 26%, against the 1% to 5% cold email normally "
            "returns."),
        blurb="Whether a machine read it or nobody did, the fix is the same: "
              "reach a person.",
    ),
    Answer(
        slug="recruitment-agency",
        question="Should I email a recruitment agency directly?",
        answer=(
            "Yes, and they are the one target worth contacting more than "
            "once. An agency carries many vacancies at the same time, so up "
            "to four approaches is reasonable provided each is about a "
            "genuinely different role, where a direct employer gets one "
            "letter ever. Agency addresses are also unusually easy to find, "
            "because consultants publish their own. Across 86 cold emails a "
            "hiring inbox such as careers@ or recruitment@ replied 5 times "
            "out of 10, which is too small a sample to trust as 50% but "
            "clearly better than a generic inbox at 10%."),
        blurb="The one target worth writing to more than once, and the cap "
              "that keeps it welcome.",
    ),
    Answer(
        slug="attach-cv",
        question="Should I attach my CV to a cold email about a job?",
        answer=(
            "Yes, to the first email, and never to a follow-up. The opening "
            "letter is a job application and the CV is what it is for, so "
            "attach it and say so in the sign-off: name, phone number, CV "
            "attached, every time. A follow-up with the attachment again "
            "reads as a second application from somebody who forgot they "
            "sent the first. Keep the body itself between 60 and 90 words "
            "whether or not anything is attached, because the attachment is "
            "not what decides if the message gets read."),
        blurb="Attach it to the first email, never to the nudge, and keep the "
              "body to 60-90 words.",
    ),
    Answer(
        slug="ai-wrote-my-application",
        question="Is it obvious when AI has written a job application?",
        answer=(
            "Usually, and the tell is rarely the writing quality. 23 stock "
            "phrases and three formatting habits give it away, and the same "
            "list turns up again and again: I hope this email finds you "
            "well, passionate, leverage, delve, seamless, synergy, thrilled, "
            "perfect fit, proven track record, team player, and so on, plus "
            "exclamation marks, em dashes and markdown. The more serious "
            "risk is not style at all. Asked to sell you, a model reaches "
            "for credentials you never mentioned, so it writes that you hold "
            "a clearance or a ticket you do not. That gets found at vetting "
            "and it follows you."),
        blurb="The stock phrases are the small problem. The invented "
              "credential is the one that follows you.",
    ),
    Answer(
        slug="good-reply-rate",
        question="What is a good reply rate for cold emails to employers?",
        answer=(
            "Cold outreach is normally quoted at 1% to 5%. Across 86 cold "
            "emails to UK employers the rate was 26%, which is not a writing "
            "trick: the letters were near identical and the variable that "
            "moved was who received them, from 10% at a generic inbox to 38% "
            "at a named person. Two things to hold on to when comparing "
            "your own. A reply is not an interview, and plenty of those 22 "
            "were polite noes. And a rate quoted without the count under it "
            "is not worth reading, which is why every figure here carries "
            "the number it came from."),
        blurb="1-5% is the normal benchmark. 26% across 86, and why the count "
              "matters more than the rate.",
    ),
)


BY_SLUG = {a.slug: a for a in ANSWERS}


def get(slug: str) -> Answer | None:
    return BY_SLUG.get(slug)


def paths() -> list[str]:
    """Every answer URL, for the sitemap."""
    return [f"/answers/{a.slug}" for a in ANSWERS]
