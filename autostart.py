"""Opt-in, reversible per-user login launchers for auto-sort."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile

import paths


LABEL = "com.snepssen.auto-sort"


class AutostartError(Exception):
    pass


def platform_name():
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def command(rules_file):
    """The argv launchd, XDG autostart or the Startup folder will run.

    Whichever interpreter installed it, because there is nothing special
    about any of them: the tray needs no packages, so any Python that can run
    auto-sort at all can also show the icon.
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "autosort.py")
    executable = sys.executable
    if platform_name() == "windows":
        candidate = os.path.join(os.path.dirname(executable), "pythonw.exe")
        if os.path.exists(candidate):
            executable = candidate
    return [executable, script, "watch", "--rules", os.path.abspath(rules_file)]


def restart(runner=subprocess.run):
    """Ask the platform to stop and start the login item again.

    Only macOS has a service manager behind the login item: launchd owns the
    process and `kickstart -k` replaces it. An XDG autostart entry and a
    Startup-folder shortcut are instructions for the next login and nothing
    is managing the process in between, so there is nothing to ask -- the
    caller falls back to stopping the daemon and starting another itself.

    Returns (restarted, reason).
    """
    state = status()
    if not state["installed"]:
        return False, "no login item is installed"
    if state["platform"] != "macos":
        return False, ("%s starts auto-sort at login but does not manage it "
                       "afterwards" % state["platform"])
    result = runner(["launchctl", "kickstart", "-k",
                     "gui/%d/%s" % (os.getuid(), LABEL)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        return False, detail.strip() or "launchctl exited %d" % result.returncode
    return True, ""


def target():
    platform = platform_name()
    if platform == "macos":
        return os.path.join(os.path.expanduser("~/Library/LaunchAgents"),
                            LABEL + ".plist")
    if platform == "windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "Microsoft", "Windows", "Start Menu",
                            "Programs", "Startup", "auto-sort.lnk")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "autostart", "auto-sort.desktop")


def launcher_target():
    """Where a clickable "open auto-sort" entry lives, on desktops that have one.

    Linux only, and it is not the same file as `target()`. That one goes in
    `~/.config/autostart` and means "run this at login"; it puts nothing in
    the applications menu. macOS and Windows both get a tray icon, so the
    only desktop with no way in but a terminal was the one where the sorting
    worked first.
    """
    if platform_name() != "linux":
        return None
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "applications", "auto-sort.desktop")


def status():
    filename = target()
    result = {"platform": platform_name(), "path": filename,
              "installed": os.path.lexists(filename)}
    launcher = launcher_target()
    if launcher:
        result["launcher"] = launcher
        result["launcher_installed"] = os.path.lexists(launcher)
    return result


def install(rules_file, runner=subprocess.run):
    """Install and activate the current user's visible login launcher."""
    filename = target()
    platform = platform_name()
    paths.ensure(os.path.dirname(filename))
    if platform == "macos":
        _write_plist(filename, command(rules_file))
        _launchctl("bootout", filename, runner, allow_failure=True)
        _launchctl("bootstrap", filename, runner)
    elif platform == "windows":
        _write_windows_shortcut(filename, command(rules_file), runner)
    else:
        _write_text(filename, _desktop_entry(command(rules_file)), 0o644)
        # And a second entry, in the menu rather than in the login folder,
        # so somebody can open the page without being told a command.
        launcher = launcher_target()
        paths.ensure(os.path.dirname(launcher))
        _write_text(launcher, _launcher_entry(open_log_command()), 0o644)
    return status()


def remove(runner=subprocess.run):
    """Deactivate and remove only auto-sort's own per-user launcher."""
    filename = target()
    if platform_name() == "macos" and os.path.lexists(filename):
        _launchctl("bootout", filename, runner, allow_failure=True)
    if os.path.lexists(filename):
        os.unlink(filename)
    launcher = launcher_target()
    if launcher and os.path.lexists(launcher):
        os.unlink(launcher)
    return status()


def _write_plist(filename, arguments):
    document = {
        "Label": LABEL,
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": os.path.join(paths.state_dir(), "daemon.log"),
        "StandardErrorPath": os.path.join(paths.state_dir(), "daemon.log"),
    }
    paths.ensure(paths.state_dir())
    _write_bytes(filename, plistlib.dumps(document, fmt=plistlib.FMT_XML), 0o600)


def _launchctl(action, filename, runner, allow_failure=False):
    user = str(os.getuid())
    result = runner(["launchctl", action, "gui/" + user, filename],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=False)
    if result.returncode and not allow_failure:
        detail = (result.stderr or result.stdout or b"").decode(
            "utf-8", "replace").strip()
        raise AutostartError("launchctl %s failed: %s" % (action, detail))


def _desktop_entry(arguments):
    return """[Desktop Entry]
Type=Application
Name=auto-sort
Comment=Sort watched folders safely in the background
Exec=%s
Terminal=false
X-GNOME-Autostart-enabled=true
""" % " ".join(_desktop_quote(argument) for argument in arguments)


def open_log_command():
    """The argv that opens the page in a browser, token and all."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "autosort.py")
    return [sys.executable, script, "open-log"]


def _launcher_entry(arguments):
    """A menu entry that answers the question people open this to ask.

    Named for the question rather than the program: somebody looking for a
    file that has gone missing is not looking for a tool called auto-sort.
    """
    return """[Desktop Entry]
Type=Application
Name=Where your files went
GenericName=auto-sort
Comment=Find anything auto-sort has moved, and put it back
Exec=%s
Icon=folder
Terminal=false
Categories=Utility;FileTools;
Keywords=files;sort;downloads;missing;backup;
""" % " ".join(_desktop_quote(argument) for argument in arguments)


def _desktop_quote(value):
    """One Exec argument, spelled the way the desktop entry spec asks.

    Two layers, applied by a launcher in this order: the file's string
    escapes, then the Exec quoting. So a character the quoting escapes with
    one backslash is written with two, and a literal backslash with four.
    `%` is doubled or it is a field code. Only `"` and `\\` were escaped
    before, once: a rules file under a folder called `100%` wrote a field
    code, and desktop-file-validate rejected the entry that was supposed to
    start auto-sort at login.
    """
    quoted = "".join("\\" + char if char in '"`$\\' else char
                     for char in str(value))
    return '"' + quoted.replace("\\", "\\\\").replace("%", "%%") + '"'


def _write_windows_shortcut(filename, arguments, runner):
    powershell = shutil.which("powershell") or shutil.which("powershell.exe")
    if not powershell:
        raise AutostartError("PowerShell is required to create the Startup shortcut")
    executable, script = arguments[:2]
    tail = " ".join(_windows_argument(value) for value in arguments[2:])
    source = """
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut(%s)
$shortcut.TargetPath = %s
$shortcut.Arguments = %s
$shortcut.WorkingDirectory = %s
$shortcut.Save()
""" % (_powershell_quote(filename), _powershell_quote(executable),
       _powershell_quote(_windows_argument(script) + " " + tail),
       _powershell_quote(os.path.dirname(script)))
    result = runner([powershell, "-NoProfile", "-NonInteractive", "-Command", source],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or b"").decode(
            "utf-8", "replace").strip()
        raise AutostartError("could not create Startup shortcut: %s" % detail)


def _windows_argument(value):
    return '"' + str(value).replace('"', '\\"') + '"'


def _powershell_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _write_text(filename, source, mode):
    _write_bytes(filename, source.encode("utf-8"), mode)


def _write_bytes(filename, source, mode):
    directory = os.path.dirname(filename)
    paths.ensure(directory)
    descriptor, temporary = tempfile.mkstemp(prefix=".auto-sort-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(source)
        os.chmod(temporary, mode)
        os.replace(temporary, filename)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
