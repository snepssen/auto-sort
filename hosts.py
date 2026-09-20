"""Turning a download URL's hostname into a name worth making a folder out of.

`d.furaffinity.net`, `static1.e621.net`, `pbs.twimg.com`,
`scontent-man2-1.xx.fbcdn.net` and `uk.pinterest.com` are five services, and
what makes them five rather than one is not something that needs a table of
sites in it. Strip the delivery-network decoration off the front, take the
registrable domain, and keep its first label: furaffinity, e621, twimg,
fbcdn, pinterest.

This matters more than it looks. The operating system records the URL a file
was downloaded from -- every mainstream browser writes it -- so the source of
anything that arrived by download is already known, exactly, without guessing
at the filename and without anybody having collected samples from that site
first. A site nobody has ever heard of groups correctly on the first file.

The only table here is of multi-part public suffixes, and it is about the
shape of the domain name system rather than about any particular website.
"""

from __future__ import annotations

import re

# Suffixes where the registrable domain is three labels rather than two.
# Short on purpose: the full public suffix list is a downloadable file that
# goes stale, and the cost of missing one is a folder named `co` instead of
# `example`, which is untidy rather than wrong.
MULTIPART_SUFFIXES = frozenset("""
co.uk org.uk me.uk ac.uk gov.uk net.uk sch.uk ltd.uk plc.uk
com.au net.au org.au edu.au gov.au id.au
co.nz net.nz org.nz govt.nz ac.nz
co.jp ne.jp or.jp ac.jp go.jp
co.kr or.kr ne.kr
com.br net.br org.br gov.br
com.cn net.cn org.cn gov.cn edu.cn
co.za org.za net.za
com.mx com.ar com.tr com.sg com.hk com.tw com.my com.ph com.vn
co.in net.in org.in gov.in
com.pl net.pl org.pl
co.il org.il net.il
github.io gitlab.io pages.dev workers.dev netlify.app vercel.app
s3.amazonaws.com storage.googleapis.com blob.core.windows.net
""".split())

# Hostname labels that are delivery-network decoration rather than identity.
# Matched as whole labels, so a service genuinely called `media` is safe.
_DECORATION = re.compile(
    r"^(?:d|dl|cdn\d*|static\d*|media\d*|img\d*|image\d*|images|i|ii|is\d*|"
    r"files?|assets?|content|thumb(?:nail)?s?|preview|video\d*|v|vid|"
    r"stream\d*|download(?:s)?|uploads?|store|storage|data|res|resource|"
    r"pbs|edge|cache|fs|f|c|s|t|p|www\d*|web|api|gw|proxy|akamai|"
    r"scontent(?:-[a-z0-9-]+)?|instagram|fna|xx|global|origin|"
    r"[a-z]{2}\d?|[a-z]{1,3}-?\d{1,4}|eu|us|uk|asia|ap|na)$")


# A registrable domain whose own first label is one of these is a bucket
# rather than a service, and keeping the fuller name is more useful. Much
# narrower than _DECORATION on purpose: that pattern treats `[a-z]{1,3}\d+`
# as a delivery-network code, which is right for `s3` and `c1` at the front
# of a hostname and catastrophically wrong for `e621`, which is the service.
_BUCKET_NAME = re.compile(r"^(?:www\d*|cdn\d*|static\d*|media\d*|files?|"
                          r"assets?|storage|store|s3|content|data)$")


def registrable(hostname):
    """The registrable domain: `example.co.uk` from `cdn.foo.example.co.uk`."""
    if not hostname:
        return None
    host = str(hostname).strip().lower().rstrip(".")
    host = host.split("@")[-1].split(":")[0]
    if not host or _is_address(host):
        return host or None
    labels = [label for label in host.split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    for size in (3, 2):
        candidate = ".".join(labels[-size:])
        if candidate in MULTIPART_SUFFIXES and len(labels) > size:
            return ".".join(labels[-(size + 1):])
    return ".".join(labels[-2:])


def source(hostname):
    """A short name for the service, suitable for a folder.

    Derived, not looked up. `d.furaffinity.net` and
    `scontent-man2-1.xx.fbcdn.net` both reduce without either of them being
    known in advance.
    """
    domain = registrable(hostname)
    if not domain:
        return None
    if _is_address(domain):
        return domain
    first = domain.split(".")[0]
    # A registrable domain whose own first label is decoration -- `s3.amazon`
    # style -- keeps the fuller name rather than becoming `s3`.
    if _BUCKET_NAME.match(first) and "." in domain:
        return domain
    return first or domain


def _is_address(host):
    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", host):
        return True
    return ":" in host


def strip_decoration(hostname):
    """The hostname with delivery-network labels removed, for display."""
    if not hostname:
        return None
    labels = [label for label in str(hostname).lower().split(".") if label]
    while len(labels) > 2 and _DECORATION.match(labels[0]):
        labels.pop(0)
    return ".".join(labels)
