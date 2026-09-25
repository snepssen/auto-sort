"""The rules file: a small language, parsed rather than evaluated.

`when = kind = image and looks_like = screenshot` has to become a decision
about somebody's file, and the one thing it must never become is a call to
`eval`. A rules file is data that a background process reads on every change,
from a path that any program on the machine can write. So this is a hand
written recursive-descent parser over a closed grammar: identifiers,
comparisons, `and`, `or`, `not`, parentheses. Nothing in it can reach anything
outside the facts it was handed.

Two behaviours carry most of the weight.

**A fact that is unset makes every comparison false.** Not an error, not a
skip — false. A rule needing `duration` simply does not fire on a machine
without the reader that provides it, and the next rule gets its turn. This is
what lets one rules file work on a capable machine and a bare one.

**A destination that needs a missing fact is not a match.** `into =
~/Music/{artist}/{album}` with no artist tag does not produce a folder called
`Unknown`; it declines, and the next rule catches the file. That is how the
worked example in RULES.md hands untagged audio down from `[rule: tagged
music]` to `[rule: audio, unclassified]` without either rule naming the other.
"""

from __future__ import annotations

import datetime
import fnmatch
import hashlib
import os
import re
import sys

try:
    import configparser
except ImportError:                                    # pragma: no cover
    import ConfigParser as configparser                # noqa: N813

import paths

IS_WINDOWS = os.name == "nt"
PLATFORM = "windows" if IS_WINDOWS else (
    "macos" if sys.platform == "darwin" else "linux")


class RuleError(Exception):
    """A rules file that cannot be read, named precisely enough to fix."""


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

_SIZE_UNITS = {"b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3,
               "tb": 1024 ** 4, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}
_TIME_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

_TOKEN = re.compile(r"""
    (?P<space>\s+)
  | (?P<string>"[^"]*"|'[^']*')
  | (?P<number>\d+(?:\.\d+)?(?:[a-zA-Z]{1,2})?)
  | (?P<operator><=|>=|!=|=|<|>|~)
  | (?P<punctuation>[(),])
  | (?P<word>[^\s(),]+)
""", re.X)

_KEYWORDS = {"and", "or", "not", "is", "set", "unset", "in", "between",
             "re", "matches", "contains"}

# Words that join or negate an expression, and so can never also be the name
# of a fact. Every other keyword can: a fact called `contains` is a perfectly
# reasonable thing to have -- it is what a folder holds -- and the parser
# knows the difference from position, because a comparison always begins with
# a fact and the operator always follows one.
_CONNECTIVES = {"and", "or", "not"}


class Token(object):
    __slots__ = ("kind", "text", "at")

    def __init__(self, kind, text, at):
        self.kind = kind
        self.text = text
        self.at = at

    def __repr__(self):
        return "%s(%r)" % (self.kind, self.text)


def tokenise(source):
    tokens = []
    cursor = 0
    while cursor < len(source):
        match = _TOKEN.match(source, cursor)
        if match is None:
            raise RuleError("cannot read %r at column %d"
                            % (source[cursor], cursor + 1))
        cursor = match.end()
        kind = match.lastgroup
        text = match.group()
        if kind == "space":
            continue
        if kind == "string":
            tokens.append(Token("value", text[1:-1], match.start()))
        elif kind == "word" and text.lower() in _KEYWORDS:
            tokens.append(Token(text.lower(), text, match.start()))
        elif kind == "word":
            tokens.append(Token("word", text, match.start()))
        else:
            tokens.append(Token(kind, text, match.start()))
    return tokens


def _literal(text):
    """A token's text as the value it denotes: number, size, duration or word."""
    lowered = text.lower()
    if lowered in ("yes", "true", "on"):
        return True
    if lowered in ("no", "false", "off"):
        return False
    match = re.match(r"^(\d+(?:\.\d+)?)([a-z]{1,2})?$", lowered)
    if match:
        amount = float(match.group(1))
        suffix = match.group(2)
        if suffix:
            if suffix in _SIZE_UNITS and suffix not in ("m", "s"):
                return amount * _SIZE_UNITS[suffix]
            if suffix in _TIME_UNITS:
                return amount * _TIME_UNITS[suffix]
            if suffix in _SIZE_UNITS:
                return amount * _SIZE_UNITS[suffix]
            raise RuleError("unknown unit %r" % suffix)
        return int(amount) if amount == int(amount) else amount
    return text


# ---------------------------------------------------------------------------
# The expression tree
# ---------------------------------------------------------------------------

class Node(object):
    def evaluate(self, facts, trace=None):
        """Return ``(outcome, facts_consulted)`` for confidence checks.

        The second value contains only facts on the branch that was actually
        evaluated.  That distinction matters for ``or``: a weak, irrelevant
        fact on the unused branch must not make an otherwise certain match
        fail its confidence floor.
        """
        raise NotImplementedError

    def test(self, facts, trace=None):
        return self.evaluate(facts, trace)[0]

    def facts_used(self):
        return set()

    def comparisons(self):
        """Every `fact operator value` in this condition, in written order.

        A condition knows what it is made of; asking it is better than a
        report elsewhere taking the tree apart by guessing at attribute
        names, which would go quietly wrong the first time a node grew one.
        """
        return []


class And(Node):
    def __init__(self, left, right):
        self.left, self.right = left, right

    def evaluate(self, facts, trace=None):
        left, used = self.left.evaluate(facts, trace)
        if not left:
            return False, used
        right, right_used = self.right.evaluate(facts, trace)
        return right, used | right_used

    def facts_used(self):
        return self.left.facts_used() | self.right.facts_used()

    def comparisons(self):
        return self.left.comparisons() + self.right.comparisons()

    def __str__(self):
        return "%s and %s" % (self.left, self.right)


class Or(Node):
    def __init__(self, left, right):
        self.left, self.right = left, right

    def evaluate(self, facts, trace=None):
        left, used = self.left.evaluate(facts, trace)
        if left:
            return True, used
        right, right_used = self.right.evaluate(facts, trace)
        # When the right branch succeeds it is sufficient proof of the OR.
        # A low-confidence fact that happened to make the left branch false
        # is irrelevant to the successful match and must not lower it.
        return (True, right_used) if right else (False, used | right_used)

    def facts_used(self):
        return self.left.facts_used() | self.right.facts_used()

    def comparisons(self):
        return self.left.comparisons() + self.right.comparisons()

    def __str__(self):
        return "(%s or %s)" % (self.left, self.right)


class Not(Node):
    def __init__(self, inner):
        self.inner = inner

    def evaluate(self, facts, trace=None):
        outcome, used = self.inner.evaluate(facts, trace)
        return not outcome, used

    def facts_used(self):
        return self.inner.facts_used()

    def comparisons(self):
        return self.inner.comparisons()

    def __str__(self):
        return "not %s" % self.inner


class Compare(Node):
    """One `fact operator value`, and the only place a fact is ever read."""

    def __init__(self, fact, operator, value, second=None):
        self.fact = fact
        self.operator = operator
        self.value = value
        self.second = second
        self._regex = None
        if operator == "re":
            try:
                self._regex = re.compile(value)
            except re.error as error:
                raise RuleError("bad regular expression %r: %s"
                                % (value, error))

    def facts_used(self):
        return {self.fact}

    def comparisons(self):
        return [self]

    def evaluate(self, facts, trace=None):
        present = self.fact in facts and facts[self.fact] is not None
        if self.operator == "is set":
            outcome = present
        elif self.operator == "is unset":
            outcome = not present
        elif not present:
            # Everything else against an absent fact is false. Not an error:
            # a reader that did not run must narrow what matches, never widen
            # it.
            outcome = False
        else:
            outcome = self._apply(facts[self.fact])
        if trace is not None:
            trace.append((str(self), outcome,
                          facts.get(self.fact, "(unset)")))
        return outcome, ({self.fact} if present else set())

    def _apply(self, actual):
        operator = self.operator
        if operator == "in":
            return any(_equal(actual, option) for option in self.value)
        if operator == "between":
            low, high = _number(self.value), _number(self.second)
            here = _number(actual)
            return None not in (low, high, here) and low <= here <= high
        if operator == "~":
            return fnmatch.fnmatch(str(actual).lower(),
                                   str(self.value).lower())
        if operator == "re":
            return bool(self._regex.search(str(actual)))
        if operator == "contains":
            return str(self.value).lower() in str(actual).lower()
        if operator == "=":
            return _equal(actual, self.value)
        if operator == "!=":
            return not _equal(actual, self.value)
        left, right = _number(actual), _number(self.value)
        if left is None or right is None:
            # Dates are ISO strings, which sort correctly as text.
            left, right = str(actual), str(self.value)
        if operator == "<":
            return left < right
        if operator == "<=":
            return left <= right
        if operator == ">":
            return left > right
        if operator == ">=":
            return left >= right
        raise RuleError("unknown operator %r" % operator)

    def __str__(self):
        if self.operator in ("is set", "is unset"):
            return "%s %s" % (self.fact, self.operator)
        if self.operator == "in":
            return "%s in %s" % (self.fact,
                                 ", ".join(str(v) for v in self.value))
        if self.operator == "between":
            return "%s between %s and %s" % (self.fact, self.value,
                                             self.second)
        return "%s %s %s" % (self.fact, self.operator, self.value)


class Always(Node):
    def evaluate(self, facts, trace=None):
        return True, set()

    def __str__(self):
        return "always"


def _equal(actual, expected):
    if isinstance(actual, bool) or isinstance(expected, bool):
        left, right = _as_boolean(actual), _as_boolean(expected)
        return left is not None and right is not None and left == right
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(actual - expected) < 1e-9
    return str(actual).strip().lower() == str(expected).strip().lower()


def _as_boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    lowered = str(value).strip().lower()
    if lowered in ("yes", "true", "on", "1"):
        return True
    if lowered in ("no", "false", "off", "0"):
        return False
    return None


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------

class Parser(object):
    def __init__(self, source):
        self.source = source
        self.tokens = tokenise(source)
        self.position = 0

    def peek(self):
        return self.tokens[self.position] \
            if self.position < len(self.tokens) else None

    def take(self):
        token = self.peek()
        if token is None:
            raise RuleError("expression ended early: %r" % self.source)
        self.position += 1
        return token

    def accept(self, kind):
        token = self.peek()
        if token is not None and token.kind == kind:
            self.position += 1
            return token
        return None

    def parse(self):
        if not self.tokens:
            return Always()
        node = self.expression()
        if self.position < len(self.tokens):
            token = self.tokens[self.position]
            raise RuleError("unexpected %r at column %d"
                            % (token.text, token.at + 1))
        return node

    def expression(self):
        node = self.term()
        while self.accept("or"):
            node = Or(node, self.term())
        return node

    def term(self):
        node = self.factor()
        while self.accept("and"):
            node = And(node, self.factor())
        return node

    def factor(self):
        if self.accept("not"):
            return Not(self.factor())
        token = self.peek()
        if token is not None and token.kind == "punctuation" \
                and token.text == "(":
            self.take()
            node = self.expression()
            closing = self.take()
            if closing.text != ")":
                raise RuleError("expected ) at column %d" % (closing.at + 1))
            return node
        return self.comparison()

    def comparison(self):
        name = self.take()
        if name.kind not in ("word", "value") \
                and name.kind not in (_KEYWORDS - _CONNECTIVES):
            raise RuleError("expected a fact name, found %r at column %d"
                            % (name.text, name.at + 1))
        fact = name.text

        if self.accept("is"):
            state = self.take()
            if state.kind not in ("set", "unset"):
                raise RuleError("expected 'set' or 'unset' after 'is', "
                                "found %r" % state.text)
            return Compare(fact, "is " + state.kind, None)

        if self.accept("in"):
            options = [_literal(self.take().text)]
            while self.peek() is not None \
                    and self.peek().kind == "punctuation" \
                    and self.peek().text == ",":
                self.take()
                options.append(_literal(self.take().text))
            return Compare(fact, "in", options)

        if self.accept("between"):
            low = _literal(self.take().text)
            if not self.accept("and"):
                raise RuleError("'between' needs 'and': %s" % self.source)
            high = _literal(self.take().text)
            return Compare(fact, "between", low, high)

        for keyword in ("re", "matches", "contains"):
            if self.accept(keyword):
                operator = "re" if keyword in ("re", "matches") else "contains"
                return Compare(fact, operator, self.take().text)

        operator = self.take()
        if operator.kind != "operator":
            raise RuleError("expected a comparison after %r, found %r "
                            "at column %d" % (fact, operator.text,
                                              operator.at + 1))
        return Compare(fact, operator.text, _literal(self.take().text))


def parse(source):
    return Parser(source).parse()


# ---------------------------------------------------------------------------
# Configuration and decisions
# ---------------------------------------------------------------------------

_SETTINGS_KEYS = {
    "dry_run", "unsorted", "unsorted_into", "on_collision",
    "min_confidence", "settle_seconds", "poll_seconds", "preserve_dates",
    "ocr", "tools",
    "regroup", "learn", "preview_wait",
}
_WATCH_KEYS = {"folders", "ignore", "depth"}
_RULE_KEYS = {
    "when", "into", "as", "extract", "holding", "mode", "stop",
    "min_confidence",
    "newer_than", "older_than", "only_on",
}
_TEMPLATE = re.compile(r"\{([^{}]+)\}")


class Settings(object):
    def __init__(self, values=None):
        values = values or {}
        self.dry_run = _boolean(values.get("dry_run", "yes"), "dry_run")
        self.unsorted = _choice(values.get("unsorted", "leave"),
                                ("leave", "gather"), "unsorted")
        self.unsorted_into = values.get("unsorted_into", "~/Unsorted")
        # What the background sorter does when files it already filed could
        # now be placed better: out of a holding folder once a pattern
        # shows, or into a category a new rule or a better reader finds.
        # `apply` is the default because the mess somebody installs this
        # for is the old one as much as the new arrivals; every such move
        # is in the ledger and undone like any other. `report` only says.
        self.regroup = _choice(values.get("regroup", "apply"),
                               ("off", "report", "apply"), "regroup")
        # Whether the background sorter adds a category to this file by
        # itself, once enough waiting documents have shown it -- the one
        # thing it ever writes here. `report` says so in the log instead.
        self.learn = _choice(values.get("learn", "apply"),
                             ("off", "report", "apply"), "learn")
        # How long a preview waits for somebody before it counts as
        # approved and sorting starts. Without it the daemon showed where
        # everything would go and then waited for a click that somebody
        # who installed it and walked away would never make: installed,
        # proposed, asleep for ever, deleted in exasperation. `never` waits
        # for Resume, as it used to.
        self.preview_wait = _wait(values.get("preview_wait", "15m"),
                                  "preview_wait")
        self.on_collision = _choice(values.get("on_collision", "suffix"),
                                    ("suffix", "skip"), "on_collision")
        self.min_confidence = _bounded_float(
            values.get("min_confidence", 0.6), "min_confidence", 0.0, 1.0)
        self.settle_seconds = _nonnegative_float(
            values.get("settle_seconds", 3), "settle_seconds")
        self.poll_seconds = _positive_float(
            values.get("poll_seconds", 5), "poll_seconds")
        self.preserve_dates = _boolean(
            values.get("preserve_dates", "yes"), "preserve_dates")
        # Whether to read pages that were scanned rather than typed. `auto`
        # means "if an OCR program is installed", which for most people is
        # the same as "no" until they install one -- the switch is really
        # the install. `off` is for somebody who has one for other reasons
        # and does not want seconds of processor time spent on each page.
        self.ocr = _choice(values.get("ocr", "auto"), ("auto", "off"), "ocr")
        # Where the optional programs run. `auto` gives each installed one a
        # process of its own, started when a file first needs it and stopped
        # again when it has been idle a while. `inline` runs them inside the
        # worker that identified the file, which is one process fewer and
        # one slow page away from that worker doing nothing else. `off`
        # means no external program is ever run at all.
        self.tools = _choice(values.get("tools", "auto"),
                             ("auto", "inline", "off"), "tools")


class Watch(object):
    def __init__(self, values=None):
        values = values or {}
        self.folders = [_expand_path(value) for value in
                        _comma_list(values.get("folders", ""))]
        self.ignore = _comma_list(values.get("ignore", ""))
        self.depth = _nonnegative_int(values.get("depth", 3), "watch.depth")


class Evaluation(object):
    """The explainable result of trying one rule against one item."""

    def __init__(self, rule, matched, reason, trace=None, destination=None):
        self.rule = rule
        self.matched = matched
        self.reason = reason
        self.trace = trace or []
        self.destination = destination


class Rule(object):
    def __init__(self, name, values, default_confidence=0.6):
        self.name = name
        self.when_text = values.get("when", "")
        try:
            self.condition = parse(self.when_text)
        except RuleError as error:
            raise RuleError("rule %r: %s" % (name, error))
        self.into = values.get("into")
        self.rename = values.get("as")
        self.extract_from, self.extract = _parse_extract(
            values.get("extract"), name)
        self.mode = _choice(values.get("mode", "move"),
                            ("move", "copy", "leave"),
                            "rule %r mode" % name)
        self.stop = _boolean(values.get("stop", "yes"),
                             "rule %r stop" % name)
        # A holding rule is a place to put something until there is a better
        # answer, rather than an answer. Marking it says two things: that its
        # destination is provisional, and that a file it placed may later be
        # promoted out of it when the folder has taught auto-sort enough to
        # do better. Files placed by an ordinary rule are never reconsidered.
        self.holding = _boolean(values.get("holding", "no"),
                                "rule %r holding" % name)
        self.min_confidence = _bounded_float(
            values.get("min_confidence", default_confidence),
            "rule %r min_confidence" % name, 0.0, 1.0)
        self.newer_than = _age_days(values.get("newer_than"),
                                    "rule %r newer_than" % name)
        self.older_than = _age_days(values.get("older_than"),
                                    "rule %r older_than" % name)
        self.only_on = set(value.lower() for value in
                           _comma_list(values.get("only_on", "")))
        unknown_platforms = self.only_on - {"macos", "windows", "linux"}
        if unknown_platforms:
            raise RuleError("rule %r only_on names unknown platform(s): %s"
                            % (name, ", ".join(sorted(unknown_platforms))))
        if self.mode != "leave" and not self.into:
            raise RuleError("rule %r needs 'into' unless mode = leave" % name)
        _check_template(self.into, "rule %r into" % name)
        _check_template(self.rename, "rule %r as" % name)
        self.template_facts = _template_fields(self.into) \
            | _template_fields(self.rename)
        # Names the extract produces are filled in per file, so they are not
        # facts the record is expected to carry and must not be gated as if
        # they were. What is gated is the fact they are extracted *from*.
        self.extracted_names = frozenset(
            self.extract.groupindex) if self.extract else frozenset()
        self.template_facts -= self.extracted_names
        if self.extract_from:
            self.template_facts.add(self.extract_from)

    def evaluate(self, record, source_root=None, platform=PLATFORM):
        facts = record.as_dict() if hasattr(record, "as_dict") else dict(record)
        trace = []
        if self.only_on and platform not in self.only_on:
            return Evaluation(self, False, "not enabled on %s" % platform)

        age = facts.get("age")
        if self.newer_than is not None \
                and (age is None or _number(age) is None
                     or float(age) >= self.newer_than):
            return Evaluation(self, False, "not newer than %g days"
                              % self.newer_than)
        if self.older_than is not None \
                and (age is None or _number(age) is None
                     or float(age) <= self.older_than):
            return Evaluation(self, False, "not older than %g days"
                              % self.older_than)

        matched, consulted = self.condition.evaluate(facts, trace)
        if not matched:
            return Evaluation(self, False, _NO_MATCH, trace)

        weak = []
        if hasattr(record, "confidence"):
            path_evidence = set(fact for fact in self.template_facts
                                if facts.get(fact) not in (None, ""))
            for fact in sorted(consulted | path_evidence):
                confidence = record.confidence(fact)
                if confidence < self.min_confidence:
                    weak.append("%s %.2f" % (fact, confidence))
        if weak:
            return Evaluation(
                self, False,
                "below confidence %.2f: %s"
                % (self.min_confidence, ", ".join(weak)), trace)

        if self.extract is not None:
            subject = facts.get(self.extract_from)
            if subject is None:
                return Evaluation(self, False,
                                  "extract needs missing fact %r"
                                  % self.extract_from, trace)
            found = self.extract.search(str(subject))
            if found is None:
                return Evaluation(self, False,
                                  "extract did not match %s=%r"
                                  % (self.extract_from, str(subject)[:60]),
                                  trace)
            facts = dict(facts)
            for key, value in found.groupdict().items():
                if value:
                    facts[key] = value

        if self.mode == "leave":
            return Evaluation(self, True, "matched; leave in place", trace)

        root = source_root or facts.get("source_root") or facts.get("dir")
        try:
            directory = render_template(self.into, facts)
            if not paths.rooted(directory):
                if not root:
                    return Evaluation(self, False,
                                      "relative destination has no source root",
                                      trace)
                directory = os.path.join(root, directory)
            directory = os.path.abspath(directory)
            if root and not paths.rooted(_expand_path(self.into)) \
                    and not paths.inside(root, directory):
                return Evaluation(self, False,
                                  "destination escapes the source root", trace)
            filename = render_template(self.rename, facts) \
                if self.rename else paths.sanitise(facts.get("name", "_"))
        except _MissingFact as error:
            return Evaluation(self, False,
                              "destination needs missing fact %r" % error.fact,
                              trace)
        except RuleError as error:
            return Evaluation(self, False, "destination: %s" % error, trace)

        return Evaluation(self, True, "matched", trace,
                          os.path.join(directory, filename))


# Facts an extract may not produce, because they decide what a file is
# called and where it lives. A pattern capturing `(?P<name>...)` reads as
# innocent and renames every file it touches to the captured text, losing the
# extension with it -- found exactly that way, on a real folder, by an
# extract whose group happened to be called `name`.
PROTECTED_EXTRACT_NAMES = frozenset((
    "name", "stem", "ext", "path", "dir", "size", "kind", "format",
    "modified", "added", "age", "bundle", "members", "is_dir",
))


def _parse_extract(text, rule_name):
    """`extract = stem re ^\\d+\\.(?P<artist>[a-z-]+)_`

    One fact, the word `re`, and a pattern with named groups. The groups
    become tokens the destination can use, and a file the pattern does not
    match makes the rule decline rather than fail -- the same way a missing
    fact does. A convention that covers most of a folder can therefore be
    written without having to describe its exceptions.

    This is what lets a *learnt* naming convention be written down. `propose`
    discovers that a folder is full of names shaped a particular way and that
    one field inside them repeats; what it emits is a rule of exactly this
    form. So the thing the survey worked out is visible, editable, and no
    different in kind from something somebody wrote by hand.
    """
    if not text:
        return None, None
    match = re.match(r"^\s*(?P<fact>[A-Za-z_][A-Za-z0-9_.]*)\s+"
                     r"(?:re|matches)\s+(?P<pattern>.+?)\s*$", text)
    if not match:
        raise RuleError("rule %r extract must read 'FACT re PATTERN', "
                        "not %r" % (rule_name, text))
    try:
        compiled = re.compile(match.group("pattern"))
    except re.error as error:
        raise RuleError("rule %r extract pattern: %s" % (rule_name, error))
    if not compiled.groupindex:
        raise RuleError("rule %r extract pattern has no named groups, so it "
                        "produces nothing: %s"
                        % (rule_name, match.group("pattern")))
    clashes = PROTECTED_EXTRACT_NAMES.intersection(compiled.groupindex)
    if clashes:
        raise RuleError(
            "rule %r extract captures %s, which would overwrite the file's "
            "own %s and rename every file it matches. Choose another name "
            "for the group."
            % (rule_name, ", ".join(sorted(clashes)),
               "identity" if len(clashes) > 1 else sorted(clashes)[0]))
    return match.group("fact"), compiled


# The one reason that means "this rule was simply not about this file", and
# so is never worth reporting back to anybody.
_NO_MATCH = "when expression did not match"


class RuleSet(object):
    def __init__(self, settings, watch, rules_list, source=None,
                 source_hash=None):
        self.settings = settings
        self.watch = watch
        self.rules = rules_list
        self.source = source
        self.source_hash = source_hash

    def source_root_for(self, item_path):
        candidates = [folder for folder in self.watch.folders
                      if paths.inside(folder, item_path)]
        return max(candidates, key=len) if candidates else None

    def evaluate(self, record, source_root=None, platform=PLATFORM):
        """Try rules in file order, stopping when a matching rule says to."""
        evaluations = []
        for rule in self.rules:
            result = rule.evaluate(record, source_root, platform)
            evaluations.append(result)
            if result.matched and rule.stop:
                break
        return evaluations

    def decision(self, record, source_root=None, platform=PLATFORM):
        return self.decide(record, source_root, platform)[0]

    def decide(self, record, source_root=None, platform=PLATFORM):
        """(the decision or None, the most informative near miss or None).

        A rule whose `when` did not match is not interesting — that is the
        normal case for every rule but one. A rule that *did* match and then
        failed a later gate is the whole explanation for a file that stayed
        put, and it was previously reachable only by running `explain` again
        by hand. Both come out of one pass, because evaluating the whole rule
        set twice to produce a sentence is not worth it on a folder with
        forty thousand files in it.
        """
        near_miss = None
        for result in self.evaluate(record, source_root, platform):
            if result.matched and (result.rule.mode == "leave"
                                   or result.destination is not None):
                return result, None
            if near_miss is None and not result.matched \
                    and result.reason != _NO_MATCH:
                near_miss = result
        return None, near_miss


def load(filename=None):
    """Read and validate a complete rules file without changing it."""
    filename = paths.rules_file(filename)
    parser = configparser.ConfigParser(
        interpolation=None, inline_comment_prefixes=(";", "#"),
        empty_lines_in_values=False)
    try:
        with open(filename, "r", encoding="utf-8") as handle:
            source_text = handle.read()
        parser.read_string(source_text, source=filename)
    except FileNotFoundError:
        # The likeliest first experience anybody has of this tool, so it gets
        # a sentence rather than an errno. A sorter that greets somebody with
        # a missing-file error and no next step is a sorter they delete.
        raise RuleError(
            "no rules file at %s\n"
            "Run `auto-sort init` to write a starter one, or pass --rules FILE."
            % filename)
    except OSError as error:
        raise RuleError("cannot read %s: %s" % (filename, error))
    except configparser.Error as error:
        raise RuleError("cannot parse %s: %s" % (filename, error))

    allowed_sections = {"settings", "watch"}
    for section in parser.sections():
        if section in allowed_sections or section.lower().startswith("rule:"):
            continue
        raise RuleError("unknown section [%s] in %s" % (section, filename))

    setting_values = _section(parser, "settings", _SETTINGS_KEYS, filename)
    watch_values = _section(parser, "watch", _WATCH_KEYS, filename)
    settings = Settings(setting_values)
    watch = Watch(watch_values)
    loaded_rules = []
    names = set()
    for section in parser.sections():
        if not section.lower().startswith("rule:"):
            continue
        name = section.split(":", 1)[1].strip()
        if not name:
            raise RuleError("empty rule name in [%s]" % section)
        folded = name.lower()
        if folded in names:
            raise RuleError("duplicate rule name %r" % name)
        names.add(folded)
        values = _section(parser, section, _RULE_KEYS, filename)
        loaded_rules.append(Rule(name, values, settings.min_confidence))
    source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    return RuleSet(settings, watch, loaded_rules, os.path.abspath(filename),
                   source_hash)


def render_template(template, facts):
    """Expand a destination template, sanitising every produced component."""
    if template is None:
        return None
    expanded = _expand_path(template)
    drive, tail = os.path.splitdrive(expanded)
    absolute = tail.startswith(("/", "\\"))
    components = [part for part in re.split(r"[/\\]+", tail) if part]
    rendered = []
    for component in components:
        # A `.` or `..` written in the template is path navigation and has to
        # be handled before substitution, never after. `./out` is a natural
        # way to write a relative destination and sanitising it as a name
        # produced a literal folder called `_`. The same two characters
        # arriving *from a fact* — an album tag reading `..` — are a traversal
        # attempt and must still be sanitised into a harmless name, which is
        # why this looks at the raw component only.
        if component == ".":
            continue
        if component == "..":
            raise RuleError("a destination cannot contain '..': %r" % template)
        value = _TEMPLATE.sub(lambda match: _template_value(match, facts),
                              component)
        rendered.append(paths.sanitise(value))
    result = os.path.join(*rendered) if rendered else ""
    if absolute:
        result = os.sep + result
    if drive:
        result = drive + result
    elif absolute and os.name == "nt":
        # `into = /Sorted/...` on Windows means the root of the current
        # drive. Python 3.8 called that absolute and 3.13 does not, so the
        # same rule filed to D:\Sorted on one and was refused on the other.
        # Windows' own answer, on every version.
        result = os.path.abspath(result)
    return result or (os.sep if absolute else "_")


class _MissingFact(Exception):
    def __init__(self, fact):
        super().__init__(fact)
        self.fact = fact


def _template_value(match, facts):
    expression = match.group(1)
    field_and_format, separator, fallback = expression.partition("|")
    field, colon, format_spec = field_and_format.partition(":")
    field = field.strip()
    if not field:
        raise RuleError("empty template token")
    value = facts.get(field)
    if value is None or value == "":
        if separator:
            value = fallback
        else:
            raise _MissingFact(field)
    if colon:
        try:
            if format_spec.strip() in _CASES:
                # `{ext:upper}` -- a folder called PDF, not pdf, for the
                # extension somebody sees at the end of the file's name.
                value = getattr(str(value), format_spec.strip())()
            elif "%" in format_spec:
                moment = _datetime(value)
                if moment is None:
                    raise ValueError("not a date")
                value = moment.strftime(format_spec)
            else:
                value = format(value, format_spec)
        except (TypeError, ValueError) as error:
            raise RuleError("cannot format %s=%r with %r: %s"
                            % (field, value, format_spec, error))
    return str(value)


_CASES = ("upper", "lower", "title")


def _wait(value, key):
    """Seconds, from `15m`, `1h` or `90s`; None for `never`."""
    text = str(value).strip().lower()
    if text in ("never", "no", "off"):
        return None
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([smhdw]?)$", text)
    if not match:
        raise RuleError("%s must be a time such as 15m or 1h, or never; "
                        "got %r" % (key, value))
    return float(match.group(1)) * _TIME_UNITS[match.group(2) or "s"]


def _datetime(value):
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        return datetime.datetime.combine(value, datetime.time())
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _check_template(template, label):
    if template is None:
        return
    without_tokens = _TEMPLATE.sub("", template)
    if "{" in without_tokens or "}" in without_tokens:
        raise RuleError("%s has unmatched braces" % label)
    for match in _TEMPLATE.finditer(template):
        field = match.group(1).partition("|")[0].partition(":")[0].strip()
        if not field:
            raise RuleError("%s has an empty token" % label)


def _template_fields(template):
    if template is None:
        return set()
    return set(match.group(1).partition("|")[0].partition(":")[0].strip()
               for match in _TEMPLATE.finditer(template))


def _section(parser, name, allowed, filename):
    if not parser.has_section(name):
        return {}
    values = dict(parser.items(name))
    unknown = set(values) - allowed
    if unknown:
        raise RuleError("unknown key(s) in [%s] in %s: %s"
                        % (name, filename, ", ".join(sorted(unknown))))
    return values


def _expand_path(value):
    # Expand configuration-owned text before inserting untrusted fact values.
    # Expanding after rendering would let a tag such as "$HOME" reach outside
    # the destination the user wrote.
    return os.path.expanduser(os.path.expandvars(str(value).strip()))


def _comma_list(value):
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _choice(value, choices, label):
    result = str(value).strip().lower()
    if result not in choices:
        raise RuleError("%s must be one of %s, not %r"
                        % (label, ", ".join(choices), value))
    return result


def _boolean(value, label):
    lowered = str(value).strip().lower()
    if lowered in ("yes", "true", "on", "1"):
        return True
    if lowered in ("no", "false", "off", "0"):
        return False
    raise RuleError("%s must be yes or no, not %r" % (label, value))


def _bounded_float(value, label, low, high):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise RuleError("%s must be a number, not %r" % (label, value))
    if not low <= result <= high:
        raise RuleError("%s must be between %s and %s" % (label, low, high))
    return result


def _nonnegative_float(value, label):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise RuleError("%s must be a number, not %r" % (label, value))
    if result < 0:
        raise RuleError("%s must not be negative" % label)
    return result


def _positive_float(value, label):
    result = _nonnegative_float(value, label)
    if result == 0:
        raise RuleError("%s must be greater than zero" % label)
    return result


def _nonnegative_int(value, label):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise RuleError("%s must be a whole number, not %r" % (label, value))
    if result < 0:
        raise RuleError("%s must not be negative" % label)
    return result


def _age_days(value, label):
    if value is None or str(value).strip() == "":
        return None
    literal = _literal(str(value).strip())
    if not isinstance(literal, (int, float)) or isinstance(literal, bool):
        raise RuleError("%s must be a duration such as 30d" % label)
    # _literal returns seconds for suffixed durations. Bare values are already
    # days, matching the `age` fact emitted by identify.py.
    has_suffix = bool(re.match(r"^\d+(?:\.\d+)?[a-zA-Z]+$",
                               str(value).strip()))
    return float(literal) / 86400.0 if has_suffix else float(literal)
