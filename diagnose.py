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

**Nothing in it names a file.** Not a path in the watched folders, not a rule
name -- rules are learnt from somebody's own documents, and theirs can say
whose payslips these are -- and every line taken from the daemon's log has
its paths and quoted names taken out. The home folder is `~`. It is printed,
never sent: the person reads it before anybody else does.
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
# A quoted name, or anything with a path separator in it.
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_PATHLIKE = re.compile(r"(?:[A-Za-z]:)?[~.]?[\\/][^\s,;:()]*")


def redact(line):
    """A log line with nothing left in it that could name a file."""
    line = line.replace(userdirs.home(), "~")
    line = _QUOTED.sub("<name>", line)
    return _PATHLIKE.sub("<path>", line)


def collect(state_file=None, rule_path=None, bus=None):
    """Everything the report says, as a dict."""
    found = {"auto-sort": _version(), "python": _python(),
             "system": _system()}
    if not sys.platform.startswith(("win", "darwin")):
        found["desktop"] = _desktop()
        found["session bus"] = _bus(bus)
    found["daemon"] = _daemon(state_file)
    found["rules"] = _rules(rule_path)
    found["standard folders"] = dict(
        (name, userdirs.short(folder))
        for name, folder in sorted(userdirs.all_dirs().items()))
    found["programs"] = dict(
        (row["key"], "installed" if row["installed"] else "not installed")
        for row in platform_support.inventory())
    found["recent problems in the log"] = _log_problems()
    return found


def render(found):
    """The report as text, fenced so an issue shows it as it is."""
    lines = ["```text", "auto-sort diagnostics -- nothing here names a file;"
             " read it before posting", ""]
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
    return autosort.VERSION + (" (%s)" % commit if commit else "")


def _python():
    return "%s %s, %d-bit" % (platform.python_implementation(),
                              platform.python_version(),
                              8 * __import__("struct").calcsize("P"))


def _system():
    if sys.platform.startswith("linux"):
        name = _os_release()
        return "%s, Linux %s" % (name, platform.release()) if name \
            else "Linux %s" % platform.release()
    if sys.platform == "darwin":
        return "macOS %s (%s)" % (platform.mac_ver()[0], platform.machine())
    return platform.platform()


def _os_release():
    for candidate in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(candidate, encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            continue
    return ""


def _desktop():
    names = ("XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "DESKTOP_SESSION")
    found = dict((name, os.environ.get(name, "")) for name in names)
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
        return {"reachable": "no (%s)" % error}
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
            found[watcher[0]] = "%s, host registered: %s" % (owner, host)
        try:
            name, vendor, version, spec = connection.call(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                member="GetServerInformation")[:4]
            found["notifications"] = "%s %s (%s, spec %s)" % (
                name, version, vendor, spec)
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
            found["queue"] = dict((row["status"], row["count"])
                                  for row in journal.queue_counts())
            report = journal.get_state("tray_report")
    except Exception as error:               # noqa: BLE001
        found["ledger"] = "unreadable (%s)" % type(error).__name__
        report = None
    try:
        found["tray"] = json.loads(report) if report else \
            "not recorded yet (the daemon records it while it runs)"
    except ValueError:
        found["tray"] = "unreadable"
    return found


def _rules(rule_path):
    try:
        rule_set = rules.load(rule_path)
    except rules.RuleError as error:
        return {"loads": "no: %s" % redact(str(error))}
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
    lines = [redact(line)[:200] for line in text.splitlines()
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
