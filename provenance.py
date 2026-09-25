"""Where a file came from, which the operating system already wrote down.

This is the cheapest strong evidence there is, and almost nothing uses it.
Every mainstream browser records the URL a download came from, macOS records
which application wrote the file, and Windows records the security zone and
referrer. None of it requires opening the file, none of it can be wrong in the
way a filename can be wrong, and `from_host = bandcamp.com` settles a question
that no amount of looking at the audio would settle.

It also answers the question that has no other answer: **how** the file
arrived. A PDF that came from Mail is a different thing from the same PDF
downloaded from a bank's website, and the only place that distinction exists
is the quarantine record.

Nothing here is required. On a filesystem with no extended attributes every
function returns nothing and the record is simply thinner.
"""

from __future__ import annotations

import ctypes
import os
import plistlib
import re
import sys

import hosts

from evidence import CERTAIN, STRONG, LIKELY, WEAK

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")

# The flag macOS sets on an iCloud file whose contents are not on this disk.
# Reading one downloads it, which for a sorter pointed at a Documents folder
# means silently pulling gigabytes over somebody's connection.
UF_DATALESS = 0x40000000


# ---------------------------------------------------------------------------
# Extended attributes, which Python exposes on Linux and nowhere else
# ---------------------------------------------------------------------------

_libc = None
if IS_MACOS:
    try:
        _libc = ctypes.CDLL(None, use_errno=True)
        _libc.getxattr.restype = ctypes.c_ssize_t
        _libc.getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                   ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.c_uint32, ctypes.c_int]
    except (OSError, AttributeError):
        _libc = None


def xattr(path, name):
    """One extended attribute as bytes, or None.

    `os.getxattr` exists only on Linux, so macOS goes through libc directly
    rather than through the `xattr` package, which would be a dependency for
    four lines of ctypes.
    """
    if IS_MACOS and _libc is not None:
        encoded = os.fsencode(path)
        key = name.encode("utf-8")
        size = _libc.getxattr(encoded, key, None, 0, 0, 0)
        if size <= 0:
            return None
        buffer = ctypes.create_string_buffer(size)
        got = _libc.getxattr(encoded, key, buffer, size, 0, 0)
        return buffer.raw[:got] if got > 0 else None
    if IS_LINUX and hasattr(os, "getxattr"):
        try:
            return os.getxattr(path, name)
        except (OSError, ValueError):
            return None
    return None


def is_dataless(path):
    """True for a cloud placeholder whose bytes are not on this disk."""
    try:
        status = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    flags = getattr(status, "st_flags", 0)
    if flags & UF_DATALESS:
        return True
    if IS_WINDOWS:
        # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS / _RECALL_ON_OPEN / _OFFLINE
        attributes = getattr(status, "st_file_attributes", 0)
        return bool(attributes & (0x00400000 | 0x00040000 | 0x00001000))
    return False


# ---------------------------------------------------------------------------
# macOS: where-froms and the quarantine record
# ---------------------------------------------------------------------------

_QUARANTINE_AGENTS = {
    "safari": ("Safari", "download"),
    "google chrome": ("Chrome", "download"),
    "chromium": ("Chromium", "download"),
    "firefox": ("Firefox", "download"),
    "microsoft edge": ("Edge", "download"),
    "brave browser": ("Brave", "download"),
    "arc": ("Arc", "download"),
    "opera": ("Opera", "download"),
    "orion": ("Orion", "download"),
    "mail": ("Mail", "email"),
    "microsoft outlook": ("Outlook", "email"),
    "airmail": ("Airmail", "email"),
    "spark": ("Spark", "email"),
    "thunderbird": ("Thunderbird", "email"),
    "messages": ("Messages", "message"),
    "whatsapp": ("WhatsApp", "message"),
    "telegram": ("Telegram", "message"),
    "signal": ("Signal", "message"),
    "slack": ("Slack", "message"),
    "discord": ("Discord", "message"),
    "microsoft teams": ("Teams", "message"),
    "transmission": ("Transmission", "torrent"),
    "qbittorrent": ("qBittorrent", "torrent"),
    "deluge": ("Deluge", "torrent"),
    "folx": ("Folx", "download"),
    "curl": ("curl", "download"),
    "wget": ("wget", "download"),
    "dropbox": ("Dropbox", "cloud"),
    "google drive": ("Google Drive", "cloud"),
    "onedrive": ("OneDrive", "cloud"),
    "finder": ("Finder", "airdrop"),
    "airdrop": ("AirDrop", "airdrop"),
    "sharingd": ("AirDrop", "airdrop"),    # the daemon behind AirDrop
    "bluetooth": ("Bluetooth", "airdrop"),
    "xcode": ("Xcode", "download"),
    "steam": ("Steam", "download"),
    "zoom": ("Zoom", "message"),
}


def _agent_origin(agent):
    lowered = (agent or "").lower()
    for known, (label, origin) in _QUARANTINE_AGENTS.items():
        if known in lowered:
            return label, origin
    return (agent, "download") if agent else (None, None)


def _macos_wherefroms(path):
    raw = xattr(path, "com.apple.metadata:kMDItemWhereFroms")
    if not raw:
        return []
    try:
        value = plistlib.loads(raw)
    except Exception:                      # a malformed plist is not fatal
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    return [str(value)] if value else []


def _macos_quarantine(path):
    """(agent, epoch seconds) from `com.apple.quarantine`, or (None, None).

    The attribute is four semicolon-separated fields: flags, a hexadecimal
    timestamp, the application that wrote the file, and a UUID.
    """
    raw = xattr(path, "com.apple.quarantine")
    if not raw:
        return None, None
    parts = raw.decode("utf-8", "replace").split(";")
    agent = parts[2] if len(parts) > 2 else None
    when = None
    if len(parts) > 1:
        try:
            when = int(parts[1], 16)
        except ValueError:
            when = None
    return (agent or None), when


# ---------------------------------------------------------------------------
# Windows: the Zone.Identifier alternate data stream
# ---------------------------------------------------------------------------

def _windows_zone(path):
    """ZoneId, ReferrerUrl and HostUrl, as written by every browser on NTFS."""
    try:
        with open(path + ":Zone.Identifier", "r",
                  encoding="utf-8", errors="replace") as stream:
            body = stream.read(4096)
    except OSError:
        return {}
    found = {}
    for line in body.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            found[key.strip().lower()] = value.strip()
    return found


# ---------------------------------------------------------------------------
# The path itself, which says as much as any attribute
# ---------------------------------------------------------------------------

_CLOUD_MARKERS = (
    ("dropbox", "Dropbox"), ("google drive", "Google Drive"),
    ("googledrive", "Google Drive"), ("onedrive", "OneDrive"),
    ("com~apple~clouddocs", "iCloud Drive"), ("icloud drive", "iCloud Drive"),
    ("mobile documents", "iCloud Drive"), ("pcloud", "pCloud"),
    ("mega", "MEGA"), ("box sync", "Box"), ("sync.com", "Sync"),
    ("nextcloud", "Nextcloud"), ("owncloud", "ownCloud"),
    ("proton drive", "Proton Drive"), ("creative cloud files", "Adobe"),
)

_PLACE_MARKERS = (
    ("/downloads", "downloads"), ("\\downloads", "downloads"),
    ("/desktop", "desktop"), ("\\desktop", "desktop"),
    ("/documents", "documents"), ("\\documents", "documents"),
    ("/pictures", "pictures"), ("\\pictures", "pictures"),
    ("/movies", "movies"), ("/videos", "movies"), ("\\videos", "movies"),
    ("/music", "music"), ("\\music", "music"),
    ("/.trash", "trash"), ("$recycle.bin", "trash"), ("/trash", "trash"),
    ("/tmp", "temp"), ("\\temp", "temp"), ("/var/folders", "temp"),
    ("/library/caches", "cache"), ("\\appdata\\local\\temp", "temp"),
)


def _windows_drive_kind(path):
    try:
        drive = os.path.splitdrive(os.path.abspath(path))[0]
        if not drive:
            return None
        import winapi
        kind = winapi.load("kernel32").GetDriveTypeW(drive + "\\")
    except (AttributeError, OSError, ValueError):
        return None
    return {2: "removable", 3: "fixed", 4: "network",
            5: "optical", 6: "ramdisk"}.get(kind)


def _place(path):
    """(where this sits, how permanent the volume is)."""
    lowered = os.path.abspath(path).lower().replace(os.sep, "/")
    place = None
    for marker, label in _PLACE_MARKERS:
        if marker.replace("\\", "/") in lowered:
            place = label
            break
    volume = None
    if IS_WINDOWS:
        volume = _windows_drive_kind(path)
    elif IS_MACOS and lowered.startswith("/volumes/"):
        volume = "removable"
    elif IS_LINUX and re.match(r"^/(media|mnt|run/media)/", lowered):
        volume = "removable"
    if volume in (None, "fixed"):
        try:
            if os.stat(path).st_dev != os.stat(os.path.expanduser("~")).st_dev:
                volume = volume or "removable"
        except OSError:
            pass
    cloud = None
    for marker, label in _CLOUD_MARKERS:
        if marker in lowered:
            cloud = label
            break
    return place, volume, cloud


# ---------------------------------------------------------------------------

_HOST = re.compile(r"^[a-z][a-z0-9+.-]*://(?:[^/@]*@)?([^/:?#]+)", re.I)


def _xattrs_supported(path):
    """Whether this path's filesystem can carry a user xattr at all.

    Separates "nothing to read" from "nothing to read *here*". A FAT32 drive
    or a filesystem mounted without xattr support explains a missing origin
    url on its own, which is the case the module docstring already covers.
    A home directory that supports xattrs fine but simply has none on this
    file does not explain itself, and on a Flatpak-installed or
    privacy-focused browser that gap is the normal case, not the exception:
    verified on a real download here (SteamOS, Zen browser, Flatpak) where
    the file landed with zero xattrs even though `setfattr`/`getfattr` work
    on the same filesystem.
    """
    if not (IS_LINUX and hasattr(os, "listxattr")):
        return False
    try:
        os.listxattr(path)
        return True
    except OSError:
        return False


def read(path, out):
    """Add every provenance fact this platform can produce to `out`.

    `out` is anything with `.add(detector, name, value, confidence)` — the
    same shape `names.Found` has, so the two readers compose without either
    knowing about the record.
    """
    place, volume, cloud = _place(path)
    if place:
        out.add("path", "place", place, CERTAIN)
    if volume:
        out.add("path", "volume", volume, STRONG)
    if cloud:
        out.add("path", "cloud", cloud, STRONG)
        out.add("path", "origin", "cloud", LIKELY)
    if is_dataless(path):
        out.add("path", "dataless", True, CERTAIN)

    urls = []
    agent = origin = None

    if IS_MACOS:
        urls = _macos_wherefroms(path)
        raw_agent, when = _macos_quarantine(path)
        agent, origin = _agent_origin(raw_agent)
        if when:
            import datetime
            stamp = datetime.datetime.fromtimestamp(when)
            out.add("quarantine", "downloaded",
                    stamp.strftime("%Y-%m-%d %H:%M:%S"), STRONG)
        if raw_agent:
            out.add("quarantine", "quarantined", True, CERTAIN)
    elif IS_WINDOWS:
        zone = _windows_zone(path)
        for key in ("hosturl", "referrerurl"):
            if zone.get(key):
                urls.append(zone[key])
        zone_id = zone.get("zoneid")
        if zone_id:
            out.add("zone", "quarantined", zone_id in ("3", "4"), CERTAIN)
            origin = {"3": "download", "4": "download",
                      "1": "network"}.get(zone_id)
    else:
        for name in ("user.xdg.origin.url", "user.xdg.referrer.url"):
            raw = xattr(path, name)
            if raw:
                urls.append(raw.decode("utf-8", "replace"))

    if urls:
        out.add("wherefroms", "from_url", urls[0], STRONG)
        if len(urls) > 1:
            out.add("wherefroms", "referrer", urls[1], STRONG)
        host = _HOST.match(urls[0])
        if host:
            hostname = hosts.strip_decoration(host.group(1))
            out.add("wherefroms", "from_host", hostname, STRONG)
            # The service, reduced from the hostname rather than looked up.
            # This is the fact worth grouping by: it is exact, it costs
            # nothing, and it works for a site nobody has ever seen before.
            service = hosts.source(host.group(1))
            if service:
                out.add("wherefroms", "source", service, STRONG)
        origin = origin or "download"
    if agent:
        out.add("quarantine", "from_app", agent, STRONG)
    if origin:
        out.add("quarantine" if agent else "path", "origin", origin,
                STRONG if agent else LIKELY)
    elif place == "downloads":
        # No attribute survived — a copy, a restore, an old file — but the
        # folder is still a statement of intent.
        out.add("path", "origin", "download", WEAK)
        if IS_LINUX and _xattrs_supported(path):
            # The filesystem can carry the attribute; this browser just
            # never wrote it. Silence here would read as "checked, and
            # this file has no known origin" when what actually happened
            # is "the strongest evidence this module has was never
            # available on this desktop."
            out.note("no user.xdg.origin.url on a file in Downloads: this "
                     "browser (or its Flatpak sandbox) is not writing the "
                     "origin attribute, so this is a folder guess, not the "
                     "browser's own record")
