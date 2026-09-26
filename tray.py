"""A tray icon with a menu on macOS, Windows and Linux, from the standard library.

    import tray

    look = tray.Look("Backups", [
        tray.Entry("open", "Open the folder"),
        tray.Entry("pause", "Pause", paused_label="Resume"),
        None,                                    # a separator
        tray.Entry("quit", "Quit"),
    ], click="open")
    icon = tray.create({"open": ..., "pause": ..., "quit": ...}, look)
    while running:
        icon.pump(0.25)                          # answer clicks, then return
    icon.close()

Nothing is installed and nothing runs on a thread: the owner calls `pump`
from its own loop, and clicks run its callbacks from inside that call. A
left click runs the `click` action; the menu is a right click away.
`set_paused(True)` is the item's one second state: each entry that has a
`paused_label` shows it, and the icon says so where the platform can.

`create` never raises. Where there is no tray to be had -- no desktop, no
session bus, a failure in a backend -- it returns an object with the same
methods that does nothing and says why in `reason`, because an icon must
never be the thing that stops the program it belongs to.

auto-sort's own menu is `AUTO_SORT`, and what `create` uses by default.
"""

from __future__ import annotations

import os
import re
import sys


class Entry(object):
    """One line of the menu: the action it runs, and what it says."""

    def __init__(self, action, label, paused_label=None):
        self.action = action
        self.label = label
        self.paused_label = paused_label

    def text(self, paused):
        return self.paused_label if paused and self.paused_label \
            else self.label


class Look(object):
    """What the icon is called, what its menu holds, and what it looks like.

    `entries` are `Entry` objects, or None for a separator. `click` is the
    action a left click runs. `status` and `status_paused` are the line
    under the title in a Linux tooltip. `symbol` is an SF Symbol name for
    the macOS menu bar; `icon` a freedesktop icon name for Linux, with
    `pixmaps` -- `(width, height, big-endian ARGB bytes)` -- drawn instead by
    a host whose theme has no such icon. Windows shows the shell's icon for
    a plain file: loading an icon of one's own is Win32 surface that nobody
    has seen run.
    """

    MAX_ENTRIES = 32

    def __init__(self, title, entries, click=None, status="",
                 status_paused="Paused", symbol="doc", icon="folder",
                 pixmaps=None):
        entries = list(entries)
        if not any(entries):
            raise ValueError("a tray menu needs at least one entry")
        if len(entries) > self.MAX_ENTRIES:
            raise ValueError("at most %d menu entries" % self.MAX_ENTRIES)
        self.title = title
        self.entries = entries
        self.click = click
        self.status = status
        self.status_paused = status_paused
        self.symbol = symbol
        self.icon = icon
        self.pixmaps = pixmaps

    def tooltip(self, paused):
        """One line, for the platforms whose tooltip is one line."""
        return "%s (%s)" % (self.title, self.status_paused.lower()) \
            if paused and self.status_paused else self.title

    def slug(self):
        return re.sub(r"[^A-Za-z0-9]+", "-", self.title).strip("-").lower() \
            or "tray"


class UnavailableTray(object):
    available = False

    def __init__(self, reason):
        self.reason = reason

    def set_paused(self, _paused):
        pass

    def pump(self, _seconds):
        pass

    def close(self):
        pass


def report(item):
    """What a diagnostics report says about a tray, whichever kind it is.

    Never a file name or a path: this is pasted into public issues.
    """
    own = getattr(item, "report", None)
    if callable(own):
        return own()
    backend = {"darwin": "macOS status item"}.get(
        sys.platform, "Windows notification icon"
        if sys.platform.startswith("win") else "none")
    return {"backend": backend if getattr(item, "available", False)
            else "none",
            "available": bool(getattr(item, "available", False)),
            "reason": getattr(item, "reason", "")}


# Restart is in the menu because a daemon cannot reload its own code, so
# every change to auto-sort itself needs one -- and asking for a terminal is
# the wrong answer for somebody whose whole interface is this icon.
AUTO_SORT = Look("auto-sort", [
    Entry("open_log", "Open log"),
    Entry("toggle_pause", "Pause sorting", paused_label="Resume sorting"),
    Entry("sort_now", "Sort now"),
    None,
    Entry("restart", "Restart auto-sort"),
    Entry("quit", "Quit auto-sort"),
], click="open_log", status="Watching your folders", status_paused="Paused",
    symbol="doc", icon="folder")


def create(actions, look=None):
    """Return a best-effort native tray, never an object that can stop sorting.

    `actions` maps each entry's action name to a function of no arguments.
    """
    look = look or AUTO_SORT
    try:
        if sys.platform == "darwin":
            return _mac_tray(actions, look)
        if sys.platform.startswith("win"):
            return _windows_tray(actions, look)
        return _linux_tray(actions, look=look)
    except Exception as error:
        return UnavailableTray("native tray unavailable: %s" % error)


# ---------------------------------------------------------------------------
# macOS, through the Objective-C runtime directly
# ---------------------------------------------------------------------------
#
# Not PyObjC. That was the first implementation and it cost more than it was
# worth: forty-odd megabytes for one icon, and -- worse -- it cannot be
# installed at all on a Homebrew, Debian or Fedora Python, because those are
# marked externally managed under PEP 668 and refuse `pip install`. The menu
# bar icon was therefore unreachable on the machines most likely to run this.
#
# The Objective-C runtime is a plain C library with an ABI that has not moved
# in twenty years, and `ctypes` speaks C. So macOS now does what Windows
# already did: talks to the system directly, with nothing installed. auto-sort
# has no third-party dependency on any platform again, which is the claim the
# whole project rests on.

# Objective-C registers class names process-wide, so the target class can be
# built exactly once. The methods therefore cannot close over one tray's
# callbacks; they go through whichever tray is currently active instead,
# and name menu entries by position -- `entry0:` to `entry31:` -- rather
# than by action. There is only ever one tray.
_MAC_TARGET = None
_ACTIVE = None


class _Runtime(object):                                      # pragma: no cover
    """Just enough Objective-C to own a status item."""

    def __init__(self):
        import ctypes
        import ctypes.util
        self.ctypes = ctypes
        self.objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        for framework in ("AppKit", "Foundation"):
            ctypes.cdll.LoadLibrary(ctypes.util.find_library(framework))
        void_p = ctypes.c_void_p
        for name, restype, argtypes in (
                ("objc_getClass", void_p, [ctypes.c_char_p]),
                ("sel_registerName", void_p, [ctypes.c_char_p]),
                ("objc_allocateClassPair", void_p,
                 [void_p, ctypes.c_char_p, ctypes.c_size_t]),
                ("objc_registerClassPair", None, [void_p]),
                ("class_addMethod", ctypes.c_bool,
                 [void_p, void_p, void_p, ctypes.c_char_p])):
            function = getattr(self.objc, name)
            function.restype = restype
            function.argtypes = argtypes
        self.libc = ctypes.CDLL(None)
        self._kept = []          # trampolines must outlive the class

    def cls(self, name):
        found = self.objc.objc_getClass(name.encode())
        if not found:
            raise RuntimeError("no Objective-C class %r" % name)
        return found

    def sel(self, name):
        return self.objc.sel_registerName(name.encode())

    def send(self, restype, receiver, selector, *args):
        """One `objc_msgSend`, declared for exactly this signature.

        Re-declaring per call is not an optimisation problem worth solving:
        `objc_msgSend` is variadic in C and calling it through a single
        ctypes prototype passes the wrong registers on arm64.
        """
        function = self.libc.objc_msgSend
        function.restype = restype
        function.argtypes = [self.ctypes.c_void_p, self.ctypes.c_void_p] + \
            [kind for kind, _value in args]
        return function(receiver, self.sel(selector),
                        *[value for _kind, value in args])

    def string(self, text):
        return self.send(self.ctypes.c_void_p, self.cls("NSString"),
                         "stringWithUTF8String:",
                         (self.ctypes.c_char_p, text.encode("utf-8")))

    def define(self, name, methods):
        """Register a class whose selectors call Python functions."""
        ctypes = self.ctypes
        created = self.objc.objc_allocateClassPair(self.cls("NSObject"),
                                                   name.encode(), 0)
        if not created:
            raise RuntimeError("could not create Objective-C class %r" % name)
        prototype = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_void_p)
        for selector, handler in methods.items():
            def make(callback):
                def method(_self, _cmd, _sender):
                    try:
                        callback()
                    except Exception:            # noqa: BLE001
                        pass                     # never unwind into ObjC
                return prototype(method)
            trampoline = make(handler)
            self._kept.append(trampoline)
            self.objc.class_addMethod(
                created, self.sel(selector),
                ctypes.cast(trampoline, ctypes.c_void_p), b"v@:@")
        self.objc.objc_registerClassPair(created)
        return created


def _dispatch(position):                                     # pragma: no cover
    """Route a menu selector to the tray that is currently up."""
    tray = _ACTIVE
    if tray is None:
        return
    if position is None:
        tray._clicked()
        return
    tray._run_entry(position)


def _mac_tray(actions, look=None):                           # pragma: no cover
    """A status item built on the Objective-C runtime, with no dependencies."""
    import ctypes

    runtime = _Runtime()
    void_p = ctypes.c_void_p
    look = look or AUTO_SORT

    class MacTray(object):
        available = True
        reason = ""

        def __init__(self):
            self.runtime = runtime
            self.actions = actions
            self.look = look
            self.menu_showing = False
            application = runtime.send(void_p, runtime.cls("NSApplication"),
                                       "sharedApplication")
            # Accessory: a menu bar presence with no Dock icon.
            runtime.send(None, application, "setActivationPolicy:",
                         (ctypes.c_long, 1))
            # Without this the application never becomes ready to receive
            # events. The item is still drawn -- the system does that -- so
            # the icon appears and does nothing, which is worse than absent.
            runtime.send(None, application, "finishLaunching")
            self.application = application

            self.status_bar = runtime.send(void_p, runtime.cls("NSStatusBar"),
                                           "systemStatusBar")
            self.status_item = runtime.send(
                void_p, self.status_bar, "statusItemWithLength:",
                (ctypes.c_double, -1.0))       # NSVariableStatusItemLength
            self.button = runtime.send(void_p, self.status_item, "button")

            self.target = self._make_target()
            instance = runtime.send(void_p, runtime.send(
                void_p, self.target, "alloc"), "init")
            self.instance = instance
            runtime.send(None, self.button, "setTarget:", (void_p, instance))
            runtime.send(None, self.button, "setAction:",
                         (void_p, runtime.sel("clicked:")))
            # Left and right mouse up, so one item can do both jobs.
            runtime.send(None, self.button, "sendActionOn:",
                         (ctypes.c_ulonglong, (1 << 2) | (1 << 4)))
            self._set_icon()
            self._build_menu()

        def _make_target(self):
            global _MAC_TARGET
            if _MAC_TARGET is not None:
                return _MAC_TARGET
            methods = {"clicked:": lambda: _dispatch(None)}
            for position in range(Look.MAX_ENTRIES):
                methods["entry%d:" % position] = (
                    lambda position=position: _dispatch(position))
            _MAC_TARGET = runtime.define("StdlibTrayStatusTarget", methods)
            return _MAC_TARGET

        def _set_icon(self):
            image = runtime.send(
                void_p, runtime.cls("NSImage"),
                "imageWithSystemSymbolName:accessibilityDescription:",
                (void_p, runtime.string(look.symbol or "")),
                (void_p, runtime.string(look.title)))
            if image:
                runtime.send(None, image, "setTemplate:", (ctypes.c_bool, True))
                runtime.send(None, self.button, "setImage:", (void_p, image))
            else:
                runtime.send(None, self.button, "setTitle:",
                             (void_p, runtime.string(look.title[:2])))

        def _build_menu(self):
            self.menu = runtime.send(void_p, runtime.send(
                void_p, runtime.cls("NSMenu"), "alloc"), "init")
            self.pause_item = None
            self.items = {}
            for position, entry in enumerate(look.entries):
                if entry is None:
                    separator = runtime.send(void_p,
                                             runtime.cls("NSMenuItem"),
                                             "separatorItem")
                    runtime.send(None, self.menu, "addItem:",
                                 (void_p, separator))
                    continue
                item = runtime.send(
                    void_p, runtime.send(void_p, runtime.cls("NSMenuItem"),
                                         "alloc"),
                    "initWithTitle:action:keyEquivalent:",
                    (void_p, runtime.string(entry.label)),
                    (void_p, runtime.sel("entry%d:" % position)),
                    (void_p, runtime.string("")))
                runtime.send(None, item, "setTarget:", (void_p, self.instance))
                runtime.send(None, self.menu, "addItem:", (void_p, item))
                self.items[position] = item
                if entry.paused_label and self.pause_item is None:
                    self.pause_item = item

        def _run_entry(self, position):
            entries = self.look.entries
            entry = entries[position] if position < len(entries) else None
            callback = self.actions.get(entry.action) if entry else None
            if callback:
                callback()

        def _clicked(self):
            """Left click runs the click action; right click raises the menu.

            A menu attached permanently swallows every click and the left
            click stops doing anything, so it is attached for the length of
            one right click and taken away again. With no click action there
            is nothing to lose, and every click raises the menu.
            """
            event = runtime.send(void_p, self.application, "currentEvent")
            secondary = False
            if event:
                kind = runtime.send(ctypes.c_ulonglong, event, "type")
                modifiers = runtime.send(ctypes.c_ulonglong, event,
                                         "modifierFlags")
                secondary = (kind == 4                      # RightMouseUp
                             or bool(modifiers & (1 << 18)))  # Control
            click = self.actions.get(self.look.click) \
                if self.look.click else None
            if secondary or click is None:
                runtime.send(None, self.status_item, "setMenu:",
                             (void_p, self.menu))
                runtime.send(None, self.button, "performClick:",
                             (void_p, None))
                runtime.send(None, self.status_item, "setMenu:",
                             (void_p, None))
            else:
                click()

        def set_paused(self, paused):
            for position, item in self.items.items():
                entry = self.look.entries[position]
                if entry.paused_label:
                    runtime.send(None, item, "setTitle:",
                                 (void_p, runtime.string(entry.text(paused))))

        def pump(self, seconds):
            """Dequeue and dispatch what the window server has sent.

            Running the run loop is not enough: status item clicks land in
            the application's own event queue and only
            `nextEventMatchingMask:` takes them out of it.
            """
            NSDate = runtime.cls("NSDate")
            until = runtime.send(void_p, NSDate,
                                 "dateWithTimeIntervalSinceNow:",
                                 (ctypes.c_double, max(0.001, seconds)))
            immediately = runtime.send(void_p, NSDate, "date")
            mode = runtime.string("kCFRunLoopDefaultMode")
            deadline = until
            while True:
                event = runtime.send(
                    void_p, self.application,
                    "nextEventMatchingMask:untilDate:inMode:dequeue:",
                    (ctypes.c_ulonglong, 0xFFFFFFFFFFFFFFFF),
                    (void_p, deadline), (void_p, mode),
                    (ctypes.c_bool, True))
                if not event:
                    return
                runtime.send(None, self.application, "sendEvent:",
                             (void_p, event))
                deadline = immediately

        def close(self):
            global _ACTIVE
            runtime.send(None, self.status_bar, "removeStatusItem:",
                         (void_p, self.status_item))
            if _ACTIVE is self:
                _ACTIVE = None

    global _ACTIVE
    _ACTIVE = MacTray()
    return _ACTIVE


def _windows_tray(actions, look=None):                       # pragma: no cover
    """Shell_NotifyIcon implementation using only ctypes and a hidden window.

    The callback lives on the daemon thread: `PollingDaemon` calls `pump` at
    least four times a second, so this never needs another thread or a message
    loop that could hold up the filesystem work.

    Every Win32 call goes through `winapi`, which declares its signature:
    called bare, ctypes cut 64-bit handles in half, and this tray could
    not have appeared on any 64-bit Windows. Written from the Win32
    documentation and tested on macOS and Linux as data only -- see
    README's platform notes for what that does and does not promise.
    """
    import ctypes
    from ctypes import wintypes

    import winapi

    look = look or AUTO_SORT
    user32 = winapi.load("user32")
    shell32 = winapi.load("shell32")
    kernel32 = winapi.load("kernel32")

    WM_NULL = 0x0000
    WM_COMMAND = 0x0111
    WM_DESTROY = 0x0002
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONUP = 0x0205
    WM_APP = 0x8000
    CALLBACK = WM_APP + 71
    NIM_ADD, NIM_MODIFY, NIM_DELETE = 0x00000000, 0x00000001, 0x00000002
    NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x00000001, 0x00000002, 0x00000004
    TPM_RIGHTBUTTON = 0x0002
    MF_SEPARATOR = 0x0800
    PM_REMOVE = 0x0001
    # A menu entry's command id is its position plus one; zero is "none".
    IDI_APPLICATION = 32512
    SHGFI_ICON, SHGFI_SMALLICON, SHGFI_USEFILEATTRIBUTES = 0x100, 0x1, 0x10
    FILE_ATTRIBUTE_NORMAL = 0x80

    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                 wintypes.UINT, wintypes.WPARAM,
                                 wintypes.LPARAM)
    NOTIFYICONDATAW, WNDCLASSW, SHFILEINFOW = _windows_structures(WNDPROC)

    class WindowsTray(object):
        available = True
        reason = ""

        def __init__(self):
            self.instance = kernel32.GetModuleHandleW(None)
            self.class_name = "%s-tray-%d" % (look.slug(), id(self))
            self.paused = False
            self.closed = False
            # Explorer broadcasts this when the taskbar comes back after a
            # crash or restart, and every icon it had is gone. Not answered,
            # the icon stays gone until auto-sort restarts.
            self.taskbar_created = user32.RegisterWindowMessageW(
                "TaskbarCreated")
            self.proc = WNDPROC(self._window_proc)
            window_class = WNDCLASSW()
            window_class.lpfnWndProc = self.proc
            window_class.hInstance = self.instance
            window_class.lpszClassName = self.class_name
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise OSError("could not register the tray window class "
                              "(error %d)" % winapi.last_error())
            self.window = user32.CreateWindowExW(
                0, self.class_name, self.class_name, 0, 0, 0, 0, 0,
                None, None, self.instance, None)
            if not self.window:
                error = winapi.last_error()
                user32.UnregisterClassW(self.class_name, self.instance)
                raise OSError("could not create the tray window "
                              "(error %d)" % error)
            info = SHFILEINFOW()
            shell32.SHGetFileInfoW("", FILE_ATTRIBUTE_NORMAL,
                                   ctypes.byref(info), ctypes.sizeof(info),
                                   SHGFI_ICON | SHGFI_SMALLICON |
                                   SHGFI_USEFILEATTRIBUTES)
            self.icon_from_shell = bool(info.hIcon)
            self.icon = info.hIcon or user32.LoadIconW(None, IDI_APPLICATION)
            self.icon_data = NOTIFYICONDATAW()
            # The whole structure, as Vista and later define it: a size that
            # matches none of the versions Windows knows can be refused.
            self.icon_data.cbSize = ctypes.sizeof(self.icon_data)
            self.icon_data.hWnd = self.window
            self.icon_data.uID = 1
            self.icon_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            self.icon_data.uCallbackMessage = CALLBACK
            self.icon_data.hIcon = self.icon
            self.icon_data.szTip = look.tooltip(False)[:127]
            if not shell32.Shell_NotifyIconW(NIM_ADD,
                                             ctypes.byref(self.icon_data)):
                error = winapi.last_error()
                user32.DestroyWindow(self.window)
                user32.UnregisterClassW(self.class_name, self.instance)
                raise OSError("could not add the notification icon "
                              "(error %d)" % error)

        def _window_proc(self, window, message, wparam, lparam):
            if message == CALLBACK and lparam == WM_LBUTTONUP:
                click = actions.get(look.click) if look.click else None
                if click:
                    click()
                else:
                    self._show_menu()
                return 0
            if message == CALLBACK and lparam == WM_RBUTTONUP:
                self._show_menu()
                return 0
            if message == WM_COMMAND:
                self._run_action(wparam & 0xffff)
                return 0
            if self.taskbar_created and message == self.taskbar_created:
                shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.icon_data))
                return 0
            if message == WM_DESTROY:
                return 0
            return user32.DefWindowProcW(window, message, wparam, lparam)

        def _show_menu(self):
            menu = user32.CreatePopupMenu()
            try:
                for position, entry in enumerate(look.entries):
                    if entry is None:
                        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                    else:
                        user32.AppendMenuW(menu, 0, position + 1,
                                           entry.text(self.paused))
                point = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(point))
                user32.SetForegroundWindow(self.window)
                user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON, point.x,
                                      point.y, 0, self.window, None)
                # Documented by Microsoft for notification-area menus: without
                # a message after it, the menu does not close when the click
                # lands elsewhere, and the second right-click shows nothing.
                user32.PostMessageW(self.window, WM_NULL, 0, 0)
            finally:
                user32.DestroyMenu(menu)

        def _run_action(self, item):
            position = item - 1
            entry = look.entries[position] \
                if 0 <= position < len(look.entries) else None
            callback = actions.get(entry.action) if entry else None
            if callback:
                callback()

        def set_paused(self, paused):
            paused = bool(paused)
            if paused == self.paused:
                return
            self.paused = paused
            self.icon_data.uFlags = NIF_TIP
            self.icon_data.szTip = look.tooltip(paused)[:127]
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.icon_data))
            self.icon_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP

        def pump(self, _seconds):
            message = wintypes.MSG()
            while user32.PeekMessageW(ctypes.byref(message), None, 0, 0,
                                      PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))

        def close(self):
            if self.closed:
                return
            self.closed = True
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.icon_data))
            if self.icon_from_shell:
                user32.DestroyIcon(self.icon)
            user32.DestroyWindow(self.window)
            user32.UnregisterClassW(self.class_name, self.instance)

    return WindowsTray()


def _windows_structures(window_procedure):
    """The Win32 structures the tray fills in, laid out as documented.

    Separate from `_windows_tray` so the layout can be checked on any
    machine: only the window procedure's type needs Windows.
    """
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT),          # a union with uTimeout
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", GUID),
            ("hBalloonIcon", wintypes.HICON),
        ]

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", window_procedure),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [("hIcon", wintypes.HICON), ("iIcon", ctypes.c_int),
                    ("dwAttributes", wintypes.DWORD),
                    ("szDisplayName", wintypes.WCHAR * 260),
                    ("szTypeName", wintypes.WCHAR * 80)]

    return NOTIFYICONDATAW, WNDCLASSW, SHFILEINFOW


# ---------------------------------------------------------------------------
# Linux, as a StatusNotifierItem on the session bus
# ---------------------------------------------------------------------------
#
# KDE, and anything that hosts the same protocol, draws tray icons for
# objects on the session bus: an `org.kde.StatusNotifierItem` describing the
# icon, registered with `org.kde.StatusNotifierWatcher`, and a
# `com.canonical.dbusmenu` object for its menu. The desktop calls in; this
# answers. `dbuswire` speaks the protocol with the standard library, so the
# Linux icon costs what the other two do: nothing installed.
#
# Registration is by a well-known name of our own, which every host
# understands. The watcher forgets every item when it restarts -- kded, not
# plasmashell, owns it on Plasma 6 -- and says nothing to them, so the item
# listens for the watcher's name changing hands and registers again.

SNI_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
SNI = "org.kde.StatusNotifierItem"
# The same protocol under freedesktop's names. swaybar runs a watcher under
# each, and reads an item registered with this one through this interface
# name; the item answers to both, so it does not matter which one is there.
SNI_FREEDESKTOP = "org.freedesktop.StatusNotifierItem"
MENU = "com.canonical.dbusmenu"
PROPERTIES = "org.freedesktop.DBus.Properties"
WATCHER = ("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
           "org.kde.StatusNotifierWatcher")
WATCHER_FREEDESKTOP = ("org.freedesktop.StatusNotifierWatcher",
                       "/StatusNotifierWatcher",
                       "org.freedesktop.StatusNotifierWatcher")
WATCHERS = (WATCHER, WATCHER_FREEDESKTOP)

# auto-sort's icon: a name from the freedesktop icon naming specification,
# so every icon theme has one; the same icon the applications-menu entry
# uses. Another program's is its `Look.icon`.
ICON = "folder"
PAUSED_OVERLAY = "media-playback-pause"

# The same icons as pixels, for a host that does not look names up or whose
# icon theme has no "folder": the specification's fallback, and without it
# such a host draws an empty square. Sizes the common panels ask for.
PIXMAP_SIZES = (16, 22, 32, 48)


def _folder_pixmap(size):
    """`(width, height, ARGB32 bytes)`: a plain folder, drawn by rule.

    Big-endian ARGB, as the specification asks. Nothing is loaded from
    disk, so there is nothing to ship and nothing to be missing.
    """
    body = (0xFF, 0x4A, 0x8F, 0xD9)          # alpha, red, green, blue
    edge = (0xFF, 0x2F, 0x6D, 0xB5)
    tab = (0xFF, 0x6E, 0xA8, 0xE8)
    clear = (0x00, 0x00, 0x00, 0x00)
    top = size * 5 // 16                     # where the body starts
    tab_right = size * 7 // 16
    margin = max(1, size // 16)
    out = bytearray()
    for y in range(size):
        for x in range(size):
            inside_x = margin <= x < size - margin
            if not inside_x or y < margin * 3 or y >= size - margin * 2:
                colour = clear
            elif y < top:
                colour = tab if x < tab_right else clear
            elif (x in (margin, size - margin - 1) or y == top
                  or y == size - margin * 2 - 1):
                colour = edge
            else:
                colour = body
            out.extend(colour)
    return size, size, bytes(out)


def _pause_pixmap(size):
    """Two bars in the lower right, on nothing: the paused overlay."""
    bar = (0xFF, 0xF2, 0xF2, 0xF2)
    back = (0xFF, 0x33, 0x33, 0x33)
    clear = (0x00, 0x00, 0x00, 0x00)
    badge = max(6, size // 2)
    left = size - badge
    out = bytearray()
    for y in range(size):
        for x in range(size):
            bx, by = x - left, y - left
            if bx < 0 or by < 0:
                out.extend(clear)
                continue
            inner = badge // 5
            in_bar = inner <= by < badge - inner and (
                inner <= bx < inner * 2 or badge - inner * 2 <= bx
                < badge - inner)
            out.extend(bar if in_bar else back)
    return size, size, bytes(out)


# Menu item ids are an entry's position plus one. Zero is the root, by the
# protocol.


def _linux_tray(actions, connect=None, look=None):
    import dbuswire

    if connect is None:
        connect = dbuswire.Connection
    connection = connect()
    try:
        return LinuxTray(actions, connection, dbuswire, look)
    except Exception:
        connection.close()
        raise


class LinuxTray(object):
    """A tray icon on any desktop that hosts StatusNotifierItems.

    `available` says whether a host was there to show it when it started.
    Where none was -- a desktop that has no tray protocol, or a login where
    the program beat the desktop to it -- the item still listens, and
    appears by itself if a watcher turns up later.
    """

    def __init__(self, actions, connection, dbuswire, look=None):
        self.actions = actions
        self.look = look or AUTO_SORT
        self.bus = connection
        self.dbus = dbuswire
        self.paused = False
        self.revision = 1
        self.name = "org.kde.StatusNotifierItem-%d-1" % os.getpid()
        self.registered = False
        self.watcher = None
        # What the host has asked of the item, by method: the difference
        # between "no icon" and "an icon whose menu nothing reads" in a
        # report from a desktop nobody here has used. See `report`.
        self.asked = {}
        variant = dbuswire.Variant

        item_methods = {
            "Activate": self._activate,
            "SecondaryActivate": lambda _message: ("", []),
            # A host that asks the item to draw its own menu, rather than
            # reading `Menu`: there is no toolkit here to draw one with.
            # Counted, so a report can say that is what this desktop does.
            "ContextMenu": lambda _message: ("", []),
            "Scroll": lambda _message: ("", []),
        }
        for interface in (SNI, SNI_FREEDESKTOP):
            connection.export(SNI_PATH, interface, self._counting(
                item_methods))
        connection.export(SNI_PATH, PROPERTIES, self._counting({
            "Get": lambda message: ("v", [self._item_properties()[
                message.body[1]]]),
            "GetAll": lambda message: ("a{sv}", [
                self._item_properties()
                if message.body[0] in (SNI, SNI_FREEDESKTOP) else {}]),
        }))
        connection.export(MENU_PATH, MENU, self._counting({
            "GetLayout": self._get_layout,
            "GetGroupProperties": self._get_group_properties,
            "GetProperty": lambda message: ("v", [self._menu_items()[
                message.body[0]][message.body[1]]]),
            "Event": self._event,
            "EventGroup": self._event_group,
            "AboutToShow": lambda _message: ("b", [False]),
            "AboutToShowGroup": lambda _message: ("aiai", [[], []]),
        }))
        connection.export(MENU_PATH, PROPERTIES, {
            "Get": lambda message: ("v", [self._menu_properties()[
                message.body[1]]]),
            "GetAll": lambda message: ("a{sv}", [
                self._menu_properties() if message.body[0] == MENU else {}]),
        })
        for path, interfaces in ((SNI_PATH, (SNI, SNI_FREEDESKTOP,
                                             PROPERTIES)),
                                 (MENU_PATH, (MENU, PROPERTIES))):
            connection.export(path, "org.freedesktop.DBus.Introspectable", {
                "Introspect": lambda _message, interfaces=interfaces: (
                    "s", [_introspection(interfaces)])})
            connection.export(path, "org.freedesktop.DBus.Peer", {
                "Ping": lambda _message: ("", [])})
        self._variant = variant

        # DO_NOT_QUEUE: this pid's name is ours or nobody's.
        connection.call(*dbuswire.BUS, member="RequestName", signature="su",
                        body=[self.name, 4])
        for watcher in WATCHERS:
            connection.add_match(
                "type='signal',sender='org.freedesktop.DBus',"
                "interface='org.freedesktop.DBus',member='NameOwnerChanged',"
                "arg0='%s'" % watcher[0],
                lambda message, name=watcher[0]:
                message.member == "NameOwnerChanged"
                and message.body and message.body[0] == name,
                self._watcher_changed)
        self.available = self._register()
        self.reason = "" if self.available else (
            "no StatusNotifier host on this desktop yet; the icon will "
            "appear if one starts")

    # -- registration -------------------------------------------------------

    def _register(self):
        """Register with whichever watcher is there, KDE's name first."""
        for watcher in WATCHERS:
            try:
                self.bus.call(*watcher, member="RegisterStatusNotifierItem",
                              signature="s", body=[self.name])
            except self.dbus.DBusError:
                continue
            self.registered = True
            self.watcher = watcher[0]
            return True
        self.registered = False
        self.watcher = None
        return False

    def _watcher_changed(self, message):
        name, _old, new = message.body[:3]
        if new:
            self._register()
        elif name == self.watcher:
            # The one we were registered with has gone; another may still
            # be there to take us.
            self._register()

    def _counting(self, handlers):
        def counted(member, handler):
            def call(message):
                self.asked[member] = self.asked.get(member, 0) + 1
                return handler(message)
            return call
        return dict((member, counted(member, handler))
                    for member, handler in handlers.items())

    def report(self):
        """What a diagnostics report says about the tray. No file names."""
        return {"backend": "StatusNotifierItem",
                "available": bool(self.available),
                "registered": self.registered,
                "watcher": self.watcher or "",
                "host_asked": dict(sorted(self.asked.items()))}

    # -- the item -----------------------------------------------------------

    def _item_properties(self):
        variant = self._variant
        look = self.look
        state = look.status_paused if self.paused else look.status
        icon = look.icon or ""
        pixmaps = _FOLDER_PIXMAPS if look.pixmaps is None else look.pixmaps
        return {
            "Category": variant("s", "ApplicationStatus"),
            "Id": variant("s", look.slug()),
            "Title": variant("s", look.title),
            "Status": variant("s", "Active"),
            "WindowId": variant("i", 0),
            "IconName": variant("s", icon),
            "IconThemePath": variant("s", ""),
            "IconPixmap": variant("a(iiay)", pixmaps),
            "OverlayIconName": variant("s", PAUSED_OVERLAY
                                       if self.paused else ""),
            "OverlayIconPixmap": variant("a(iiay)", _PAUSE_PIXMAPS
                                         if self.paused else []),
            "AttentionIconName": variant("s", ""),
            "AttentionIconPixmap": variant("a(iiay)", []),
            "AttentionMovieName": variant("s", ""),
            "ToolTip": variant("(sa(iiay)ss)",
                               (icon, [], look.title, state or "")),
            # With no click action, a left click shows the menu too.
            "ItemIsMenu": variant("b", not look.click),
            "Menu": variant("o", MENU_PATH),
        }

    def _activate(self, _message):
        # A left click runs the click action, as it does on a Mac; the menu
        # is a right click away.
        self._run(self.look.click)
        return "", []

    # -- the menu -----------------------------------------------------------

    def _menu_properties(self):
        variant = self._variant
        return {"Version": variant("u", 3),
                "TextDirection": variant("s", "ltr"),
                "Status": variant("s", "normal"),
                "IconThemePath": variant("as", [])}

    def _menu_items(self):
        variant = self._variant
        items = {0: {"children-display": variant("s", "submenu")}}
        for position, entry in enumerate(self.look.entries):
            number = position + 1
            if entry is None:
                items[number] = {"type": variant("s", "separator")}
            else:
                items[number] = {"label": variant("s",
                                                  entry.text(self.paused)),
                                 "enabled": variant("b", True),
                                 "visible": variant("b", True)}
        return items

    def _node(self, number, names, depth):
        properties = self._menu_items()[number]
        if names:
            properties = dict((key, value) for key, value
                              in properties.items() if key in names)
        children = []
        if number == 0 and depth != 0:
            children = [self._variant("(ia{sv}av)",
                                      self._node(child, names, depth - 1))
                        for child in range(1, len(self.look.entries) + 1)]
        return (number, properties, children)

    def _get_layout(self, message):
        parent, depth, names = message.body
        if parent not in self._menu_items():
            raise ValueError("no menu item %d" % parent)
        return "u(ia{sv}av)", [self.revision,
                               self._node(parent, set(names), depth)]

    def _get_group_properties(self, message):
        ids, names = message.body
        items = self._menu_items()
        wanted = ids or sorted(items)
        return "a(ia{sv})", [[
            (number, dict((key, value) for key, value
                          in items[number].items()
                          if not names or key in names))
            for number in wanted if number in items]]

    def _event(self, message):
        number, event_id = message.body[0], message.body[1]
        if event_id == "clicked":
            self._clicked(number)
        return "", []

    def _event_group(self, message):
        for number, event_id, _data, _timestamp in message.body[0]:
            if event_id == "clicked":
                self._clicked(number)
        return "ai", [[]]

    def _clicked(self, number):
        entries = self.look.entries
        entry = entries[number - 1] if 0 < number <= len(entries) else None
        self._run(entry.action if entry else None)

    def _run(self, action):
        callback = self.actions.get(action) if action else None
        if callback is not None:
            callback()

    # -- the contract -------------------------------------------------------

    def set_paused(self, paused):
        paused = bool(paused)
        if paused == self.paused or self.bus.closed:
            return
        self.paused = paused
        self.revision += 1
        try:
            self.bus.emit(MENU_PATH, MENU, "LayoutUpdated", "ui",
                          [self.revision, 0])
            interface = SNI_FREEDESKTOP if self.watcher == \
                WATCHER_FREEDESKTOP[0] else SNI
            self.bus.emit(SNI_PATH, interface, "NewOverlayIcon")
            self.bus.emit(SNI_PATH, interface, "NewToolTip")
        except self.dbus.DBusError:
            pass

    def pump(self, seconds):
        """Answer the desktop for at most `seconds`, and never raise.

        The daemon calls this four times a second between sorts. A bus that
        has gone away -- the session ended under a daemon that outlived
        it -- leaves the icon gone and the sorting exactly as it was.
        """
        if self.bus.closed:
            return
        try:
            self.bus.pump(seconds)
        except Exception:                        # noqa: BLE001
            self.bus.close()

    def close(self):
        self.bus.close()


_FOLDER_PIXMAPS = [_folder_pixmap(size) for size in PIXMAP_SIZES]
_PAUSE_PIXMAPS = [_pause_pixmap(size) for size in PIXMAP_SIZES]


def _introspection(interfaces):
    """Enough introspection data for a tool that asks what is here.

    Hosts use generated proxies and never ask, but `busctl`, `qdbus` and
    D-Feet do, and an object that cannot say what it is looks broken.
    """
    known = {
        SNI: """  <interface name="org.kde.StatusNotifierItem">
    <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <signal name="NewTitle"/><signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/><signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s"/></signal>
  </interface>
""",
        MENU: """  <interface name="com.canonical.dbusmenu">
    <method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/><arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
    <method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/><arg type="a(ia{sv})" direction="out"/></method>
    <method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
    <method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/><arg type="u" direction="in"/></method>
    <method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
    <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
    <method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/></method>
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
    <signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ias)"/></signal>
    <signal name="ItemActivationRequested"><arg type="i"/><arg type="u"/></signal>
  </interface>
""",
        PROPERTIES: """  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
    <method name="GetAll"><arg type="s" direction="in"/><arg type="a{sv}" direction="out"/></method>
  </interface>
""",
    }
    known[SNI_FREEDESKTOP] = known[SNI].replace(SNI, SNI_FREEDESKTOP)
    return ('<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object '
            'Introspection 1.0//EN"\n "http://www.freedesktop.org/standards/'
            'dbus/1.0/introspect.dtd">\n<node>\n'
            + "".join(known[name] for name in interfaces)
            + """  <interface name="org.freedesktop.DBus.Introspectable">
    <method name="Introspect"><arg type="s" direction="out"/></method>
  </interface>
  <interface name="org.freedesktop.DBus.Peer">
    <method name="Ping"/>
  </interface>
</node>
""")
