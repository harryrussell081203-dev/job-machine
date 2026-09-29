"""Does the employer's domain accept email at all?

A letter to a domain with nowhere to deliver mail bounces, and bounces are
what a mail provider counts against the sender. The sender here is the
user's own Gmail, so every avoidable bounce is a small piece of their
reputation spent on nothing. One DNS lookup before the letter is written,
and again before it is sent, costs a fraction of a second.

The answer has three values on purpose:

  - True   the domain has an MX record, or (with none) an address record,
           which RFC 5321 says to deliver to directly
  - False  the domain does not exist, publishes a "null MX" (RFC 7505: this
           domain accepts no mail), or has neither MX nor address records
  - None   the lookup could not be done - a timeout, no DNS library, a
           resolver refusing. Treated as "go ahead". A slow DNS server is a
           fact about this runner, not about the employer, and must never
           stop a letter to a real address.

Switch: MX_CHECK (default on).
"""

from __future__ import annotations

from . import settings

_cache: dict[str, bool | None] = {}


def enabled() -> bool:
    return settings.flag("MX_CHECK")


def domain_of(address: str) -> str:
    return (address or "").rsplit("@", 1)[-1].strip().lower().rstrip(".")


def accepts_mail(domain: str, *, resolver=None) -> bool | None:
    domain = (domain or "").strip().lower().rstrip(".")
    if not domain or "." not in domain:
        return False
    if domain in _cache and resolver is None:
        return _cache[domain]
    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        return None
    resolve = resolver or _default_resolver(dns.resolver)
    answer = _ask(domain, resolve, dns)
    if resolver is None:
        _cache[domain] = answer
    return answer


def _default_resolver(module):
    r = module.Resolver()
    r.lifetime = 5.0
    return r.resolve


def _ask(domain, resolve, dns) -> bool | None:
    try:
        records = resolve(domain, "MX")
        hosts = [str(r.exchange).rstrip(".") for r in records]
        # A single MX of "." is the domain saying it takes no mail at all.
        if hosts and all(h in ("", ".") for h in hosts):
            return False
        return bool(hosts)
    except dns.resolver.NXDOMAIN:
        return False
    except dns.resolver.NoAnswer:
        pass                   # no MX: fall back to an address record
    except dns.exception.DNSException:
        return None
    for kind in ("A", "AAAA"):
        try:
            if resolve(domain, kind):
                return True
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            continue
        except dns.exception.DNSException:
            return None
    return False


def undeliverable(address: str, *, resolver=None) -> bool:
    """True only when the domain definitely takes no mail."""
    if not enabled():
        return False
    return accepts_mail(domain_of(address), resolver=resolver) is False
