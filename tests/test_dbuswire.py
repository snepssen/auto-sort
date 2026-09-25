"""The D-Bus wire protocol, as `dbuswire` writes and reads it.

The byte strings here are the fixtures. The ones this module writes are
worked out by hand from the specification -- every length, every padding
byte -- rather than produced by the code under test. The ones it reads
were recorded from a real Plasma session: what the bus daemon and Plasma
itself sent, so the reader is held to what other implementations write,
not only to what this one does.
"""

from __future__ import annotations

import os
import socket
import struct
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dbuswire                                          # noqa: E402
from dbuswire import Variant                             # noqa: E402

# Recorded on a Steam Deck in Desktop Mode (Plasma 6.7.3): the bus's reply
# to Hello, the NameAcquired signal that follows it, and Plasma's reply to
# org.freedesktop.Notifications.GetServerInformation.
HELLO_REPLY = (
    b"l\x02\x01\x01\x0b\x00\x00\x00\xff\xff\xff\xff?\x00\x00\x00"
    b"\x05\x01u\x00\x01\x00\x00\x00\x07\x01s\x00\x14\x00\x00\x00"
    b"org.freedesktop.DBus\x00\x00\x00\x00\x06\x01s\x00\x06\x00\x00\x00"
    b":1.710\x00\x00\x08\x01g\x00\x01s\x00\x00\x06\x00\x00\x00:1.710\x00")
NAME_ACQUIRED = (
    b"l\x04\x01\x01\x0b\x00\x00\x00\xff\xff\xff\xff\x8f\x00\x00\x00"
    b"\x07\x01s\x00\x14\x00\x00\x00org.freedesktop.DBus\x00\x00\x00\x00"
    b"\x06\x01s\x00\x06\x00\x00\x00:1.710\x00\x00\x01\x01o\x00\x15\x00\x00"
    b"\x00/org/freedesktop/DBus\x00\x00\x00\x02\x01s\x00\x14\x00\x00\x00"
    b"org.freedesktop.DBus\x00\x00\x00\x00\x03\x01s\x00\x0c\x00\x00\x00"
    b"NameAcquired\x00\x00\x00\x00\x08\x01g\x00\x01s\x00\x00\x06\x00\x00"
    b"\x00:1.710\x00")
SERVER_INFORMATION = (
    b"l\x02\x01\x01(\x00\x00\x00\x03\x06\x00\x007\x00\x00\x00"
    b"\x06\x01s\x00\x06\x00\x00\x00:1.710\x00\x00\x05\x01u\x00\x02\x00\x00"
    b"\x00\x08\x01g\x00\x04ssss\x00\x00\x00\x00\x00\x00\x00\x07\x01s\x00"
    b"\x06\x00\x00\x00:1.500\x00\x00\x06\x00\x00\x00Plasma\x00\x00\x03\x00"
    b"\x00\x00KDE\x00\x05\x00\x00\x006.7.3\x00\x00\x00\x03\x00\x00\x001.2"
    b"\x00")


def u32(value):
    return struct.pack("<I", value)


class Writing(unittest.TestCase):
    """Each expected value is laid out by hand, padding included."""

    def test_a_string_is_length_bytes_and_a_nul(self):
        self.assertEqual(dbuswire.marshal("s", ["abc"]),
                         u32(3) + b"abc\0")

    def test_a_uint32_after_a_byte_is_aligned_to_four(self):
        self.assertEqual(dbuswire.marshal("yu", [1, 7]),
                         b"\x01\0\0\0" + u32(7))

    def test_a_boolean_is_four_bytes(self):
        self.assertEqual(dbuswire.marshal("b", [True]), u32(1))

    def test_a_signature_has_a_one_byte_length(self):
        self.assertEqual(dbuswire.marshal("g", ["a{sv}"]), b"\x05a{sv}\0")

    def test_an_array_length_does_not_count_its_leading_padding(self):
        # Length at 0, padding to the int64's boundary at 8, one element.
        self.assertEqual(dbuswire.marshal("ax", [[1]]),
                         u32(8) + b"\0" * 4 + struct.pack("<q", 1))

    def test_an_empty_array_is_still_padded_to_its_element(self):
        self.assertEqual(dbuswire.marshal("ax", [[]]), u32(0) + b"\0" * 4)

    def test_a_dictionary_of_variants(self):
        # a{sv} {"k": <uint32 7>}: length, pad to 8, the entry's key at 8,
        # the variant's signature at 14, its value aligned to 4 at 20.
        expected = (u32(16) + b"\0" * 4
                    + u32(1) + b"k\0"
                    + b"\x01u\0" + b"\0" * 3
                    + u32(7))
        self.assertEqual(
            dbuswire.marshal("a{sv}", [{"k": Variant("u", 7)}]), expected)

    def test_a_struct_is_aligned_to_eight(self):
        self.assertEqual(dbuswire.marshal("y(y)", [1, (2,)]),
                         b"\x01" + b"\0" * 7 + b"\x02")

    def test_hello_is_the_message_the_spec_describes(self):
        message = dbuswire.Message(
            dbuswire.METHOD_CALL, path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus", member="Hello",
            destination="org.freedesktop.DBus")
        fields = (
            # (PATH, <o "/org/freedesktop/DBus">): 30 bytes, 2 to pad.
            b"\x01\x01o\0" + u32(21) + b"/org/freedesktop/DBus\0" + b"\0" * 2
            # (INTERFACE, <s ...>): 29 bytes, 3 to pad.
            + b"\x02\x01s\0" + u32(20) + b"org.freedesktop.DBus\0" + b"\0" * 3
            # (MEMBER, <s "Hello">): 14 bytes, 2 to pad.
            + b"\x03\x01s\0" + u32(5) + b"Hello\0" + b"\0" * 2
            # (DESTINATION, <s ...>): 29 bytes, the last.
            + b"\x06\x01s\0" + u32(20) + b"org.freedesktop.DBus\0")
        self.assertEqual(len(fields), 109)
        expected = (b"l\x01\x00\x01" + u32(0) + u32(1) + u32(109) + fields
                    + b"\0" * 3)                 # the header, padded to 128
        self.assertEqual(message.encode(1), expected)

    def test_a_variant_needs_to_say_what_it_is(self):
        with self.assertRaises(TypeError):
            dbuswire.marshal("v", ["bare"])


class Reading(unittest.TestCase):
    """What a real bus and a real Plasma sent."""

    def test_the_bus_reply_to_hello(self):
        self.assertEqual(dbuswire.message_length(HELLO_REPLY),
                         len(HELLO_REPLY))
        message = dbuswire.decode(HELLO_REPLY)
        self.assertEqual(message.kind, dbuswire.METHOD_RETURN)
        self.assertEqual(message.reply_serial, 1)
        self.assertEqual(message.sender, "org.freedesktop.DBus")
        self.assertEqual(message.body, [":1.710"])

    def test_a_signal_from_the_bus(self):
        message = dbuswire.decode(NAME_ACQUIRED)
        self.assertEqual(message.kind, dbuswire.SIGNAL)
        self.assertEqual((message.interface, message.member),
                         ("org.freedesktop.DBus", "NameAcquired"))
        self.assertEqual(message.path, "/org/freedesktop/DBus")
        self.assertEqual(message.body, [":1.710"])

    def test_plasmas_reply_with_four_strings(self):
        message = dbuswire.decode(SERVER_INFORMATION)
        self.assertEqual(message.sender, ":1.500")
        self.assertEqual(message.signature, "ssss")
        self.assertEqual(message.body, ["Plasma", "KDE", "6.7.3", "1.2"])

    def test_two_messages_arriving_as_one_read(self):
        connection = dbuswire.Connection(sock=_Quiet(), authenticate=False)
        connection.buffer.extend(HELLO_REPLY + NAME_ACQUIRED[:40])
        self.assertEqual(len(connection._split()), 1)
        connection.buffer.extend(NAME_ACQUIRED[40:])
        self.assertEqual(connection._split()[0].member, "NameAcquired")

    def test_a_big_endian_message_is_read(self):
        # A method return from a big-endian peer, laid out by hand: one
        # REPLY_SERIAL field and a uint32 body of 42.
        fields = b"\x05\x01u\0" + struct.pack(">I", 9) \
            + b"\0" * 0 + b"\x08\x01g\0\x01u\0"
        header = (b"B\x02\x00\x01" + struct.pack(">III", 4, 3, len(fields))
                  + fields)
        data = header + b"\0" * (-len(header) % 8) + struct.pack(">I", 42)
        message = dbuswire.decode(data)
        self.assertEqual((message.reply_serial, message.body), (9, [42]))

    def test_a_message_too_large_is_refused_not_buffered(self):
        header = b"l\x02\x01\x01" + u32(dbuswire.MAX_MESSAGE) + u32(1) \
            + u32(0)
        with self.assertRaises(dbuswire.DBusError):
            dbuswire.message_length(header)


class RoundTrip(unittest.TestCase):

    def test_a_menu_layout_survives_the_wire(self):
        """The most nested thing the tray sends: u(ia{sv}av), where each
        child is a variant holding the same structure again."""
        child = (1, {"label": Variant("s", "Open log")}, [])
        layout = [7, (0, {"children-display": Variant("s", "submenu")},
                      [Variant("(ia{sv}av)", child)])]
        data = dbuswire.marshal("u(ia{sv}av)", layout)
        self.assertEqual(dbuswire.unmarshal("u(ia{sv}av)", data), layout)

    def test_bytes_and_negative_numbers(self):
        values = [b"\x00\xffpixels", -5, -2 ** 40, 1.5]
        data = dbuswire.marshal("ayixd", values)
        self.assertEqual(dbuswire.unmarshal("ayixd", data), values)

    def test_a_signature_is_split_into_complete_types(self):
        self.assertEqual(dbuswire.split_signature("sa{sv}(ii)av"),
                         ["s", "a{sv}", "(ii)", "av"])
        for broken in ("a", "(ii", "{sv", "z"):
            with self.assertRaises(ValueError):
                dbuswire.split_signature(broken)


class _Quiet(object):
    """A socket that is never read from."""

    def fileno(self):
        return -1

    def close(self):
        pass


class FakeBus(object):
    """The other end of a socketpair, playing the bus.

    Checks the handshake bytes, answers Hello, and for every other method
    call runs `script`, which may send whatever it likes -- including a
    call back to the client before the reply.
    """

    def __init__(self, script=None):
        self.client, self.server = socket.socketpair()
        self.script = script or (lambda bus, message: bus.reply(message, "", []))
        self.handshake = b""
        self.calls = []
        self.answers = []
        self.serial = 100
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def send(self, message):
        self.serial += 1
        self.server.sendall(message.encode(self.serial))
        return self.serial

    def reply(self, message, signature, body):
        self.send(dbuswire.Message(dbuswire.METHOD_RETURN,
                                   reply_serial=message.serial,
                                   destination=":1.9", signature=signature,
                                   body=body))

    def run(self):
        buffer = b""
        while b"BEGIN\r\n" not in buffer:
            try:
                chunk = self.server.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            if b"\r\n" in buffer and b"OK" not in self.handshake:
                self.handshake = buffer.split(b"\r\n")[0] + b"\r\n"
                self.server.sendall(b"OK 1234deadbeef\r\n")
                self.handshake += b"OK"
        buffer = buffer.split(b"BEGIN\r\n", 1)[1]
        while True:
            total = dbuswire.message_length(buffer)
            if total is None or len(buffer) < total:
                try:
                    chunk = self.server.recv(65536)
                except OSError:
                    return
                if not chunk:
                    return
                buffer += chunk
                continue
            message = dbuswire.decode(buffer[:total])
            buffer = buffer[total:]
            if message.kind == dbuswire.METHOD_CALL and \
                    message.member == "Hello":
                self.reply(message, "s", [":1.9"])
            elif message.kind == dbuswire.METHOD_CALL:
                self.calls.append(message)
                self.script(self, message)
            else:
                self.answers.append(message)

    def close(self):
        self.server.close()


class TheConnection(unittest.TestCase):

    def connect(self, script=None):
        bus = FakeBus(script)
        self.addCleanup(bus.close)
        connection = dbuswire.Connection(sock=bus.client, timeout=2.0)
        self.addCleanup(connection.close)
        return bus, connection

    def test_the_handshake_is_sasl_external_with_the_uid(self):
        bus, connection = self.connect()
        uid = str(os.getuid()).encode("ascii").hex().encode("ascii")
        self.assertEqual(bus.handshake,
                         b"\0AUTH EXTERNAL " + uid + b"\r\nOK")
        self.assertEqual(connection.unique_name, ":1.9")

    def test_a_call_answers_a_call_made_back_to_it_before_its_reply(self):
        """A host that asks for our properties before it replies to our
        registration would otherwise wait out the whole timeout."""
        def script(bus, message):
            # The call back first, then the reply: the client has to
            # answer the first while it waits for the second, since
            # nothing else is reading its socket.
            bus.send(dbuswire.Message(
                dbuswire.METHOD_CALL, path="/Item", member="Get",
                interface="test.Item", destination=":1.9"))
            bus.reply(message, "s", ["registered"])

        bus, connection = self.connect(script)
        connection.export("/Item", "test.Item", {
            "Get": lambda _message: ("s", ["here"])})
        started = time.monotonic()
        self.assertEqual(connection.call("x.Watcher", "/W", "x.Watcher",
                                         "Register"), ["registered"])
        self.assertLess(time.monotonic() - started, 1.5)
        deadline = time.monotonic() + 2
        while not bus.answers and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(bus.answers[0].body, ["here"])

    def test_an_error_reply_raises_with_its_name(self):
        def script(bus, message):
            bus.send(dbuswire.Message(
                dbuswire.ERROR, reply_serial=message.serial,
                error_name="org.freedesktop.DBus.Error.ServiceUnknown",
                signature="s", body=["nobody"]))
        _bus, connection = self.connect(script)
        with self.assertRaises(dbuswire.DBusError) as caught:
            connection.call("x", "/", "x", "Nothing")
        self.assertEqual(caught.exception.name,
                         "org.freedesktop.DBus.Error.ServiceUnknown")

    def test_an_unknown_method_is_answered_with_an_error(self):
        _bus, connection = self.connect()
        connection.export("/Item", "test.Item", {})
        bus = _bus
        bus.send(dbuswire.Message(dbuswire.METHOD_CALL, path="/Item",
                                  interface="test.Item", member="Missing",
                                  destination=":1.9"))
        connection.pump(0.3)
        deadline = time.monotonic() + 2
        while not bus.answers and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(bus.answers[0].error_name,
                         "org.freedesktop.DBus.Error.UnknownMethod")

    def test_pump_returns_within_its_time_slice(self):
        """The daemon's loop calls it four times a second."""
        _bus, connection = self.connect()
        started = time.monotonic()
        connection.pump(0.2)
        self.assertLess(time.monotonic() - started, 0.35)

    def test_a_bus_that_hangs_up_raises_and_closes(self):
        bus, connection = self.connect()
        bus.server.shutdown(socket.SHUT_RDWR)
        with self.assertRaises(dbuswire.DBusError):
            connection.pump(1.0)
        self.assertTrue(connection.closed)

    def test_signals_go_to_the_matches_that_accept_them(self):
        bus, connection = self.connect()
        heard = []
        connection.add_match("type='signal'",
                             lambda message: message.member == "Wanted",
                             heard.append)
        for member in ("Unwanted", "Wanted"):
            bus.send(dbuswire.Message(dbuswire.SIGNAL, path="/",
                                      interface="x", member=member))
        deadline = time.monotonic() + 2
        while not heard and time.monotonic() < deadline:
            connection.pump(0.05)
        self.assertEqual([message.member for message in heard], ["Wanted"])


class FindingTheBus(unittest.TestCase):

    def test_the_address_the_environment_names(self):
        with mock.patch.dict(os.environ, {
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/7/bus"}):
            self.assertEqual(dbuswire.session_address(),
                             "unix:path=/run/user/7/bus")

    def test_the_runtime_directory_when_the_variable_is_gone(self):
        directory = os.path.dirname(os.path.abspath(__file__))
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory},
                             clear=True), \
                mock.patch("os.path.exists", return_value=True):
            self.assertEqual(dbuswire.session_address(),
                             "unix:path=%s/bus" % directory)

    def test_no_bus_at_all_is_none_and_notify_does_nothing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(dbuswire.session_address())
            self.assertIsNone(dbuswire.notify("Nothing to see"))

    def test_socket_addresses(self):
        self.assertEqual(dbuswire._unix_socket("unix:path=/tmp/a%20b"),
                         "/tmp/a b")
        self.assertEqual(dbuswire._unix_socket(
            "tcp:host=x;unix:abstract=/tmp/dbus-XYZ,guid=1"), "\0/tmp/dbus-XYZ")
        with self.assertRaises(dbuswire.DBusError):
            dbuswire._unix_socket("tcp:host=localhost,port=1")


if __name__ == "__main__":
    unittest.main()
