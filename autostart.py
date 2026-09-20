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


def status():
    filename = target()
    return {"platform": platform_name(), "path": filename,
            "installed": os.path.lexists(filename)}


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
    return status()


def remove(runner=subprocess.run):
    """Deactivate and remove only auto-sort's own per-user launcher."""
    filename = target()
    if platform_name() == "macos" and os.path.lexists(filename):
        _launchctl("bootout", filename, runner, allow_failure=True)
    if os.path.lexists(filename):
        os.unlink(filename)
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


def _desktop_quote(value):
    value = str(value)
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


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
