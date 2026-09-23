"""Who this computer belongs to, which is on every document they own.

auto-sort does not know what a `Rechnung` is, and the whole design rests on
not knowing: categories come from counting what turns up, in whatever
language the post arrives in. But there is one word it can know without a
table, because the operating system was told it when the account was made,
and it is the single most useless word in a household's paperwork.

Your name is at the top of your payslip, your tenancy agreement, your tax
assessment and your phone bill. It heads fifty documents and divides none of
them: a folder named after yourself, inside your own home folder, is a
folder of everything. The induction cannot see that -- fifty documents out
of five hundred is exactly the shape of a real category -- so it is told.

What this is careful about: a surname is often also a word. `Koch` is a
cook, `Baker` is a baker, `Bill` is a bill. So the name is not banned; it is
held to a much lower ceiling than other words. Above it, the name is
letterhead and is dropped. Below it, a word that happens to be somebody's
name is far likelier to be the language than the letterhead, and it is kept.

Nothing here is sent anywhere, and nothing is stored: it is read from the
account this program is already running as.
"""

from __future__ import annotations

import os
import re

# Above this share of the documents, the owner's own name is a letterhead
# rather than a category. Much lower than the ceiling other words get,
# because a name at the top of a pile of post describes the pile.
MAX_SHARE = 0.1

_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)

# Parts of a full name that are not the name: the comma-separated fields
# some systems put in the same record, and the honorifics people type in.
_NOT_A_NAME = frozenset("""
mr mrs miss ms dr prof sir dame herr frau dhr mevr sr sra
admin administrator user guest owner root home office work test
""".split())

_cached = None
_account = None


def _from_account():
    """The full name the account was created with, where a system keeps one."""
    try:
        import pwd
    except ImportError:                      # Windows has no pwd module
        return ""
    try:
        record = pwd.getpwuid(os.getuid())
    except (KeyError, OSError, AttributeError):
        return ""
    # The GECOS field is comma-separated -- name, office, telephone -- and
    # only the first part of it is a person.
    return str(getattr(record, "pw_gecos", "") or "")


def _words(source):
    """The name out of one field, whatever else that field is carrying.

    The comma split lives here rather than beside `pwd` so that every
    source gets it: a Windows machine with a full name in the environment
    can carry the same "name, office, telephone" shape.
    """
    first = str(source or "").split(",")[0]
    return set(word.lower() for word in _WORD.findall(first)
               if word.lower() not in _NOT_A_NAME)


def names(refresh=False):
    """The owner's name, as a set of folded words. Possibly empty.

    Empty is an ordinary answer: an account made without a full name knows
    only a login, and that is a different thing -- see `account`.
    """
    global _cached
    if _cached is None or refresh:
        # Asking the machine who it belongs to must never be the reason a
        # sort fails. An account record that cannot be read leaves this
        # empty, which is the same answer as an account with no name.
        try:
            _cached = (_words(_from_account())
                       | _words(os.environ.get("USERFULLNAME", "")))
        except Exception:                    # noqa: BLE001
            _cached = set()
    return _cached


def account(refresh=False):
    """The login name, the home folder's name, and this machine's name.

    Kept apart from the name because it is a different kind of thing, and
    often a sillier one: plenty of people use an online moniker as a login
    and call the machine something like `sausage@factory`. Those are just
    as useless as a category and just as much a letterhead -- they turn up
    in exported headers and printed paths across everything the person
    owns.

    They are not banned, and the reason is the moniker: `sausage` is a
    perfectly good username and a perfectly good thing for a butcher's
    invoice to say at the top. What settles it is not the word but whether
    the documents ever use it as one -- see `shapes._bare_words`.
    """
    global _account
    if _account is None or refresh:
        try:
            _account = (_words(os.path.basename(os.path.expanduser("~")))
                        | _words(os.environ.get("USER", ""))
                        | _words(os.environ.get("USERNAME", ""))
                        | _words(_machine()))
        except Exception:                    # noqa: BLE001
            _account = set()
    return _account


def _machine():
    """What this computer calls itself, minus the parts that are not a name."""
    try:
        import socket
        name = socket.gethostname() or ""
    except Exception:                        # noqa: BLE001
        return ""
    # `sausage.local`, `factory.lan`, `Tamas-MacBook-Pro.fritz.box`: the
    # suffix is the network's, not the machine's.
    return re.sub(r"\.(local|lan|home|internal|box|fritz\.box)$", "",
                  str(name), flags=re.I).replace("-", " ").replace(".", " ")


def forget():
    """For tests, and for an account renamed under a running daemon."""
    global _cached, _account
    _cached = None
    _account = None
