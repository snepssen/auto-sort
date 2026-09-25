"""A report somebody can paste into an issue, about a machine nobody here has.

auto-sort has been run on macOS and on KDE Plasma. Everywhere else it was
built to the specifications -- Win32, StatusNotifierItem, dbusmenu, the
freedesktop trash and user-dirs -- and nobody involved has seen it work.
When it does not, the person it failed for knows what happened and the
program knows why, and the two are usually in different places: they saw
no icon; the daemon knows no watcher answered, or that the desktop asked
the icon to draw its own menu.

So `auto-sort diagnose` puts the second next to the first, as plain text to
paste into an issue.

The generated report includes no file names, paths, document text or rule names.
Only known labels, numeric versions, counts and states leave the collectors.
Logs become fixed problem categories, never excerpts. Unknown text is omitted,
including saved tray errors and system metadata. This policy applies before
rendering, so JSON and text reports have the same privacy boundary. It is
printed, never sent: the person reads it before anybody else does.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys

import daemon as daemon_module
import ledger as ledger_module
import paths
import platform_support
import rules
import userdirs

# Lines worth showing from the daemon's log, and how many of the latest.
_PROBLEM = re.compile(r"could not|error|failed|traceback|unavailable|"
                      r"headless|stopped|refused", re.IGNORECASE)
_LOG_LINES = 15
_STAMP = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ")
# Never return text captured from a problem message. Even a bare word can
# be a document title; escaping or removing path separators cannot fix that.
_PROBLEM_CATEGORIES = (
    ("Rules error; sorting paused:", "rules invalid; sorting paused"),
    ("Could not tidy the ledger:", "ledger maintenance failed"),
    ("Could not check for corrections:", "checking corrections failed"),
    ("Could not look for new categories:", "category discovery failed"),
    ("Could not add ", "writing learned rules failed"),
    ("Could not look at filed files again:", "refiling failed"),
    ("Watched folder unavailable;", "watched folder unavailable"),
    ("Reader stopped ", "reader stopped responding"),
    ("Could not open a browser", "opening the browser failed"),
    ("Tray:", "tray unavailable; continuing headless"),
)
_FOLDERS = ("desktop", "documents", "downloads", "music", "pictures",
            "public", "templates", "video")
_WATCHERS = ("org.kde.StatusNotifierWatcher",
             "org.freedesktop.StatusNotifierWatcher")
_TRAY_METHODS = ("Activate", "SecondaryActivate", "ContextMenu", "Scroll",
                 "Get", "GetAll", "GetLayout", "GetGroupProperties",
                 "GetProperty", "Event", "EventGroup", "AboutToShow",
                 "AboutToShowGroup")


def _choice(value, choices):
    return value if isinstance(value, str) and value in choices else "other/omitted"


def _numeric_version(value):
    return value if isinstance(value, str) and re.fullmatch(
        r"[0-9]{1,10}(?:\.[0-9]{1,10}){0,4}", value) else "unknown"


def _error(error):
    """Useful error identity without exception messages or custom type names."""
    if isinstance(error, OSError):
        if type(error.errno) is int:
            return "OS error %d" % error.errno
        return "OS error"
    return "error (details omitted)"


def _problem_summary(line):
    stamp = _STAMP.match(line)
    bare = line[stamp.end():] if stamp else line
    category = next((label for prefix, label in _PROBLEM_CATEGORIES
                     if bare.startswith(prefix)), "other problem (details omitted)")
    return (stamp.group() if stamp else "") + category


def _standard_folders():
    folders = userdirs.all_dirs()
    return {name: ("available" if os.path.isdir(folders.get(name, ""))
                   else "missing or inaccessible") for name in _FOLDERS}


def collect(state_file=None, rule_path=None, bus=None):
    """Everything the report says, as a dict."""
    found = {"auto-sort": _version(), "python": _python(),
             "system": _system()}
    if not sys.platform.startswith(("win", "darwin")):
        found["desktop"] = _desktop()
        found["session bus"] = _bus(bus)
    found["daemon"] = _daemon(state_file)
    found["rules"] = _rules(rule_path)
    found["standard folders"] = _standard_folders()
    found["programs"] = dict(
        (row["key"], "installed" if row["installed"] else "not installed")
        for row in platform_support.inventory())
    found["recent problems in the log"] = _log_problems()
    return found


def render(found):
    """The report as text, fenced so an issue shows it as it is."""
    lines = ["```text", "auto-sort diagnostics -- no file names, paths, document text"
             " or rule names", ""]
    for section, value in found.items():
        if isinstance(value, dict):
            lines.append("%s:" % section)
            for key, item in value.items():
                lines.append("  %s: %s" % (key, _plain(item)))
        elif isinstance(value, list):
            lines.append("%s:%s" % (section, "" if value else " none"))
            lines.extend("  %s" % item for item in value)
        else:
            lines.append("%s: %s" % (section, value))
        lines.append("")
    lines.append("```")
    return "\n".join(lines)


ISSUES = "https://github.com/snepssen/auto-sort/issues/new"


def report_text(found, now=None):
    """The whole file somebody sends: what to do with it, then the report.

    Written for a person who has never filed an issue: the one thing only
    they can say goes at the top, with room to say it, and how to send the
    file comes before anything technical.
    """
    import time
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    return "\n".join([
        "auto-sort problem report, written %s" % stamp,
        "",
        "WHAT HAPPENED? Write it here, in your own words: what you did, what",
        "you expected, and what you saw instead. A screenshot helps too.",
        "",
        "",
        "",
        "HOW TO SEND IT: open %s" % ISSUES,
        "(a free GitHub account is needed), give it a short title, and drag",
        "this file into the box.",
        "",
        "The generated diagnostics contain no file names, paths, document text",
        "or rule names. Error details and unrecognised text are omitted.",
        "Review your description and screenshots too: issues are public.",
        "Nothing is sent automatically -- it is yours to send or not.",
        "",
        "-" * 72,
        render(found),
        "",
    ])


def write_to_desktop(found, folder=None, now=None):
    """Write the report beside everything else they can find: the Desktop.

    A new file every time, named by when it was written -- never over an
    older report somebody may still mean to send.
    """
    import time
    folder = folder or userdirs.path("desktop") or userdirs.home()
    if not os.path.isdir(folder):
        folder = userdirs.home()
    stamp = time.strftime("%Y-%m-%d %H%M", time.localtime(now))
    base = os.path.join(folder, "auto-sort problem report %s" % stamp)
    target, number = base + ".txt", 1
    while os.path.exists(target):
        number += 1
        target = "%s (%d).txt" % (base, number)
    with open(target, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(report_text(found, now))
    return target


def show(path):
    """Open the report in whatever this computer opens text files with."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)                           # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        return True
    except (OSError, AttributeError):
        return False


def _plain(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return value


def _version():
    import autosort
    here = os.path.dirname(os.path.abspath(__file__))
    commit = ""
    if os.path.isdir(os.path.join(here, ".git")):
        try:
            commit = subprocess.run(
                ["git", "-C", here, "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            commit = ""
    commit = commit if re.fullmatch(r"[0-9a-f]{7,40}", commit) else ""
    return _numeric_version(autosort.VERSION) + (" (%s)" % commit if commit else "")


def _python():
    return "%s %s, %d-bit" % (_choice(platform.python_implementation(),
                                      ("CPython", "PyPy", "Jython", "IronPython")),
                              _numeric_version(platform.python_version()),
                              8 * __import__("struct").calcsize("P"))


def _system():
    if sys.platform.startswith("linux"):
        # Kernel suffixes can contain custom build or machine names.
        release = re.match(r"[0-9]+(?:\.[0-9]+)*", platform.release())
        kernel = _numeric_version(release.group()) if release else "unknown"
        return "%s, Linux %s" % (_os_release(), kernel)
    if sys.platform == "darwin":
        return "macOS %s (%s)" % (_numeric_version(platform.mac_ver()[0]),
                                  _choice(platform.machine(), ("arm64", "x86_64")))
    if sys.platform.startswith("win"):
        return "Windows %s" % _numeric_version(platform.win32_ver()[1])
    return "other system"


def _os_release():
    for candidate in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            values = {}
            with open(candidate, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    key, separator, value = line.partition("=")
                    if separator and key in ("ID", "VERSION_ID"):
                        values[key] = value.strip().strip("\"'")
            distro = _choice(values.get("ID"), (
                "ubuntu", "debian", "fedora", "arch", "manjaro", "linuxmint",
                "opensuse", "opensuse-leap", "opensuse-tumbleweed", "steamos",
                "pop", "nixos", "gentoo", "alpine", "rhel", "centos", "rocky",
                "almalinux", "void", "endeavouros", "kali", "zorin"))
            return "%s %s" % (distro, _numeric_version(values.get("VERSION_ID")))
        except OSError:
            continue
    return "unknown distribution"


def _desktop():
    desktops = ("kde", "plasma", "gnome", "ubuntu", "unity", "xfce", "xfce4",
                "x-cinnamon", "cinnamon", "mate", "lxqt", "lxde", "sway",
                "hyprland", "i3", "budgie", "cosmic", "pantheon", "enlightenment")
    found = {}
    for name in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION"):
        value = os.environ.get(name, "")
        found[name] = ":".join(_choice(part.lower(), desktops)
                               for part in value.split(":")[:8]) if value else "not set"
    found["XDG_SESSION_TYPE"] = _choice(os.environ.get("XDG_SESSION_TYPE"),
                                       ("wayland", "x11", "tty"))
    for name in ("WAYLAND_DISPLAY", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
        found[name] = "set" if os.environ.get(name) else "not set"
    return found


def _bus(bus=None):
    """Who is on the session bus that a tray needs, and who is not."""
    import dbuswire
    import tray
    try:
        connection = bus or dbuswire.Connection()
    except Exception as error:               # noqa: BLE001
        return {"reachable": "no (%s)" % _error(error)}
    found = {"reachable": "yes"}
    try:
        for watcher in tray.WATCHERS:
            try:
                owner = connection.call(*dbuswire.BUS, member="GetNameOwner",
                                        signature="s", body=[watcher[0]])[0]
            except dbuswire.DBusError:
                found[watcher[0]] = "nobody"
                continue
            try:
                host = connection.call(
                    watcher[0], watcher[1], "org.freedesktop.DBus.Properties",
                    member="Get", signature="ss",
                    body=[watcher[2], "IsStatusNotifierHostRegistered"])[0]
                host = getattr(host, "value", host)
            except dbuswire.DBusError:
                host = "unknown"
            owner = owner if isinstance(owner, str) and re.fullmatch(
                r":[0-9]{1,10}\.[0-9]{1,10}", owner) else "present"
            host = host if type(host) is bool else "unknown"
            found[watcher[0]] = "%s, host registered: %s" % (owner, host)
        try:
            name, _vendor, version, spec = connection.call(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                member="GetServerInformation")[:4]
            found["notifications"] = "%s %s (spec %s)" % (
                _choice(name, ("Plasma", "gnome-shell", "GNOME Shell", "dunst",
                               "mako", "Xfce Notify Daemon", "Cinnamon",
                               "mate-notification-daemon")),
                _numeric_version(version), _numeric_version(spec))
        except dbuswire.DBusError:
            found["notifications"] = "nobody"
    finally:
        if bus is None:
            connection.close()
    return found


def _daemon(state_file):
    port, answered = daemon_module.probe(state_file)
    found = {"state": "not running" if port is None else
             ("running" if answered else "running, busy")}
    try:
        with ledger_module.Ledger(state_file) as journal:
            found["paused"] = "yes" if journal.paused() else "no"
            found["files filed"] = journal.connection.execute(
                "SELECT count(*) FROM moves WHERE status IN "
                "('done','copied')").fetchone()[0]
            counts = {}
            for row in journal.queue_counts():
                status = _choice(row["status"], ("pending", "processing",
                                                  "failed", "done"))
                counts[status] = counts.get(status, 0) + row["count"]
            found["queue"] = counts
            report = journal.get_state("tray_report")
    except Exception as error:               # noqa: BLE001
        found["ledger"] = "unreadable (%s)" % _error(error)
        report = None
    try:
        found["tray"] = _tray_report(json.loads(report)) if report else \
            "not recorded yet (the daemon records it while it runs)"
    except ValueError:
        found["tray"] = "unreadable"
    return found


def _tray_report(report):
    """Old ledgers may contain arbitrary exception text. Select fields afresh."""
    if not isinstance(report, dict):
        return "unreadable"
    found = {"backend": _choice(report.get("backend"), (
        "none", "StatusNotifierItem", "macOS status item", "Windows notification icon"))}
    for key in ("available", "registered"):
        if type(report.get(key)) is bool:
            found[key] = report[key]
    if "watcher" in report:
        found["watcher"] = _choice(report["watcher"], _WATCHERS + ("",))
    if report.get("reason"):
        found["reason"] = "unavailable (details omitted)"
    asked = report.get("host_asked")
    if isinstance(asked, dict):
        found["host_asked"] = {key: asked[key] for key in _TRAY_METHODS
                               if type(asked.get(key)) is int and asked[key] >= 0}
    return found


def _rules(rule_path):
    try:
        rule_set = rules.load(rule_path)
    except rules.RuleError:
        return {"loads": "no (invalid or unreadable; details omitted)"}
    settings = rule_set.settings
    return {"loads": "yes",
            "rules": len(rule_set.rules),
            "holding rules": sum(1 for rule in rule_set.rules
                                 if rule.holding),
            "watched folders": len(rule_set.watch.folders),
            "dry run": "yes" if settings.dry_run else "no",
            "regroup": settings.regroup,
            "learn": getattr(settings, "learn", "apply"),
            "ocr": getattr(settings, "ocr", ""),
            "tools": getattr(settings, "tools", "")}


def _log_problems():
    log = os.path.join(paths.state_dir(), "daemon.log")
    try:
        with open(log, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 256 * 1024))
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = [_problem_summary(line) for line in text.splitlines()
             if _PROBLEM.search(line)]
    # The same complaint every few seconds is one problem, said how often.
    collapsed = []
    for line in lines:
        bare = _STAMP.sub("", line)
        if collapsed and collapsed[-1][1] == bare:
            collapsed[-1][2] += 1
            collapsed[-1][0] = line
        else:
            collapsed.append([line, bare, 1])
    return ["%s%s" % (line, "  (x%d)" % count if count > 1 else "")
            for line, _bare, count in collapsed[-_LOG_LINES:]]
