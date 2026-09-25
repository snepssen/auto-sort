"""Every Windows function auto-sort calls, with its real signature.

`ctypes.windll.user32.CreateWindowExW(...)` works in a demonstration and
fails on the machines people own. Called without `argtypes` and `restype`,
ctypes passes and returns every value as a 32-bit int, and on 64-bit
Windows a handle, a module address or a message parameter is 64 bits:

- `GetModuleHandleW` returned an address cut in half, so the tray's window
  class was registered against a module that does not exist;
- `DefWindowProcW`, handed the pointer that comes with a window's very
  first message, raised inside the window procedure, the procedure
  answered 0, and Windows took that as "do not create this window";
- `GetCurrentProcess`'s pseudo-handle -1 arrived as 0x00000000FFFFFFFF.

None of that was ever seen, because none of it was ever run on Windows.
So the signatures live here, in one table, taken from the Win32
documentation, and `tests/test_winapi.py` holds every call in the program
to it -- on any machine, because the table is only data until `load` is
asked for it on Windows.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_P = ctypes.c_void_p
_LRESULT = ctypes.c_ssize_t
_UINT_PTR = ctypes.c_size_t

SIGNATURES = {
    "kernel32": {
        "GetModuleHandleW": (wintypes.HMODULE, [wintypes.LPCWSTR]),
        "GetCurrentProcess": (wintypes.HANDLE, []),
        "CreateFileW": (wintypes.HANDLE, [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, _P,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]),
        "CloseHandle": (wintypes.BOOL, [wintypes.HANDLE]),
        "MoveFileExW": (wintypes.BOOL, [
            wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]),
        "GetDriveTypeW": (wintypes.UINT, [wintypes.LPCWSTR]),
    },
    "psapi": {
        "GetProcessMemoryInfo": (wintypes.BOOL, [
            wintypes.HANDLE, _P, wintypes.DWORD]),
    },
    "user32": {
        "RegisterClassW": (wintypes.ATOM, [_P]),
        "UnregisterClassW": (wintypes.BOOL, [
            wintypes.LPCWSTR, wintypes.HINSTANCE]),
        "CreateWindowExW": (wintypes.HWND, [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wintypes.HWND, wintypes.HMENU,
            wintypes.HINSTANCE, _P]),
        "DestroyWindow": (wintypes.BOOL, [wintypes.HWND]),
        "DefWindowProcW": (_LRESULT, [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
            wintypes.LPARAM]),
        # The name is a pointer or a small integer (MAKEINTRESOURCE), so it
        # is declared as a pointer: IDI_APPLICATION is 32512.
        "LoadIconW": (wintypes.HICON, [wintypes.HINSTANCE, _P]),
        "DestroyIcon": (wintypes.BOOL, [wintypes.HICON]),
        "CreatePopupMenu": (wintypes.HMENU, []),
        "AppendMenuW": (wintypes.BOOL, [
            wintypes.HMENU, wintypes.UINT, _UINT_PTR, wintypes.LPCWSTR]),
        "DestroyMenu": (wintypes.BOOL, [wintypes.HMENU]),
        "GetCursorPos": (wintypes.BOOL, [_P]),
        "SetForegroundWindow": (wintypes.BOOL, [wintypes.HWND]),
        "TrackPopupMenu": (wintypes.BOOL, [
            wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wintypes.HWND, _P]),
        "PostMessageW": (wintypes.BOOL, [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
            wintypes.LPARAM]),
        "PeekMessageW": (wintypes.BOOL, [
            _P, wintypes.HWND, wintypes.UINT, wintypes.UINT,
            wintypes.UINT]),
        "TranslateMessage": (wintypes.BOOL, [_P]),
        "DispatchMessageW": (_LRESULT, [_P]),
        "RegisterWindowMessageW": (wintypes.UINT, [wintypes.LPCWSTR]),
    },
    "shell32": {
        "Shell_NotifyIconW": (wintypes.BOOL, [wintypes.DWORD, _P]),
        "SHGetFileInfoW": (_UINT_PTR, [
            wintypes.LPCWSTR, wintypes.DWORD, _P, wintypes.UINT,
            wintypes.UINT]),
    },
}


class _Library(object):
    """One DLL, with only the declared functions reachable."""

    def __init__(self, name):
        self._dll = ctypes.WinDLL(name, use_last_error=True)
        for function, (restype, argtypes) in SIGNATURES[name].items():
            bound = getattr(self._dll, function)
            bound.restype = restype
            bound.argtypes = argtypes
            setattr(self, function, bound)

    def __getattr__(self, name):
        # Only reached for a name not declared above: refusing it is the
        # point, since an undeclared call is exactly the bug this prevents.
        raise AttributeError("%s is not declared in winapi.SIGNATURES" % name)


_LOADED = {}


def load(name):
    """The DLL `name`, every function declared. Windows only."""
    if name not in _LOADED:
        _LOADED[name] = _Library(name)
    return _LOADED[name]


def last_error():
    return ctypes.get_last_error()
