"""Optional native tray control, with a deliberately harmless headless mode."""

from __future__ import annotations

import os
import sys


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


def create(actions):
    """Return a best-effort native tray, never an object that can stop sorting."""
    try:
        if sys.platform == "darwin":
            return _mac_tray(actions)
        if sys.platform.startswith("win"):
            return _windows_tray(actions)
        return _linux_tray(actions)
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
# callbacks; they go through whichever tray is currently active instead.
# There is only ever one.
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


def _dispatch(name):                                         # pragma: no cover
    """Route a menu selector to the tray that is currently up."""
    tray = _ACTIVE
    if tray is None:
        return
    if name == "clicked":
        tray._clicked()
        return
    action = tray.actions.get(name)
    if action:
        action()


def _mac_tray(actions):                                      # pragma: no cover
    """A status item built on the Objective-C runtime, with no dependencies."""
    import ctypes

    runtime = _Runtime()
    void_p = ctypes.c_void_p

    class MacTray(object):
        available = True
        reason = ""

        def __init__(self):
            self.runtime = runtime
            self.actions = actions
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
            _MAC_TARGET = runtime.define("AutoSortStatusTarget", {
                "clicked:": lambda: _dispatch("clicked"),
                "openLog:": lambda: _dispatch("open_log"),
                "togglePause:": lambda: _dispatch("toggle_pause"),
                "sortNow:": lambda: _dispatch("sort_now"),
                "restart:": lambda: _dispatch("restart"),
                "quit:": lambda: _dispatch("quit"),
            })
            return _MAC_TARGET

        def _set_icon(self):
            image = runtime.send(
                void_p, runtime.cls("NSImage"),
                "imageWithSystemSymbolName:accessibilityDescription:",
                (void_p, runtime.string("doc")),
                (void_p, runtime.string("auto-sort")))
            if image:
                runtime.send(None, image, "setTemplate:", (ctypes.c_bool, True))
                runtime.send(None, self.button, "setImage:", (void_p, image))
            else:
                runtime.send(None, self.button, "setTitle:",
                             (void_p, runtime.string("AS")))

        def _build_menu(self):
            self.menu = runtime.send(void_p, runtime.send(
                void_p, runtime.cls("NSMenu"), "alloc"), "init")
            self.pause_item = None
            # Restart is in the menu because a daemon cannot reload its own
            # code, so every change to auto-sort itself needs one -- and
            # asking for a terminal is the wrong answer for somebody whose
            # whole interface is this icon.
            for title, selector in (("Open log", "openLog:"),
                                    ("Pause sorting", "togglePause:"),
                                    ("Sort now", "sortNow:"),
                                    (None, None),
                                    ("Restart auto-sort", "restart:"),
                                    ("Quit auto-sort", "quit:")):
                if title is None:
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
                    (void_p, runtime.string(title)),
                    (void_p, runtime.sel(selector)),
                    (void_p, runtime.string("")))
                runtime.send(None, item, "setTarget:", (void_p, self.instance))
                runtime.send(None, self.menu, "addItem:", (void_p, item))
                if selector == "togglePause:":
                    self.pause_item = item

        def _clicked(self):
            """Left click opens the log; right click raises the menu.

            A menu attached permanently swallows every click and the icon
            stops opening the log, so it is attached for the length of one
            right click and taken away again.
            """
            event = runtime.send(void_p, self.application, "currentEvent")
            secondary = False
            if event:
                kind = runtime.send(ctypes.c_ulonglong, event, "type")
                modifiers = runtime.send(ctypes.c_ulonglong, event,
                                         "modifierFlags")
                secondary = (kind == 4                      # RightMouseUp
                             or bool(modifiers & (1 << 18)))  # Control
            if secondary:
                runtime.send(None, self.status_item, "setMenu:",
                             (void_p, self.menu))
                runtime.send(None, self.button, "performClick:",
                             (void_p, None))
                runtime.send(None, self.status_item, "setMenu:",
                             (void_p, None))
            else:
                actions["open_log"]()

        def set_paused(self, paused):
            if self.pause_item:
                runtime.send(None, self.pause_item, "setTitle:",
                             (void_p, runtime.string(
                                 "Resume sorting" if paused
                                 else "Pause sorting")))

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


def _windows_tray(actions):                                  # pragma: no cover
    """Shell_NotifyIcon implementation using only ctypes and a hidden window.

    The callback lives on the daemon thread: `PollingDaemon` calls `pump` at
    least four times a second, so this never needs another thread or a message
    loop that could hold up the filesystem work.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32

    WM_COMMAND = 0x0111
    WM_DESTROY = 0x0002
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONUP = 0x0205
    WM_APP = 0x8000
    CALLBACK = WM_APP + 71
    NIM_ADD, NIM_DELETE = 0x00000000, 0x00000002
    NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x00000001, 0x00000002, 0x00000004
    TPM_RIGHTBUTTON = 0x0002
    ID_OPEN, ID_TOGGLE, ID_SORT, ID_QUIT, ID_RESTART = 1, 2, 3, 4, 5
    IDI_APPLICATION = 32512
    SHGFI_ICON, SHGFI_SMALLICON, SHGFI_USEFILEATTRIBUTES = 0x100, 0x1, 0x10
    FILE_ATTRIBUTE_NORMAL = 0x80

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HANDLE),
            ("szTip", wintypes.WCHAR * 128),
        ]

    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                 wintypes.UINT, ctypes.c_size_t,
                                 ctypes.c_ssize_t)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HANDLE),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [("hIcon", wintypes.HANDLE), ("iIcon", ctypes.c_int),
                    ("dwAttributes", wintypes.DWORD),
                    ("szDisplayName", wintypes.WCHAR * 260),
                    ("szTypeName", wintypes.WCHAR * 80)]

    class WindowsTray(object):
        available = True
        reason = ""

        def __init__(self):
            self.instance = kernel32.GetModuleHandleW(None)
            self.class_name = "auto-sort-tray-%d" % id(self)
            self.paused = False
            self.closed = False
            self.proc = WNDPROC(self._window_proc)
            window_class = WNDCLASSW()
            window_class.lpfnWndProc = self.proc
            window_class.hInstance = self.instance
            window_class.lpszClassName = self.class_name
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise OSError("could not register the tray window class")
            self.window = user32.CreateWindowExW(
                0, self.class_name, self.class_name, 0, 0, 0, 0, 0,
                None, None, self.instance, None)
            if not self.window:
                user32.UnregisterClassW(self.class_name, self.instance)
                raise OSError("could not create the tray window")
            info = SHFILEINFOW()
            shell32.SHGetFileInfoW("", FILE_ATTRIBUTE_NORMAL,
                                    ctypes.byref(info), ctypes.sizeof(info),
                                    SHGFI_ICON | SHGFI_SMALLICON |
                                    SHGFI_USEFILEATTRIBUTES)
            self.icon_from_shell = bool(info.hIcon)
            self.icon = info.hIcon or user32.LoadIconW(None, IDI_APPLICATION)
            self.icon_data = NOTIFYICONDATAW()
            self.icon_data.cbSize = ctypes.sizeof(self.icon_data)
            self.icon_data.hWnd = self.window
            self.icon_data.uID = 1
            self.icon_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            self.icon_data.uCallbackMessage = CALLBACK
            self.icon_data.hIcon = self.icon
            self.icon_data.szTip = "auto-sort"
            if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.icon_data)):
                user32.DestroyWindow(self.window)
                user32.UnregisterClassW(self.class_name, self.instance)
                raise OSError("could not add the notification icon")

        def _window_proc(self, _window, message, wparam, lparam):
            if message == CALLBACK and lparam == WM_LBUTTONUP:
                actions["open_log"]()
                return 0
            if message == CALLBACK and lparam == WM_RBUTTONUP:
                self._show_menu()
                return 0
            if message == WM_COMMAND:
                self._run_action(wparam & 0xffff)
                return 0
            if message == WM_DESTROY:
                return 0
            return user32.DefWindowProcW(_window, message, wparam, lparam)

        def _show_menu(self):
            menu = user32.CreatePopupMenu()
            try:
                user32.AppendMenuW(menu, 0, ID_OPEN, "Open log")
                user32.AppendMenuW(menu, 0, ID_TOGGLE,
                                   "Resume sorting" if self.paused else "Pause sorting")
                user32.AppendMenuW(menu, 0, ID_SORT, "Sort now")
                user32.AppendMenuW(menu, 0x0800, 0, None)
                user32.AppendMenuW(menu, 0, ID_RESTART, "Restart auto-sort")
                user32.AppendMenuW(menu, 0, ID_QUIT, "Quit auto-sort")
                point = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(point))
                user32.SetForegroundWindow(self.window)
                user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON, point.x, point.y,
                                      0, self.window, None)
            finally:
                user32.DestroyMenu(menu)

        def _run_action(self, item):
            mapping = {ID_OPEN: "open_log", ID_TOGGLE: "toggle_pause",
                       ID_SORT: "sort_now", ID_RESTART: "restart",
                       ID_QUIT: "quit"}
            callback = actions.get(mapping.get(item))
            if callback:
                callback()

        def set_paused(self, paused):
            self.paused = bool(paused)

        def pump(self, _seconds):
            message = wintypes.MSG()
            while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
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
MENU = "com.canonical.dbusmenu"
PROPERTIES = "org.freedesktop.DBus.Properties"
WATCHER = ("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
           "org.kde.StatusNotifierWatcher")

# A name from the freedesktop icon naming specification, so every icon
# theme has one; the same icon the applications-menu entry uses.
ICON = "folder"
PAUSED_OVERLAY = "media-playback-pause"

# Menu item ids. Zero is the root, by the protocol.
_OPEN, _TOGGLE, _SORT, _SEPARATOR, _RESTART, _QUIT = 1, 2, 3, 4, 5, 6


def _linux_tray(actions, connect=None):
    import dbuswire

    if connect is None:
        connect = dbuswire.Connection
    connection = connect()
    try:
        return LinuxTray(actions, connection, dbuswire)
    except Exception:
        connection.close()
        raise


class LinuxTray(object):
    """A tray icon on any desktop that hosts StatusNotifierItems.

    `available` says whether a host was there to show it when it started.
    Where none was -- a desktop that has no tray protocol, or a login where
    auto-sort beat the desktop to it -- the item still listens, and appears
    by itself if a watcher turns up later.
    """

    def __init__(self, actions, connection, dbuswire):
        self.actions = actions
        self.bus = connection
        self.dbus = dbuswire
        self.paused = False
        self.revision = 1
        self.name = "org.kde.StatusNotifierItem-%d-1" % os.getpid()
        self.registered = False
        variant = dbuswire.Variant

        connection.export(SNI_PATH, SNI, {
            "Activate": self._activate,
            "SecondaryActivate": lambda _message: ("", []),
            "ContextMenu": lambda _message: ("", []),
            "Scroll": lambda _message: ("", []),
        })
        connection.export(SNI_PATH, PROPERTIES, {
            "Get": lambda message: ("v", [self._item_properties()[
                message.body[1]]]),
            "GetAll": lambda message: ("a{sv}", [
                self._item_properties() if message.body[0] == SNI else {}]),
        })
        connection.export(MENU_PATH, MENU, {
            "GetLayout": self._get_layout,
            "GetGroupProperties": self._get_group_properties,
            "GetProperty": lambda message: ("v", [self._menu_items()[
                message.body[0]][message.body[1]]]),
            "Event": self._event,
            "EventGroup": self._event_group,
            "AboutToShow": lambda _message: ("b", [False]),
            "AboutToShowGroup": lambda _message: ("aiai", [[], []]),
        })
        connection.export(MENU_PATH, PROPERTIES, {
            "Get": lambda message: ("v", [self._menu_properties()[
                message.body[1]]]),
            "GetAll": lambda message: ("a{sv}", [
                self._menu_properties() if message.body[0] == MENU else {}]),
        })
        for path, interfaces in ((SNI_PATH, (SNI, PROPERTIES)),
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
        connection.add_match(
            "type='signal',sender='org.freedesktop.DBus',"
            "interface='org.freedesktop.DBus',member='NameOwnerChanged',"
            "arg0='%s'" % WATCHER[0],
            lambda message: message.member == "NameOwnerChanged"
            and message.body and message.body[0] == WATCHER[0],
            self._watcher_changed)
        self.available = self._register()
        self.reason = "" if self.available else (
            "no StatusNotifier host on this desktop yet; the icon will "
            "appear if one starts")

    # -- registration -------------------------------------------------------

    def _register(self):
        try:
            self.bus.call(*WATCHER, member="RegisterStatusNotifierItem",
                          signature="s", body=[self.name])
        except self.dbus.DBusError:
            self.registered = False
            return False
        self.registered = True
        return True

    def _watcher_changed(self, message):
        _name, _old, new = message.body[:3]
        if new:
            self._register()
        else:
            self.registered = False

    # -- the item -----------------------------------------------------------

    def _item_properties(self):
        variant = self._variant
        state = "Paused" if self.paused else "Watching your folders"
        return {
            "Category": variant("s", "ApplicationStatus"),
            "Id": variant("s", "auto-sort"),
            "Title": variant("s", "auto-sort"),
            "Status": variant("s", "Active"),
            "WindowId": variant("i", 0),
            "IconName": variant("s", ICON),
            "IconThemePath": variant("s", ""),
            "IconPixmap": variant("a(iiay)", []),
            "OverlayIconName": variant("s", PAUSED_OVERLAY
                                       if self.paused else ""),
            "OverlayIconPixmap": variant("a(iiay)", []),
            "AttentionIconName": variant("s", ""),
            "AttentionIconPixmap": variant("a(iiay)", []),
            "AttentionMovieName": variant("s", ""),
            "ToolTip": variant("(sa(iiay)ss)",
                               (ICON, [], "auto-sort", state)),
            "ItemIsMenu": variant("b", False),
            "Menu": variant("o", MENU_PATH),
        }

    def _activate(self, _message):
        # A left click opens the log, as it does on a Mac; the menu is a
        # right click away.
        self._run("open_log")
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
        # Restart is here because a daemon cannot reload its own code, and
        # asking for a terminal is the wrong answer for somebody whose whole
        # interface is this icon.
        items = {0: {"children-display": variant("s", "submenu")}}
        for number, label in ((_OPEN, "Open log"),
                              (_TOGGLE, "Resume sorting" if self.paused
                               else "Pause sorting"),
                              (_SORT, "Sort now"),
                              (_SEPARATOR, None),
                              (_RESTART, "Restart auto-sort"),
                              (_QUIT, "Quit auto-sort")):
            if label is None:
                items[number] = {"type": variant("s", "separator")}
            else:
                items[number] = {"label": variant("s", label),
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
                        for child in (_OPEN, _TOGGLE, _SORT, _SEPARATOR,
                                      _RESTART, _QUIT)]
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
        self._run({_OPEN: "open_log", _TOGGLE: "toggle_pause",
                   _SORT: "sort_now", _RESTART: "restart",
                   _QUIT: "quit"}.get(number))

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
            self.bus.emit(SNI_PATH, SNI, "NewOverlayIcon")
            self.bus.emit(SNI_PATH, SNI, "NewToolTip")
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
