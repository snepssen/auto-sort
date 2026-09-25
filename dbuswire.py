"""The D-Bus wire protocol, spoken with the standard library.

Linux desktops put tray icons, menus and notifications on the session bus,
and every Python binding for it -- dbus-python, PyGObject, jeepney -- is a
package somebody would have to install. That is the one thing this program
does not ask of anybody (see `tray.py` on PyObjC), so the protocol is
spoken here directly, the way the macOS tray speaks to the Objective-C
runtime through ctypes: a Unix socket, the SASL handshake, and the binary
message format. It is a small protocol and the specification is exact.

Only what auto-sort needs is here: calling a method and waiting for its
reply, answering calls made to objects this process exports, emitting and
receiving signals. No file descriptor passing, no big-endian writing
(messages from a big-endian peer are read correctly), no introspection of
anybody else's objects.

Nothing here may ever be the reason sorting stops. Every call has a
timeout, a broken connection raises `DBusError`, and the caller decides
what an absent desktop means -- which is always "carry on without it".
"""

from __future__ import annotations

import os
import select
import socket
import struct
import time

# Message types.
METHOD_CALL, METHOD_RETURN, ERROR, SIGNAL = 1, 2, 3, 4
NO_REPLY_EXPECTED = 0x1

# Header field codes.
PATH, INTERFACE, MEMBER, ERROR_NAME, REPLY_SERIAL, DESTINATION, SENDER, \
    SIGNATURE = 1, 2, 3, 4, 5, 6, 7, 8
_FIELD_TYPES = {PATH: "o", INTERFACE: "s", MEMBER: "s", ERROR_NAME: "s",
                REPLY_SERIAL: "u", DESTINATION: "s", SENDER: "s",
                SIGNATURE: "g", 9: "u"}

BUS = ("org.freedesktop.DBus", "/org/freedesktop/DBus",
       "org.freedesktop.DBus")

# A message larger than this is refused rather than buffered: the spec
# allows 128 MiB, and nothing a tray is sent comes near a megabyte.
MAX_MESSAGE = 16 * 1024 * 1024


class DBusError(Exception):
    """The bus is not there, went away, or answered with an error."""

    def __init__(self, message, name=None):
        Exception.__init__(self, message)
        self.name = name


class Variant(object):
    """A value that carries its own signature, as `v` does on the wire."""

    __slots__ = ("signature", "value")

    def __init__(self, signature, value):
        self.signature = signature
        self.value = value

    def __eq__(self, other):
        return isinstance(other, Variant) and \
            (self.signature, self.value) == (other.signature, other.value)

    def __ne__(self, other):
        return not self == other

    def __repr__(self):
        return "Variant(%r, %r)" % (self.signature, self.value)


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------

_FIXED = {"y": ("B", 1), "b": ("I", 4), "n": ("h", 2), "q": ("H", 2),
          "i": ("i", 4), "u": ("I", 4), "x": ("q", 8), "t": ("Q", 8),
          "d": ("d", 8), "h": ("I", 4)}
_STRINGS = "sog"


def split_signature(signature):
    """`"sa{sv}(ii)"` -> `["s", "a{sv}", "(ii)"]`: one complete type each."""
    types = []
    index = 0
    while index < len(signature):
        end = _complete(signature, index)
        types.append(signature[index:end])
        index = end
    return types


def _complete(signature, index):
    """Where the complete type starting at `index` ends."""
    if index >= len(signature):
        raise ValueError("signature ends early: %r" % signature)
    char = signature[index]
    if char in _FIXED or char in _STRINGS or char == "v":
        return index + 1
    if char == "a":
        return _complete(signature, index + 1)
    closing = {"(": ")", "{": "}"}.get(char)
    if closing is None:
        raise ValueError("unknown type %r in %r" % (char, signature))
    index += 1
    while index < len(signature) and signature[index] != closing:
        index = _complete(signature, index)
    if index >= len(signature):
        raise ValueError("unclosed %r in %r" % (char, signature))
    return index + 1


def _alignment(type_code):
    char = type_code[0]
    if char in _FIXED:
        return _FIXED[char][1]
    if char in "so" or char == "a":
        return 4
    if char in "(" "{":
        return 8
    return 1                                    # g, v


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

class _Writer(object):
    """Little-endian, aligned relative to the start of what is written.

    A message body starts on an 8-byte boundary, so aligning within the
    body is the same as aligning within the message.
    """

    def __init__(self):
        self.data = bytearray()

    def pad(self, alignment):
        self.data.extend(b"\0" * (-len(self.data) % alignment))

    def write(self, type_code, value):
        char = type_code[0]
        if char in _FIXED:
            code, size = _FIXED[char]
            self.pad(size)
            if char == "b":
                value = 1 if value else 0
            self.data.extend(struct.pack("<" + code, value))
        elif char in "so":
            encoded = value.encode("utf-8")
            self.pad(4)
            self.data.extend(struct.pack("<I", len(encoded)))
            self.data.extend(encoded + b"\0")
        elif char == "g":
            encoded = value.encode("ascii")
            self.data.extend(struct.pack("<B", len(encoded)))
            self.data.extend(encoded + b"\0")
        elif char == "v":
            if not isinstance(value, Variant):
                raise TypeError("a variant needs a Variant, not %r" % value)
            self.write("g", value.signature)
            self.write(value.signature, value.value)
        elif char == "a":
            self._array(type_code[1:], value)
        elif char in "({":
            self.pad(8)
            members = split_signature(type_code[1:-1])
            if char == "{":
                value = tuple(value)
            if len(members) != len(value):
                raise ValueError("%s needs %d values, got %r"
                                 % (type_code, len(members), value))
            for member, item in zip(members, value):
                self.write(member, item)
        else:
            raise ValueError("cannot write type %r" % type_code)

    def _array(self, element, value):
        self.pad(4)
        at = len(self.data)
        self.data.extend(b"\0\0\0\0")
        # The length does not count the padding before the first element,
        # which is there even when the array is empty.
        self.pad(_alignment(element))
        start = len(self.data)
        if element == "y":
            self.data.extend(bytes(value))
        elif element.startswith("{"):
            for pair in (value.items() if isinstance(value, dict) else value):
                self.write(element, pair)
        else:
            for item in value:
                self.write(element, item)
        struct.pack_into("<I", self.data, at, len(self.data) - start)


def marshal(signature, values):
    """The bytes of `values` laid out as `signature`, from offset zero."""
    writer = _Writer()
    types = split_signature(signature)
    if len(types) != len(values):
        raise ValueError("signature %r needs %d values, got %d"
                         % (signature, len(types), len(values)))
    for type_code, value in zip(types, values):
        writer.write(type_code, value)
    return bytes(writer.data)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

class _Reader(object):

    def __init__(self, data, offset=0, endian="<"):
        self.data = data
        self.offset = offset
        self.endian = endian

    def align(self, alignment):
        self.offset += -self.offset % alignment

    def take(self, count):
        if self.offset + count > len(self.data):
            raise DBusError("message is shorter than its contents")
        chunk = self.data[self.offset:self.offset + count]
        self.offset += count
        return chunk

    def read(self, type_code):
        char = type_code[0]
        if char in _FIXED:
            code, size = _FIXED[char]
            self.align(size)
            value = struct.unpack(self.endian + code, self.take(size))[0]
            return bool(value) if char == "b" else value
        if char in "so":
            self.align(4)
            length = struct.unpack(self.endian + "I", self.take(4))[0]
            text = self.take(length).decode("utf-8", "replace")
            self.take(1)
            return text
        if char == "g":
            length = self.take(1)[0]
            text = self.take(length).decode("ascii", "replace")
            self.take(1)
            return text
        if char == "v":
            signature = self.read("g")
            return Variant(signature, self.read(signature))
        if char == "a":
            return self._array(type_code[1:])
        if char in "({":
            self.align(8)
            values = tuple(self.read(member)
                           for member in split_signature(type_code[1:-1]))
            return values
        raise DBusError("cannot read type %r" % type_code)

    def _array(self, element):
        self.align(4)
        length = struct.unpack(self.endian + "I", self.take(4))[0]
        self.align(_alignment(element))
        end = self.offset + length
        if end > len(self.data):
            raise DBusError("array runs past the end of the message")
        if element == "y":
            return bytes(self.take(length))
        items = []
        while self.offset < end:
            items.append(self.read(element))
        if element.startswith("{"):
            return dict(items)
        return items


def unmarshal(signature, data, offset=0, endian="<"):
    reader = _Reader(data, offset, endian)
    return [reader.read(type_code) for type_code in split_signature(signature)]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

class Message(object):

    def __init__(self, kind, path=None, interface=None, member=None,
                 destination=None, signature="", body=(), flags=0,
                 error_name=None, reply_serial=None, sender=None, serial=0):
        self.kind = kind
        self.path = path
        self.interface = interface
        self.member = member
        self.destination = destination
        self.signature = signature
        self.body = list(body)
        self.flags = flags
        self.error_name = error_name
        self.reply_serial = reply_serial
        self.sender = sender
        self.serial = serial

    def encode(self, serial):
        self.serial = serial
        body = marshal(self.signature, self.body) if self.signature else b""
        fields = []
        for code, value in ((PATH, self.path), (INTERFACE, self.interface),
                            (MEMBER, self.member),
                            (ERROR_NAME, self.error_name),
                            (REPLY_SERIAL, self.reply_serial),
                            (DESTINATION, self.destination),
                            (SENDER, self.sender),
                            (SIGNATURE, self.signature or None)):
            if value is not None:
                fields.append((code, Variant(_FIELD_TYPES[code], value)))
        header = marshal("yyyyuua(yv)",
                         [ord("l"), self.kind, self.flags, 1, len(body),
                          serial, fields])
        return header + b"\0" * (-len(header) % 8) + body

    def __repr__(self):
        return "<Message %d %s %s.%s %r>" % (self.kind, self.path,
                                           self.interface, self.member,
                                           self.body)


def message_length(data):
    """Bytes the message at the front of `data` needs, or None if the
    fixed header has not all arrived yet."""
    if len(data) < 16:
        return None
    endian = {ord("l"): "<", ord("B"): ">"}.get(data[0])
    if endian is None:
        raise DBusError("not a D-Bus message (endian byte %r)" % data[0])
    body_length, _serial, fields_length = struct.unpack(
        endian + "III", bytes(data[4:16]))
    header = 16 + fields_length
    total = header + (-header % 8) + body_length
    if total > MAX_MESSAGE:
        raise DBusError("message of %d bytes refused" % total)
    return total


def decode(data):
    """A `Message` from exactly one message's bytes."""
    data = bytes(data)
    endian = {ord("l"): "<", ord("B"): ">"}[data[0]]
    reader = _Reader(data, 0, endian)
    _endian, kind, flags, _version, body_length, serial, fields = \
        [reader.read(code) for code in split_signature("yyyyuua(yv)")]
    found = dict((code, variant.value) for code, variant in fields)
    reader.align(8)
    signature = found.get(SIGNATURE, "")
    body = unmarshal(signature, data, reader.offset, endian) \
        if signature else []
    return Message(kind, path=found.get(PATH),
                   interface=found.get(INTERFACE), member=found.get(MEMBER),
                   destination=found.get(DESTINATION), signature=signature,
                   body=body, flags=flags, error_name=found.get(ERROR_NAME),
                   reply_serial=found.get(REPLY_SERIAL),
                   sender=found.get(SENDER), serial=serial)


# ---------------------------------------------------------------------------
# The connection
# ---------------------------------------------------------------------------

def session_address():
    """The session bus, as the environment names it.

    Falls back to `$XDG_RUNTIME_DIR/bus`, which is where systemd puts the
    user bus and what sd-bus itself falls back to, so a daemon whose
    environment lost the variable -- started from an SSH login into a
    machine with a desktop session, say -- still finds it.
    """
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    if address:
        return address
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.exists(os.path.join(runtime, "bus")):
        return "unix:path=" + os.path.join(runtime, "bus")
    return None


def _unix_socket(address):
    """The socket address for the first `unix:` entry `address` lists."""
    for entry in address.split(";"):
        transport, _, rest = entry.partition(":")
        if transport != "unix":
            continue
        options = dict(part.split("=", 1) for part in rest.split(",")
                       if "=" in part)
        if "path" in options:
            return _unescape(options["path"])
        if "abstract" in options:
            return "\0" + _unescape(options["abstract"])
    raise DBusError("no unix socket in bus address %r" % address)


def _unescape(value):
    out, index = [], 0
    while index < len(value):
        if value[index] == "%" and index + 2 < len(value):
            out.append(chr(int(value[index + 1:index + 3], 16)))
            index += 3
        else:
            out.append(value[index])
            index += 1
    return "".join(out)


class Connection(object):
    """One connection to a bus, driven by whoever owns it.

    Nothing runs in the background. `pump(seconds)` reads and dispatches
    what has arrived, for at most that long, and `call()` waits for its own
    reply while still answering calls made to exported objects -- a host
    that calls back before replying would otherwise wait out the timeout.
    """

    def __init__(self, address=None, timeout=2.0, sock=None,
                 authenticate=True):
        self.timeout = timeout
        self.serial = 0
        self.buffer = bytearray()
        self.pending = []           # messages read while waiting for a reply
        self.objects = {}           # path -> {interface: {member: handler}}
        self.matches = []           # (predicate, callback)
        self.unique_name = None
        self.closed = False
        if sock is None:
            address = address or session_address()
            if not address:
                raise DBusError("no session bus address")
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            try:
                sock.connect(_unix_socket(address))
            except OSError as error:
                sock.close()
                raise DBusError("cannot reach the session bus: %s" % error)
        self.sock = sock
        if authenticate:
            self._authenticate()
            self.unique_name = self.call(*BUS, member="Hello")[0]

    # -- the handshake ------------------------------------------------------

    def _authenticate(self):
        """SASL EXTERNAL: the kernel vouches for our uid over the socket.

        The uid goes as its decimal digits, hex-encoded, which is what the
        spec asks for and what every bus daemon checks.
        """
        uid = str(os.getuid()).encode("ascii").hex()
        try:
            self.sock.sendall(b"\0AUTH EXTERNAL " + uid.encode("ascii")
                              + b"\r\n")
            line = self._auth_line()
            if not line.startswith(b"OK "):
                raise DBusError("the bus refused us: %r" % line)
            self.sock.sendall(b"BEGIN\r\n")
        except OSError as error:
            raise DBusError("handshake with the bus failed: %s" % error)

    def _auth_line(self):
        deadline = time.monotonic() + self.timeout
        while b"\r\n" not in self.buffer:
            if time.monotonic() > deadline:
                raise DBusError("the bus did not answer the handshake")
            chunk = self.sock.recv(4096)
            if not chunk:
                raise DBusError("the bus hung up during the handshake")
            self.buffer.extend(chunk)
        line, _, rest = bytes(self.buffer).partition(b"\r\n")
        self.buffer = bytearray(rest)
        return line

    # -- sending ------------------------------------------------------------

    def send(self, message):
        if self.closed:
            raise DBusError("the connection is closed")
        self.serial += 1
        try:
            self.sock.settimeout(self.timeout)
            self.sock.sendall(message.encode(self.serial))
        except OSError as error:
            self._close()
            raise DBusError("could not write to the bus: %s" % error)
        return self.serial

    def call(self, destination, path, interface, member, signature="",
             body=(), timeout=None):
        """Call a method and return its reply's body, or raise DBusError."""
        serial = self.send(Message(METHOD_CALL, path=path,
                                   interface=interface, member=member,
                                   destination=destination,
                                   signature=signature, body=body))
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            for index, message in enumerate(self.pending):
                if message.reply_serial == serial and \
                        message.kind in (METHOD_RETURN, ERROR):
                    del self.pending[index]
                    if message.kind == ERROR:
                        raise DBusError("%s: %s" % (
                            message.error_name,
                            message.body[0] if message.body else ""),
                            message.error_name)
                    return message.body
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DBusError("no answer to %s.%s" % (interface, member))
            for message in self._read(remaining):
                if message.kind == METHOD_CALL:
                    self._dispatch(message)
                else:
                    self.pending.append(message)

    def emit(self, path, interface, member, signature="", body=()):
        self.send(Message(SIGNAL, path=path, interface=interface,
                          member=member, signature=signature, body=body))

    def add_match(self, rule, predicate, callback):
        """Ask the bus for signals matching `rule`, and hand each one that
        `predicate` accepts to `callback`."""
        self.matches.append((predicate, callback))
        self.call(*BUS, member="AddMatch", signature="s", body=[rule])

    # -- exporting ----------------------------------------------------------

    def export(self, path, interface, handlers):
        """`handlers` is {member: function(message) -> (signature, body)}."""
        self.objects.setdefault(path, {})[interface] = handlers

    # -- receiving ----------------------------------------------------------

    def pump(self, seconds):
        """Read and dispatch for at most `seconds`; never longer."""
        deadline = time.monotonic() + max(0.0, seconds)
        waiting, self.pending = self.pending, []
        for message in waiting:
            self._handle(message)
        while not self.closed:
            remaining = deadline - time.monotonic()
            messages = self._read(max(0.0, remaining))
            for message in messages:
                self._handle(message)
            if remaining <= 0 or not messages:
                return

    def _read(self, seconds):
        """Whatever complete messages arrive within `seconds`."""
        messages = self._split()
        if messages:
            return messages
        try:
            ready, _, _ = select.select([self.sock], [], [], seconds)
            if not ready:
                return []
            self.sock.settimeout(0)
            chunk = self.sock.recv(65536)
        except (OSError, ValueError) as error:
            self._close()
            raise DBusError("lost the bus: %s" % error)
        if not chunk:
            self._close()
            raise DBusError("the bus closed the connection")
        self.buffer.extend(chunk)
        return self._split()

    def _split(self):
        messages = []
        while True:
            total = message_length(self.buffer)
            if total is None or len(self.buffer) < total:
                return messages
            messages.append(decode(self.buffer[:total]))
            del self.buffer[:total]

    def _handle(self, message):
        if message.kind == METHOD_CALL:
            self._dispatch(message)
        elif message.kind == SIGNAL:
            for predicate, callback in list(self.matches):
                if predicate(message):
                    callback(message)

    def _dispatch(self, message):
        handlers = self.objects.get(message.path, {})
        handler = None
        if message.interface:
            handler = handlers.get(message.interface, {}).get(message.member)
        else:
            for members in handlers.values():
                handler = members.get(message.member) or handler
        if handler is None:
            if message.path in self.objects:
                self._reply_error(message, "org.freedesktop.DBus.Error."
                                  "UnknownMethod", "no method %s.%s"
                                  % (message.interface, message.member))
            else:
                self._reply_error(message, "org.freedesktop.DBus.Error."
                                  "UnknownObject", "no object at %s"
                                  % message.path)
            return
        try:
            signature, body = handler(message)
        except Exception as error:           # noqa: BLE001
            self._reply_error(message, "org.freedesktop.DBus.Error.Failed",
                              str(error))
            return
        if message.flags & NO_REPLY_EXPECTED:
            return
        self.send(Message(METHOD_RETURN, destination=message.sender,
                          reply_serial=message.serial, signature=signature,
                          body=body))

    def _reply_error(self, message, name, text):
        if message.flags & NO_REPLY_EXPECTED:
            return
        self.send(Message(ERROR, destination=message.sender,
                          reply_serial=message.serial, error_name=name,
                          signature="s", body=[text]))

    def fileno(self):
        return self.sock.fileno()

    def _close(self):
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass

    def close(self):
        self._close()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def notify(summary, body="", icon="folder", timeout=3.0):
    """Show a desktop notification; the id, or None where there is no
    desktop to show it on.

    For whoever clicked something and cannot see a terminal: the reason a
    menu entry did nothing belongs where they are looking.
    """
    try:
        connection = Connection(timeout=timeout)
    except DBusError:
        return None
    try:
        reply = connection.call(
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications", "Notify", "susssasa{sv}i",
            ["auto-sort", 0, icon, summary, body, [], {}, -1])
        return reply[0]
    except DBusError:
        return None
    finally:
        connection.close()
