"""Optional tray controls must never make the daemon unavailable."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import daemon                                             # noqa: E402
import dbuswire                                           # noqa: E402
import tray                                               # noqa: E402


class TrayAvailability(unittest.TestCase):

    def test_linux_without_a_session_bus_is_explicitly_headless(self):
        """No bus in the environment -- a daemon started from a bare SSH
        login, or a desktop with no D-Bus at all -- is no icon and a
        reason, never an exception. And never the real bus of whoever
        runs the tests: an icon appeared on a desktop during a test run."""
        environment = dict((key, value) for key, value in os.environ.items()
                           if key not in ("DBUS_SESSION_BUS_ADDRESS",
                                          "XDG_RUNTIME_DIR"))
        with mock.patch("tray.sys.platform", "linux"), \
                mock.patch.dict(os.environ, environment, clear=True):
            item = tray.create({})
        self.assertFalse(item.available)
        self.assertIn("session bus", item.reason)

    def test_windows_backend_failure_falls_back_without_raising(self):
        with mock.patch("tray.sys.platform", "win32"), \
                mock.patch("tray._windows_tray", side_effect=OSError("Shell")):
            item = tray.create({})
        self.assertFalse(item.available)
        self.assertIn("unavailable", item.reason)

    def test_macos_import_failure_falls_back_without_raising(self):
        with mock.patch("tray.sys.platform", "darwin"), \
                mock.patch("tray._mac_tray", side_effect=ImportError("PyObjC")):
            item = tray.create({})
        self.assertFalse(item.available)
        self.assertIn("unavailable", item.reason)


class TrayActions(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.rules_file = os.path.join(self.directory, "rules.ini")
        self.state_file = os.path.join(self.directory, "state.db")
        inbox = os.path.join(self.directory, "inbox")
        os.makedirs(inbox)
        with open(self.rules_file, "w", encoding="utf-8") as handle:
            handle.write("[watch]\nfolders = %s\n" % inbox)
        self.messages = []
        self.service = daemon.PollingDaemon(
            self.rules_file, self.state_file, port=0,
            output=self.messages.append)

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_pause_sort_and_quit_actions_are_local_state_changes(self):
        self.assertFalse(self.service.journal.paused())
        self.service._tray_toggle_pause()
        self.assertTrue(self.service.journal.paused())
        self.service._tray_toggle_pause()
        self.assertFalse(self.service.journal.paused())
        self.service._tray_sort_now()
        self.assertTrue(self.service._sort_requested)
        self.service._tray_quit()
        self.assertTrue(self.service._quit_requested)

    def test_every_tray_action_is_in_the_log(self):
        """A click that seemed to do nothing could not be checked: the log
        said "Restarting" and nothing else a tray click had caused, so a
        Resume that never arrived and one that did looked the same."""
        with mock.patch("daemon.webbrowser.open", return_value=True):
            self.service._tray_open_log()
        self.service._tray_toggle_pause()
        self.service._tray_toggle_pause()
        self.service._tray_sort_now()
        self.service._tray_quit()
        said = "\n".join(self.messages)
        for line in ("Opening the log page", "Paused from the tray",
                     "Resumed from the tray", "Sort now, from the tray",
                     "Quit from the tray"):
            self.assertIn(line, said)

    def test_a_log_page_that_would_not_open_says_so(self):
        with mock.patch("daemon.webbrowser.open", return_value=False):
            self.service._tray_open_log()
        self.assertTrue(any("could not open" in message.lower()
                            for message in self.messages), self.messages)

    def test_restart_stands_down_and_says_why(self):
        """Quitting and restarting both end the loop; only one comes back."""
        self.assertFalse(self.service.restart_requested)
        self.service._tray_restart()
        self.assertTrue(self.service.restart_requested)
        self.assertTrue(self.service._quit_requested)

    def test_open_log_uses_the_daemon_owned_url(self):
        with mock.patch("daemon.webbrowser.open") as open_browser:
            self.service._tray_open_log()
        open_browser.assert_called_once_with(self.service.web.url)


class NativeBackend(unittest.TestCase):
    """Exercise the real backend when the host can load it.

    `tray.create` turns any failure into "continuing headless", which is the
    right behaviour for a sorter -- a missing status item must never stop
    files being filed -- and is also why this code sat broken and unnoticed
    through four separate faults. Nothing failed, nothing was reported, and
    the only symptom was an absence.

    So these assert the backend actually starts rather than that it degrades
    politely, and they no longer skip for a missing package, because there is
    no package.
    """

    def available(self):
        # No import check any more: the backend needs nothing installed, so
        # on a Mac it either works or it is broken, and skipping would hide
        # the difference.
        return sys.platform == "darwin"

    def setUp(self):
        if not self.available():
            self.skipTest("the native status item is macOS only")
        self.calls = []
        self.actions = {
            "open_log": lambda: self.calls.append("open_log"),
            "toggle_pause": lambda: self.calls.append("toggle_pause"),
            "sort_now": lambda: self.calls.append("sort_now"),
            "restart": lambda: self.calls.append("restart"),
            "quit": lambda: self.calls.append("quit"),
        }

    def test_the_status_item_actually_starts(self):
        item = tray.create(self.actions)
        try:
            self.assertTrue(item.available,
                            "tray fell back to headless: %s"
                            % getattr(item, "reason", ""))
        finally:
            item.close()

    def test_the_menu_is_reachable(self):
        # Built but never attached, the menu is dead code and Pause, Sort now
        # and Quit cannot be reached at all.
        item = tray.create(self.actions)
        try:
            self.assertTrue(item.available)
            self.assertTrue(item.menu, "no menu was built")
            self.assertTrue(item.pause_item,
                            "the pause entry was never found, so it can "
                            "never be renamed")
            # Open log, Pause, Sort now, separator, Restart, Quit.
            self.assertEqual(item.runtime.send(
                item.runtime.ctypes.c_long, item.menu, "numberOfItems"), 6)
        finally:
            item.close()

    def test_clicking_the_icon_opens_the_log(self):
        # The whole point of a constant presence in the menu bar.
        item = tray.create(self.actions)
        try:
            item._clicked()
            self.assertEqual(self.calls, ["open_log"])
        finally:
            item.close()

    def test_pausing_renames_the_menu_entry(self):
        item = tray.create(self.actions)
        try:
            # Renaming goes through Objective-C, so the assertion is that
            # it does not raise and the entry is still there afterwards.
            item.set_paused(True)
            item.set_paused(False)
            self.assertTrue(item.pause_item)
        finally:
            item.close()


class FakeBus(object):
    """A session bus as far as the tray can tell: calls are recorded,
    signals are kept, and exported handlers are called directly."""

    def __init__(self, watcher=True, name="org.kde.StatusNotifierWatcher"):
        # The watchers on this bus, by name; a desktop runs one or both.
        self.watchers = {name} if watcher else set()
        self.accepted = []
        self.closed = False
        self.objects = {}
        self.calls = []
        self.emitted = []
        self.matches = []

    def export(self, path, interface, handlers):
        self.objects.setdefault(path, {})[interface] = handlers

    def call(self, destination, path, interface, member, signature="",
             body=(), timeout=None):
        self.calls.append((destination, member, list(body)))
        if member == "RegisterStatusNotifierItem":
            if destination not in self.watchers:
                raise dbuswire.DBusError("org.freedesktop.DBus.Error."
                                         "ServiceUnknown")
            self.accepted.append((destination, list(body)))
        return []

    def add_match(self, rule, predicate, callback):
        self.matches.append((rule, predicate, callback))

    def emit(self, path, interface, member, signature="", body=()):
        self.emitted.append((path, interface, member, list(body)))

    def pump(self, seconds):
        pass

    def close(self):
        self.closed = True

    def invoke(self, path, interface, member, *body):
        """Call an exported method as the desktop would, and put the
        answer through the real marshaller with the signature the handler
        says it has -- which is what would reach the desktop."""
        message = dbuswire.Message(dbuswire.METHOD_CALL, path=path,
                                   interface=interface, member=member,
                                   body=body)
        signature, reply = self.objects[path][interface][member](message)
        if signature:
            dbuswire.marshal(signature, reply)
        return reply

    def owner_changed(self, old, new, name="org.kde.StatusNotifierWatcher"):
        if new:
            self.watchers.add(name)
        else:
            self.watchers.discard(name)
        signal = dbuswire.Message(
            dbuswire.SIGNAL, interface="org.freedesktop.DBus",
            member="NameOwnerChanged", signature="sss",
            body=[name, old, new])
        for _rule, predicate, callback in self.matches:
            if predicate(signal):
                callback(signal)

    def registrations(self):
        """What each watcher that took the item was given."""
        return [body for _watcher, body in self.accepted]


class LinuxBackend(unittest.TestCase):
    """The StatusNotifierItem, against a bus that records what it is told.

    Plasma showed the icon and its menu on a Steam Deck; these hold the
    item to what it told Plasma, so the next change cannot quietly send a
    property of the wrong type or a menu the host cannot read.
    """

    def tray(self, watcher=True, name="org.kde.StatusNotifierWatcher"):
        self.bus = FakeBus(watcher, name)
        self.done = []
        actions = dict((name, (lambda name=name: self.done.append(name)))
                       for name in ("open_log", "toggle_pause", "sort_now",
                                    "restart", "quit"))
        return tray._linux_tray(actions, connect=lambda: self.bus)

    def labels(self, item):
        _revision, (_id, _properties, children) = self.bus.invoke(
            tray.MENU_PATH, tray.MENU, "GetLayout", 0, -1, [])
        return [child.value[1].get("label", child.value[1].get("type")).value
                for child in children]

    def test_it_takes_a_name_and_registers_it_with_the_watcher(self):
        item = self.tray()
        self.assertTrue(item.available)
        name = "org.kde.StatusNotifierItem-%d-1" % os.getpid()
        self.assertIn(("org.freedesktop.DBus", "RequestName", [name, 4]),
                      self.bus.calls)
        self.assertEqual(self.bus.registrations(), [[name]])

    def test_every_property_the_host_reads(self):
        item = self.tray()
        properties = self.bus.invoke(tray.SNI_PATH, tray.PROPERTIES,
                                     "GetAll", tray.SNI)[0]
        self.assertEqual(properties["IconName"].value, "folder")
        self.assertEqual(properties["Status"].value, "Active")
        self.assertEqual(properties["Menu"].value, tray.MENU_PATH)
        self.assertEqual(properties["Menu"].signature, "o")
        self.assertEqual(properties["ToolTip"].value[3],
                         "Watching your folders")
        self.assertEqual(self.bus.invoke(tray.SNI_PATH, tray.PROPERTIES,
                                         "Get", tray.SNI, "Title")[0].value,
                         "auto-sort")
        self.assertEqual(self.bus.invoke(tray.SNI_PATH, tray.PROPERTIES,
                                         "GetAll", "some.Other")[0], {})
        item.close()

    def test_the_menu_has_the_same_actions_as_the_other_two(self):
        item = self.tray()
        self.assertEqual(self.labels(item),
                         ["Open log", "Pause sorting", "Sort now",
                          "separator", "Restart auto-sort",
                          "Quit auto-sort"])
        groups = self.bus.invoke(tray.MENU_PATH, tray.MENU,
                                 "GetGroupProperties", [1, 2], ["label"])[0]
        self.assertEqual([(number, properties["label"].value)
                          for number, properties in groups],
                         [(1, "Open log"), (2, "Pause sorting")])
        self.assertEqual(self.bus.invoke(tray.MENU_PATH, tray.PROPERTIES,
                                         "Get", tray.MENU,
                                         "Version")[0].value, 3)

    def test_each_entry_does_what_it_says(self):
        item = self.tray()
        for number in (1, 2, 3, 5, 6):
            self.bus.invoke(tray.MENU_PATH, tray.MENU, "Event", number,
                            "clicked", dbuswire.Variant("s", ""), 0)
        self.bus.invoke(tray.MENU_PATH, tray.MENU, "Event", 1, "hovered",
                        dbuswire.Variant("s", ""), 0)
        self.assertEqual(self.done, ["open_log", "toggle_pause", "sort_now",
                                     "restart", "quit"])
        self.bus.invoke(tray.MENU_PATH, tray.MENU, "EventGroup",
                        [(3, "clicked", dbuswire.Variant("s", ""), 0)])
        self.assertEqual(self.done[-1], "sort_now")

    def test_a_left_click_opens_the_log(self):
        item = self.tray()
        self.bus.invoke(tray.SNI_PATH, tray.SNI, "Activate", 10, 10)
        self.assertEqual(self.done, ["open_log"])

    def test_pausing_renames_the_entry_and_tells_the_host(self):
        item = self.tray()
        item.set_paused(True)
        self.assertEqual(self.labels(item)[1], "Resume sorting")
        properties = self.bus.invoke(tray.SNI_PATH, tray.PROPERTIES,
                                     "GetAll", tray.SNI)[0]
        self.assertEqual(properties["OverlayIconName"].value,
                         "media-playback-pause")
        self.assertEqual(properties["ToolTip"].value[3], "Paused")
        members = [member for _path, _interface, member, _body
                   in self.bus.emitted]
        self.assertEqual(members, ["LayoutUpdated", "NewOverlayIcon",
                                   "NewToolTip"])
        revision = self.bus.emitted[0][3][0]
        self.assertEqual(self.bus.invoke(tray.MENU_PATH, tray.MENU,
                                         "GetLayout", 0, -1, [])[0], revision)
        item.set_paused(True)                  # the loop says so every cycle
        self.assertEqual(len(self.bus.emitted), 3)

    def test_a_watcher_that_restarts_is_registered_with_again(self):
        """kded owns the watcher on Plasma 6 and forgets every item when it
        restarts, without telling any of them."""
        item = self.tray()
        self.bus.owner_changed(":1.30", "")
        self.assertFalse(item.registered)
        self.bus.owner_changed("", ":1.31")
        self.assertTrue(item.registered)
        self.assertEqual(len(self.bus.registrations()), 2)

    def test_a_freedesktop_watcher_takes_it_too(self):
        """swaybar runs its watcher under freedesktop's name as well as
        KDE's, and reads an item registered there under the matching
        interface -- which the item answers to."""
        item = self.tray(name="org.freedesktop.StatusNotifierWatcher")
        self.assertTrue(item.available)
        self.assertEqual(item.watcher, "org.freedesktop.StatusNotifierWatcher")
        properties = self.bus.invoke(tray.SNI_PATH, tray.PROPERTIES, "GetAll",
                                     tray.SNI_FREEDESKTOP)[0]
        self.assertEqual(properties["Menu"].value, tray.MENU_PATH)
        self.bus.invoke(tray.SNI_PATH, tray.SNI_FREEDESKTOP, "Activate", 0, 0)
        self.assertEqual(self.done, ["open_log"])
        item.set_paused(True)
        self.assertIn((tray.SNI_PATH, tray.SNI_FREEDESKTOP, "NewOverlayIcon",
                       []), self.bus.emitted)

    def test_when_one_watcher_goes_the_other_takes_it(self):
        item = self.tray()
        self.bus.watchers.add("org.freedesktop.StatusNotifierWatcher")
        self.bus.owner_changed(":1.30", "")
        self.assertTrue(item.registered)
        self.assertEqual(item.watcher, "org.freedesktop.StatusNotifierWatcher")

    def test_the_icon_is_there_as_pixels_for_a_host_without_the_name(self):
        """The specification's fallback: without it, a host whose icon
        theme has no `folder` draws an empty square."""
        item = self.tray()
        properties = self.bus.invoke(tray.SNI_PATH, tray.PROPERTIES,
                                     "GetAll", tray.SNI)[0]
        pixmaps = properties["IconPixmap"].value
        self.assertEqual([width for width, _height, _data in pixmaps],
                         list(tray.PIXMAP_SIZES))
        for width, height, data in pixmaps:
            self.assertEqual(len(data), width * height * 4)
            alphas = data[0::4]
            self.assertTrue(0 < alphas.count(0xFF) < width * height)
        self.assertEqual(properties["OverlayIconPixmap"].value, [])
        item.set_paused(True)
        overlay = self.bus.invoke(tray.SNI_PATH, tray.PROPERTIES, "Get",
                                  tray.SNI, "OverlayIconPixmap")[0].value
        self.assertEqual(len(overlay), len(tray.PIXMAP_SIZES))

    def test_the_report_says_what_the_host_asked_for(self):
        """A desktop that shows no menu because it asks the item to draw
        one itself is told apart, in a report, from one that never asked."""
        item = self.tray()
        self.bus.invoke(tray.SNI_PATH, tray.PROPERTIES, "GetAll", tray.SNI)
        self.bus.invoke(tray.SNI_PATH, tray.SNI, "ContextMenu", 10, 10)
        report = item.report()
        self.assertEqual(report["watcher"], "org.kde.StatusNotifierWatcher")
        self.assertEqual(report["host_asked"],
                         {"ContextMenu": 1, "GetAll": 1})

    def test_no_watcher_yet_is_a_reason_and_an_icon_later(self):
        """A login where auto-sort beat the desktop to it."""
        item = self.tray(watcher=False)
        self.assertFalse(item.available)
        self.assertIn("will appear", item.reason)
        self.bus.owner_changed("", ":1.40")
        self.assertTrue(item.registered)

    def test_a_bus_that_breaks_never_reaches_the_daemon(self):
        item = self.tray()
        self.bus.pump = mock.Mock(side_effect=dbuswire.DBusError("gone"))
        item.pump(0.25)
        self.assertTrue(self.bus.closed)
        item.pump(0.25)                        # and stays quiet
        item.set_paused(True)

    def test_introspection_describes_what_is_there(self):
        import xml.etree.ElementTree as ElementTree
        self.tray()
        for path, interface in ((tray.SNI_PATH, tray.SNI),
                                (tray.MENU_PATH, tray.MENU)):
            text = self.bus.invoke(path, "org.freedesktop.DBus."
                                   "Introspectable", "Introspect")[0]
            names = [node.get("name") for node
                     in ElementTree.fromstring(text.split("\n", 2)[2])]
            self.assertIn(interface, names)

    def test_a_bus_that_will_not_connect_is_headless(self):
        with mock.patch("tray.sys.platform", "linux"), \
                mock.patch("dbuswire.Connection",
                           side_effect=dbuswire.DBusError(
                               "cannot reach the session bus")):
            item = tray.create({})
        self.assertFalse(item.available)
        self.assertIn("session bus", item.reason)



class AnyProgramsMenu(unittest.TestCase):
    """The tray is auto-sort's, but nothing in it is: a `Look` says what the
    icon is called and what its menu holds, and every backend draws that.
    This is what lets tools-core carry the same file for other programs."""

    def setUp(self):
        self.done = []
        self.actions = dict((name, (lambda name=name: self.done.append(name)))
                            for name in ("open", "pause", "quit"))
        self.look = tray.Look("My Backups", [
            tray.Entry("open", "Open the folder"),
            tray.Entry("pause", "Pause", paused_label="Resume"),
            None,
            tray.Entry("quit", "Quit"),
        ], click="open", status="Idle", status_paused="Held")

    def linux(self, look):
        self.bus = FakeBus()
        return tray._linux_tray(self.actions, connect=lambda: self.bus,
                                look=look)

    def labels(self):
        _revision, (_id, _properties, children) = self.bus.invoke(
            tray.MENU_PATH, tray.MENU, "GetLayout", 0, -1, [])
        return [child.value[1].get("label", child.value[1].get("type")).value
                for child in children]

    def test_the_menu_is_the_one_it_was_given(self):
        item = self.linux(self.look)
        self.assertEqual(self.labels(),
                         ["Open the folder", "Pause", "separator", "Quit"])
        for number in (1, 2, 4):
            self.bus.invoke(tray.MENU_PATH, tray.MENU, "Event", number,
                            "clicked", dbuswire.Variant("s", ""), 0)
        self.bus.invoke(tray.SNI_PATH, tray.SNI, "Activate", 1, 1)
        self.assertEqual(self.done, ["open", "pause", "quit", "open"])
        item.set_paused(True)
        self.assertEqual(self.labels()[1], "Resume")
        properties = self.bus.invoke(tray.SNI_PATH, tray.PROPERTIES,
                                     "GetAll", tray.SNI)[0]
        self.assertEqual(properties["Title"].value, "My Backups")
        self.assertEqual(properties["Id"].value, "my-backups")
        self.assertEqual(properties["ToolTip"].value[2:], ("My Backups",
                                                           "Held"))
        self.assertFalse(properties["ItemIsMenu"].value)

    def test_without_a_click_action_a_click_is_the_menu(self):
        look = tray.Look("Menu only", [tray.Entry("quit", "Quit")])
        self.linux(look)
        properties = self.bus.invoke(tray.SNI_PATH, tray.PROPERTIES,
                                     "GetAll", tray.SNI)[0]
        self.assertTrue(properties["ItemIsMenu"].value)
        self.bus.invoke(tray.SNI_PATH, tray.SNI, "Activate", 1, 1)
        self.assertEqual(self.done, [])

    def test_a_menu_it_cannot_draw_is_refused_when_described(self):
        with self.assertRaises(ValueError):
            tray.Look("Empty", [None])
        with self.assertRaises(ValueError):
            tray.Look("Long", [tray.Entry("x", str(n)) for n in range(33)])

    def test_the_one_line_tooltip(self):
        self.assertEqual(self.look.tooltip(False), "My Backups")
        self.assertEqual(self.look.tooltip(True), "My Backups (held)")
        self.assertEqual(tray.AUTO_SORT.tooltip(True), "auto-sort (paused)")

    def test_the_native_menu_follows_the_look(self):
        if sys.platform != "darwin":
            self.skipTest("the native status item is macOS only")
        item = tray.create(self.actions, self.look)
        try:
            self.assertTrue(item.available, getattr(item, "reason", ""))
            self.assertEqual(item.runtime.send(
                item.runtime.ctypes.c_long, item.menu, "numberOfItems"), 4)
            item._clicked()
            item._run_entry(3)
            item.set_paused(True)
            self.assertEqual(self.done, ["open", "quit"])
        finally:
            item.close()


if __name__ == "__main__":
    unittest.main()
