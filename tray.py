"""Optional native tray control, with a deliberately harmless headless mode."""

from __future__ import annotations

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
        return UnavailableTray(
            "no StatusNotifier backend on this Linux desktop")
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
            for title, selector in (("Open log", "openLog:"),
                                    ("Pause sorting", "togglePause:"),
                                    ("Sort now", "sortNow:"),
                                    (None, None),
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
    ID_OPEN, ID_TOGGLE, ID_SORT, ID_QUIT = 1, 2, 3, 4
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
                       ID_SORT: "sort_now", ID_QUIT: "quit"}
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
