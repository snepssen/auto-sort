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
            "no StatusNotifier backend on this Linux desktop; continuing headless")
    except Exception as error:
        return UnavailableTray("native tray unavailable: %s" % error)


def _mac_tray(actions):                                      # pragma: no cover
    """PyObjC implementation, loaded only when the host happens to provide it."""
    from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                        NSImage, NSMenu, NSMenuItem, NSStatusBar,
                        NSVariableStatusItemLength)
    from Foundation import NSDate, NSObject, NSRunLoop

    class Target(NSObject):
        def initWithActions_(self, callbacks):
            self = super(Target, self).init()
            if self is not None:
                self.callbacks = callbacks
            return self

        def openLog_(self, _sender):
            self.callbacks["open_log"]()

        def togglePause_(self, _sender):
            self.callbacks["toggle_pause"]()

        def sortNow_(self, _sender):
            self.callbacks["sort_now"]()

        def quit_(self, _sender):
            self.callbacks["quit"]()

    class MacTray(object):
        available = True
        reason = ""

        def __init__(self):
            application = NSApplication.sharedApplication()
            application.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
            self.status_bar = NSStatusBar.systemStatusBar()
            self.status_item = self.status_bar.statusItemWithLength_(
                NSVariableStatusItemLength)
            button = self.status_item.button()
            try:
                image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                    "doc", "auto-sort")
                image.setTemplate_(True)
                button.setImage_(image)
            except AttributeError:
                button.setTitle_("□")
            self.target = Target.alloc().initWithActions_(actions)
            button.setTarget_(self.target)
            button.setAction_("openLog:")
            self.menu = NSMenu.alloc().init()
            self.menu.addItem_(self._item("Open log", "openLog:"))
            self.pause_item = self._item("Pause sorting", "togglePause:")
            self.menu.addItem_(self.pause_item)
            self.menu.addItem_(self._item("Sort now", "sortNow:"))
            self.menu.addItem_(NSMenuItem.separatorItem())
            self.menu.addItem_(self._item("Quit auto-sort", "quit:"))

        def _item(self, title, action):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, action, "")
            item.setTarget_(self.target)
            return item

        def set_paused(self, paused):
            self.pause_item.setTitle_("Resume sorting" if paused
                                      else "Pause sorting")

        def pump(self, seconds):
            until = NSDate.dateWithTimeIntervalSinceNow_(max(0.001, seconds))
            NSRunLoop.currentRunLoop().runUntilDate_(until)

        def close(self):
            self.status_bar.removeStatusItem_(self.status_item)

    return MacTray()


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
