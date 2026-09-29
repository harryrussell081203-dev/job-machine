"""Error reports to Sentry, once there is somewhere to send them.

The production "permission denied for table contacted_mail" error on 28
September was found by reading logs after the fact. An error report arriving
the moment it first happened would have found it before anybody was
affected.

DORMANT until SENTRY_DSN is set: with no DSN nothing is imported, nothing
is sent, and nothing changes.

What it is allowed to send is kept deliberately small, because this app
holds people's CVs, letters and mail passwords:

  - no request bodies, ever (a CV upload, a letter, a pasted advert)
  - no cookies, headers or query strings
  - no user email or IP address (send_default_pii is off)
  - no performance tracing, which would spend the free plan's quota on
    nothing an error report does not already say
"""

from __future__ import annotations

from jobseeker import settings

_started = False


def _scrub(event, hint):
    request = event.get("request") or {}
    for key in ("data", "cookies", "headers", "query_string", "env"):
        request.pop(key, None)
    event.pop("user", None)
    return event


def start(where: str) -> bool:
    """Switch error reporting on for this process if SENTRY_DSN is set.
    `where` is "web" or "sweep", so the two can be told apart."""
    global _started
    dsn = settings.text("SENTRY_DSN")
    if _started or not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        print("[errors] SENTRY_DSN is set but sentry-sdk is not installed")
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=settings.text("SENTRY_ENVIRONMENT", "production"),
        send_default_pii=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        before_send=_scrub,
    )
    sentry_sdk.set_tag("process", where)
    _started = True
    return True
