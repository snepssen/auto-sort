"""Every Windows call declared, and declared the way Windows defines it.

Nothing here runs Windows. What it can hold every machine to is the one
mistake that made the tray impossible on 64-bit Windows: a function called
without its signature, so that ctypes cut its handles to 32 bits.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
import unittest
from ctypes import wintypes
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import tray                                              # noqa: E402
import winapi                                            # noqa: E402

_CALL = re.compile(r"\b(user32|shell32|kernel32|kernel|psapi)\.(\w+)\(")
_LOADED_CALL = re.compile(r'winapi\.load\("(\w+)"\)\.(\w+)\(')
_ALIASES = {"kernel": "kernel32"}


def _source(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as handle:
        return handle.read()


def _program_files():
    for name in sorted(os.listdir(HERE)):
        if name.endswith(".py"):
            yield name
    for name in sorted(os.listdir(os.path.join(HERE, "readers"))):
        if name.endswith(".py"):
            yield os.path.join("readers", name)


class EveryCallIsDeclared(unittest.TestCase):

    def test_no_function_is_called_bare(self):
        for name in _program_files():
            if name == "winapi.py":
                continue
            self.assertNotIn("windll", _source(name),
                             "%s calls Windows without winapi" % name)

    def test_every_call_has_a_signature(self):
        calls = set()
        for name in ("tray.py", "mover.py", "costs.py", "provenance.py"):
            text = _source(name)
            for library, function in _CALL.findall(text):
                calls.add((_ALIASES.get(library, library), function))
            calls.update(_LOADED_CALL.findall(text))
        self.assertTrue(calls)
        for library, function in sorted(calls):
            self.assertIn(function, winapi.SIGNATURES.get(library, {}),
                          "%s.%s is called but not declared"
                          % (library, function))

    def test_handles_and_message_parameters_are_pointer_sized(self):
        pointer = ctypes.sizeof(ctypes.c_void_p)
        wide = (wintypes.HANDLE, wintypes.HWND, wintypes.HMENU,
                wintypes.HICON, wintypes.HINSTANCE, wintypes.HMODULE,
                wintypes.WPARAM, wintypes.LPARAM)
        for library, functions in winapi.SIGNATURES.items():
            for function, (restype, argtypes) in functions.items():
                for kind in [restype] + list(argtypes):
                    if kind in wide:
                        self.assertEqual(ctypes.sizeof(kind), pointer,
                                         "%s.%s" % (library, function))
        # LRESULT and the window procedure's return: pointer-sized too.
        self.assertEqual(
            ctypes.sizeof(winapi.SIGNATURES["user32"]["DefWindowProcW"][0]),
            pointer)


class TheNotifyIconStructure(unittest.TestCase):
    """Windows accepts only the sizes of the versions it defines."""

    def test_it_is_the_whole_vista_structure(self):
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            self.skipTest("the documented size here is the 64-bit one")
        # Windows' WCHAR is two bytes and its DWORD four; ctypes on macOS
        # and Linux makes them four and eight (wchar_t, unsigned long).
        with mock.patch.object(wintypes, "WCHAR", ctypes.c_uint16), \
                mock.patch.object(wintypes, "DWORD", ctypes.c_uint32):
            structure = tray._windows_structures(
                ctypes.CFUNCTYPE(ctypes.c_ssize_t))[0]
        self.assertEqual(ctypes.sizeof(structure), 976)
        self.assertEqual(structure._fields_[-1][0], "hBalloonIcon")
        self.assertEqual(structure.hBalloonIcon.offset, 968)


if __name__ == "__main__":
    unittest.main()
