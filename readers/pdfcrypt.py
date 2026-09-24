"""Reading a PDF that is encrypted but opens without a password.

Payroll portals, banks and phone companies encrypt what they send, and
almost always with an empty password to open it: the encryption is there to
forbid printing or editing, not to stop anybody reading. Every PDF viewer
opens those files without asking. To this reader they were bytes -- 19
payslips on one machine had no heading, no producer, not a word -- and were
filed as though nothing could be known about them.

This handles exactly that case and nothing more. The key is derived from
the empty password and checked against the file's own check value before a
byte is decrypted; a file that needs a real password is left unread, and
no password is ever guessed. Standard security handler, revisions 2 to 4:
RC4 of 40 to 128 bits and AES-128. The AES-256 of revision 6 is not here
yet, and such a file reads as before -- not at all.

AES is not in Python's standard library and this program depends on
nothing else, so the decryption is written out below. It is slow by the
standards of a C library and fast enough for what it is asked to do: the
pages of a document, never its pictures.
"""

from __future__ import annotations

import hashlib
import re
import struct

# The 32 bytes every password is padded with (PDF 1.7, 7.6.3.3).
_PAD = bytes.fromhex(
    "28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")

_ENCRYPT_REF = re.compile(rb"/Encrypt\s+(\d+)\s+(\d+)\s+R")
_ENCRYPT_INLINE = re.compile(rb"/Encrypt\s*<<")
_ID = re.compile(rb"/ID\s*\[\s*<([0-9A-Fa-f\s]*)>")
_ID_LITERAL = re.compile(rb"/ID\s*\[\s*\(")
_OBJ = re.compile(rb"(?<![0-9])(\d{1,7})[ \t\r\n]+(\d{1,5})[ \t\r\n]+obj\b")
_STREAM = re.compile(rb"(?<!end)stream(?:\r\n|\n|\r)")
_LENGTH = re.compile(rb"/Length\s+(\d+)(?!\s+\d+\s+R)")
# Streams that are never decrypted, or not worth decrypting here.
_LEAVE = re.compile(rb"/Type\s*/XRef\b|/Subtype\s*/Image\b")

# How much this will decrypt from one file. The page's own text and fonts
# are a few hundred kilobytes at most; this is well past that and still
# only a second or two of pure-Python AES.
MAX_DECRYPT = 2 * 1024 * 1024
# On the way to OCR the page is a picture, and a picture is megabytes. A
# few seconds, once: what OCR says is kept.
MAX_DECRYPT_PICTURES = 24 * 1024 * 1024


class Handler(object):
    """What is needed to decrypt one file's objects."""

    def __init__(self, key, aes, stream_method, string_method):
        self.key = key
        self.aes = aes
        self.stream_method = stream_method
        self.string_method = string_method

    def object_key(self, number, generation, aes=None):
        aes = self.aes if aes is None else aes
        seed = (self.key + struct.pack("<I", number)[:3]
                + struct.pack("<I", generation)[:2]
                + (b"sAlT" if aes else b""))
        return hashlib.md5(seed).digest()[:min(len(self.key) + 5, 16)]

    def decrypt(self, number, generation, data, method=None):
        method = method or self.stream_method
        if method == "Identity":
            return data
        aes = method == "AESV2"
        key = self.object_key(number, generation, aes)
        if aes:
            return aes_cbc_decrypt(key, data)
        return rc4(key, data)


def handler(data, passwords=None):
    """A `Handler` if `data` is encrypted and can be opened.

    With no password first, which is how most of them open; then with each
    of `passwords` -- the owner's own, from the Keychain (see `known`) --
    as the password that opens the file and as the one that owns it.

    None for a file that is not encrypted, one whose password is not
    known, and one this does not know how to read -- the three are the same
    answer to the caller, which is "read it as it is".
    """
    if passwords is None:
        passwords = known()
    dictionary = _encrypt_dictionary(data)
    if dictionary is None:
        return None
    if not re.search(rb"/Filter\s*/Standard\b", dictionary):
        return None
    revision = _int(rb"/R", dictionary)
    version = _int(rb"/V", dictionary) or 0
    if revision not in (2, 3, 4):
        return None
    owner = _string_value(rb"/O", dictionary)
    user = _string_value(rb"/U", dictionary)
    permissions = _int(rb"/P", dictionary)
    if owner is None or user is None or permissions is None:
        return None
    ident = _first_id(data)
    if ident is None:
        return None

    stream_method = string_method = "V2"
    length = 40
    if version >= 2:
        length = _int(rb"/Length", dictionary) or 40
    if version == 4:
        filters = _crypt_filters(dictionary)
        stream_method = filters.get(_name(rb"/StmF", dictionary) or "Identity",
                                    "Identity")
        string_method = filters.get(_name(rb"/StrF", dictionary) or "Identity",
                                    "Identity")
        length = 128
        if "AESV2" not in (stream_method, string_method) and \
                "V2" not in (stream_method, string_method):
            return None
    if length % 8 or not 40 <= length <= 128:
        return None
    size = 5 if revision == 2 else length // 8
    metadata = not re.search(rb"/EncryptMetadata\s+false", dictionary)

    def file_key(padded):
        # Algorithm 2: the file key, from a padded user password.
        digest = hashlib.md5(
            padded + owner[:32] + struct.pack("<i", _signed(permissions))
            + ident + (b"\xff\xff\xff\xff" if revision >= 4 and not metadata
                       else b"")).digest()
        if revision >= 3:
            for _round in range(50):
                digest = hashlib.md5(digest[:size]).digest()
        return digest[:size]

    def opens(key):
        # Algorithms 4 and 5: does this key produce the file's check value?
        if revision == 2:
            return rc4(key, _PAD) == user[:32]
        check = hashlib.md5(_PAD + ident).digest()
        for count in range(20):
            check = rc4(bytes(byte ^ count for byte in key), check)
        return check == user[:16]

    def as_owner(padded):
        # Algorithm 7: the owner password unlocks the user password.
        digest = hashlib.md5(padded).digest()
        if revision >= 3:
            for _round in range(50):
                digest = hashlib.md5(digest).digest()
        owner_key = digest[:size]
        found = owner[:32]
        if revision == 2:
            return rc4(owner_key, found)
        for count in range(19, -1, -1):
            found = rc4(bytes(byte ^ count for byte in owner_key), found)
        return found

    for padded in [_PAD] + [_padded(word) for word in passwords]:
        for candidate in (padded, as_owner(padded) if padded != _PAD
                          else None):
            if candidate is None:
                continue
            key = file_key(candidate)
            if opens(key):
                return Handler(key, stream_method == "AESV2", stream_method,
                               string_method)
    return None


def _padded(password):
    raw = password.encode("utf-8") if isinstance(password, str) else password
    return (raw + _PAD)[:32]


# ---------------------------------------------------------------------------
# The owner's own passwords
# ---------------------------------------------------------------------------

# Where they are kept: a generic password in the login Keychain. Never in
# the rules file, the ledger or a log -- the rules file is plain text that
# gets shared, and the ledger outlives any one file.
KEYCHAIN_SERVICE = "auto-sort PDF passwords"

_known = None


def known(refresh=False):
    """The passwords the owner has given for their own PDFs. Often none.

    One per line in a single Keychain item, read once per process. The
    Keychain asks the owner before handing it to a program it has not
    allowed; asking is its job, not this one's. Anything but a Mac, or no
    item, is an empty list and the same behaviour as before.
    """
    global _known
    if _known is not None and not refresh:
        return _known
    _known = []
    import subprocess
    import sys
    if sys.platform != "darwin":
        return _known
    try:
        done = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s",
             KEYCHAIN_SERVICE, "-w"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return _known
    if done.returncode == 0:
        text = done.stdout.decode("utf-8", "replace").rstrip("\n")
        _known = [line for line in text.split("\n") if line]
    return _known


def forget():
    """For tests, and after the owner changes what is kept."""
    global _known
    _known = None


def decrypted(data, found=None, pictures=False):
    """`data` with the streams it needs decrypted, or None.

    Every stream keeps its place in the file and its dictionary; only its
    bytes are replaced. The reader finds streams by looking rather than by
    trusting lengths, so a stream that comes out shorter changes nothing
    for it. Pictures are left as they are: they are the bulk of a file and
    never where its words are -- unless `pictures`, which is how OCR gets a
    scanned page out of an encrypted file.
    """
    found = found or handler(data)
    if found is None:
        return None
    pieces = []
    at = 0
    spent = 0
    for match in _OBJ.finditer(data):
        if match.start() < at:
            continue
        end = data.find(b"endobj", match.end())
        if end == -1:
            break
        start = _STREAM.search(data, match.end(), end)
        if not start:
            continue
        dictionary = data[match.end():start.start()]
        if _LEAVE.search(dictionary) and not (
                pictures and not re.search(rb"/Type\s*/XRef\b", dictionary)):
            continue
        body_end = _stream_end(data, start.end(), end, dictionary)
        if body_end is None:
            continue
        body = data[start.end():body_end]
        if spent + len(body) > (MAX_DECRYPT_PICTURES if pictures
                                else MAX_DECRYPT):
            break
        spent += len(body)
        try:
            plain = found.decrypt(int(match.group(1)), int(match.group(2)),
                                  body)
        except (ValueError, IndexError):
            continue
        pieces.append(data[at:start.end()])
        pieces.append(plain)
        at = body_end
    pieces.append(data[at:])
    return b"".join(pieces)


_INFO_REF = re.compile(rb"/Info\s+(\d+)\s+(\d+)\s+R")
_INFO_KEY = re.compile(rb"/(Producer|Creator|Title|Author|CreationDate|ModDate)"
                       rb"\s*([(<])")


def info(data, found):
    """`{key: raw bytes}` of the document information, decrypted.

    The producer, creator and title are encrypted like everything else, and
    read without decrypting they were recorded as they came: a producer of
    `\xbc\xf7-,\xa9...` on nineteen payslips.
    """
    refs = list(_INFO_REF.finditer(data))
    if not refs:
        return {}
    number, generation = refs[-1].group(1), refs[-1].group(2)
    headers = list(re.finditer(rb"(?<![0-9])" + number + rb"\s+"
                               + generation + rb"\s+obj\b", data))
    if not headers:
        return {}
    start = headers[-1].end()
    end = data.find(b"endobj", start)
    body = data[start:end if end != -1 else start + 4096]
    values = {}
    for match in _INFO_KEY.finditer(body):
        opener = match.start(2)
        if match.group(2) == b"<":
            close = body.find(b">", opener)
            try:
                raw = bytes.fromhex(re.sub(rb"\s", b"",
                                           body[opener + 1:close]).decode())
            except ValueError:
                continue
        else:
            raw, _end = _literal(body, opener)
        values[match.group(1).decode("ascii").lower()] = decrypt_string(
            found, int(number), int(generation), raw)
    return values


def decrypt_string(found, number, generation, raw):
    """One string from an object, such as the producer in the info record."""
    if found is None or found.string_method == "Identity":
        return raw
    try:
        return found.decrypt(number, generation, raw, found.string_method)
    except (ValueError, IndexError):
        return raw


def _stream_end(data, start, limit, dictionary):
    length = _LENGTH.search(dictionary)
    if length:
        end = start + int(length.group(1))
        if end <= limit and data[end:end + 20].lstrip().startswith(
                b"endstream"):
            return end
    end = data.rfind(b"endstream", start, limit)
    if end == -1:
        return None
    # The line ending before `endstream` is not part of the stream.
    while end > start and data[end - 1:end] in (b"\n", b"\r"):
        end -= 1
    return end


# ---------------------------------------------------------------------------
# Reading the encryption dictionary
# ---------------------------------------------------------------------------

def _encrypt_dictionary(data):
    refs = list(_ENCRYPT_REF.finditer(data))
    if refs:
        number = refs[-1].group(1)
        headers = list(re.finditer(rb"(?<![0-9])" + number
                                   + rb"\s+\d+\s+obj\b", data))
        if not headers:
            return None
        start = headers[-1].end()
        end = data.find(b"endobj", start)
        return data[start:end if end != -1 else start + 4096]
    inline = list(_ENCRYPT_INLINE.finditer(data))
    if inline:
        start = inline[-1].end()
        return data[start:start + 4096]
    return None


def _int(name, dictionary):
    match = re.search(re.escape(name) + rb"\s+(-?\d+)", dictionary)
    return int(match.group(1)) if match else None


def _name(name, dictionary):
    match = re.search(re.escape(name) + rb"\s*/(\w+)", dictionary)
    return match.group(1).decode("latin-1") if match else None


def _signed(value):
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def _crypt_filters(dictionary):
    """`{filter name: method}` from `/CF`, plus the built-in Identity."""
    filters = {"Identity": "Identity"}
    block = re.search(rb"/CF\s*<<(.*)", dictionary, re.S)
    if not block:
        return filters
    for match in re.finditer(rb"/(\w+)\s*<<([^<>]*)>>", block.group(1)):
        method = _name(rb"/CFM", match.group(2))
        if method in ("V2", "AESV2", "None"):
            filters[match.group(1).decode("latin-1")] = \
                "Identity" if method == "None" else method
    return filters


def _first_id(data):
    matches = list(_ID.finditer(data))
    if matches:
        hexed = re.sub(rb"\s", b"", matches[-1].group(1))
        if len(hexed) % 2:
            hexed += b"0"
        try:
            return bytes.fromhex(hexed.decode("ascii"))
        except ValueError:
            return None
    literal = list(_ID_LITERAL.finditer(data))
    if literal:
        value, _end = _literal(data, literal[-1].end() - 1)
        return value
    return None


def _string_value(name, dictionary):
    match = re.search(re.escape(name) + rb"\s*([(<])", dictionary)
    if not match:
        return None
    if match.group(1) == b"<":
        end = dictionary.find(b">", match.end())
        if end == -1:
            return None
        hexed = re.sub(rb"\s", b"", dictionary[match.end():end])
        if len(hexed) % 2:
            hexed += b"0"
        try:
            return bytes.fromhex(hexed.decode("ascii"))
        except ValueError:
            return None
    value, _end = _literal(dictionary, match.start(1))
    return value


_ESCAPES = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t",
            ord("b"): b"\b", ord("f"): b"\f", ord("("): b"(",
            ord(")"): b")", ord("\\"): b"\\"}


def _literal(data, start):
    """A `( ... )` string at `start`, escapes and nesting honoured."""
    out = bytearray()
    depth = 0
    index = start
    while index < len(data):
        byte = data[index]
        if byte == 0x5C:                             # backslash
            index += 1
            if index >= len(data):
                break
            code = data[index]
            if code in _ESCAPES:
                out += _ESCAPES[code]
            elif 0x30 <= code <= 0x37:
                digits = data[index:index + 3]
                count = 1
                while count < len(digits) and 0x30 <= digits[count] <= 0x37:
                    count += 1
                out.append(int(digits[:count], 8) & 0xFF)
                index += count - 1
            elif code in (0x0A, 0x0D):
                if code == 0x0D and data[index + 1:index + 2] == b"\n":
                    index += 1
            else:
                out.append(code)
        elif byte == 0x28:                           # (
            if depth:
                out.append(byte)
            depth += 1
        elif byte == 0x29:                           # )
            depth -= 1
            if depth == 0:
                return bytes(out), index + 1
            out.append(byte)
        else:
            out.append(byte)
        index += 1
    return bytes(out), index


# ---------------------------------------------------------------------------
# RC4
# ---------------------------------------------------------------------------

def rc4(key, data):
    state = list(range(256))
    j = 0
    length = len(key)
    for i in range(256):
        j = (j + state[i] + key[i % length]) & 0xFF
        state[i], state[j] = state[j], state[i]
    out = bytearray(len(data))
    i = j = 0
    for index, byte in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + state[i]) & 0xFF
        state[i], state[j] = state[j], state[i]
        out[index] = byte ^ state[(state[i] + state[j]) & 0xFF]
    return bytes(out)


# ---------------------------------------------------------------------------
# AES, decryption only (FIPS-197)
# ---------------------------------------------------------------------------

def _tables():
    sbox = [0] * 256
    p = q = 1
    while True:
        # Multiply p by 3 and divide q by 3 in GF(2^8).
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        rotated = q ^ _rotl8(q, 1) ^ _rotl8(q, 2) ^ _rotl8(q, 3) \
            ^ _rotl8(q, 4)
        sbox[p] = rotated ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    inverse = [0] * 256
    for index, value in enumerate(sbox):
        inverse[value] = index
    return sbox, inverse


def _rotl8(value, shift):
    return ((value << shift) | (value >> (8 - shift))) & 0xFF


def _xtime(value):
    value <<= 1
    return (value ^ 0x1B) & 0xFF if value & 0x100 else value


def _multiply(a, b):
    result = 0
    while b:
        if b & 1:
            result ^= a
        a = _xtime(a)
        b >>= 1
    return result


_SBOX, _INV_SBOX = _tables()
# One table per column position, so a round is lookups and exclusive-ors.
_INV_T = []
for _shift in range(4):
    _table = []
    for _value in range(256):
        _s = _INV_SBOX[_value]
        _word = ((_multiply(_s, 14) << 24) | (_multiply(_s, 9) << 16)
                 | (_multiply(_s, 13) << 8) | _multiply(_s, 11))
        _table.append(((_word >> (8 * _shift)) | (_word << (32 - 8 * _shift)))
                      & 0xFFFFFFFF)
    _INV_T.append(_table)
_INV_MIX = [[_multiply(_v, m) for _v in range(256)] for m in (14, 9, 13, 11)]
del _shift, _table, _value, _s, _word


def _expand(key):
    words = len(key) // 4
    rounds = words + 6
    schedule = [struct.unpack(">I", key[4 * i:4 * i + 4])[0]
                for i in range(words)]
    rcon = 1
    for i in range(words, 4 * (rounds + 1)):
        temp = schedule[i - 1]
        if i % words == 0:
            temp = ((temp << 8) | (temp >> 24)) & 0xFFFFFFFF
            temp = ((_SBOX[temp >> 24] << 24) | (_SBOX[(temp >> 16) & 0xFF] << 16)
                    | (_SBOX[(temp >> 8) & 0xFF] << 8) | _SBOX[temp & 0xFF])
            temp ^= rcon << 24
            rcon = _xtime(rcon)
        elif words > 6 and i % words == 4:
            temp = ((_SBOX[temp >> 24] << 24) | (_SBOX[(temp >> 16) & 0xFF] << 16)
                    | (_SBOX[(temp >> 8) & 0xFF] << 8) | _SBOX[temp & 0xFF])
        schedule.append(schedule[i - words] ^ temp)
    # The equivalent inverse cipher wants the middle round keys run through
    # InvMixColumns, so decryption has the same shape as encryption.
    decrypting = []
    for round_number in range(rounds, -1, -1):
        block = schedule[4 * round_number:4 * round_number + 4]
        if 0 < round_number < rounds:
            block = [_inv_mix_word(word) for word in block]
        decrypting.append(block)
    return decrypting, rounds


def _inv_mix_word(word):
    a, b, c, d = (word >> 24, (word >> 16) & 0xFF, (word >> 8) & 0xFF,
                  word & 0xFF)
    m14, m9, m13, m11 = _INV_MIX
    return (((m14[a] ^ m11[b] ^ m13[c] ^ m9[d]) << 24)
            | ((m9[a] ^ m14[b] ^ m11[c] ^ m13[d]) << 16)
            | ((m13[a] ^ m9[b] ^ m14[c] ^ m11[d]) << 8)
            | (m11[a] ^ m13[b] ^ m9[c] ^ m14[d]))


def _decrypt_block(keys, rounds, block):
    t0, t1, t2, t3 = _INV_T
    inv = _INV_SBOX
    k = keys[0]
    s0, s1, s2, s3 = struct.unpack(">4I", block)
    s0 ^= k[0]
    s1 ^= k[1]
    s2 ^= k[2]
    s3 ^= k[3]
    for round_number in range(1, rounds):
        k = keys[round_number]
        n0 = (t0[s0 >> 24] ^ t1[(s3 >> 16) & 0xFF] ^ t2[(s2 >> 8) & 0xFF]
              ^ t3[s1 & 0xFF] ^ k[0])
        n1 = (t0[s1 >> 24] ^ t1[(s0 >> 16) & 0xFF] ^ t2[(s3 >> 8) & 0xFF]
              ^ t3[s2 & 0xFF] ^ k[1])
        n2 = (t0[s2 >> 24] ^ t1[(s1 >> 16) & 0xFF] ^ t2[(s0 >> 8) & 0xFF]
              ^ t3[s3 & 0xFF] ^ k[2])
        n3 = (t0[s3 >> 24] ^ t1[(s2 >> 16) & 0xFF] ^ t2[(s1 >> 8) & 0xFF]
              ^ t3[s0 & 0xFF] ^ k[3])
        s0, s1, s2, s3 = n0, n1, n2, n3
    k = keys[rounds]
    out = (((inv[s0 >> 24] << 24) | (inv[(s3 >> 16) & 0xFF] << 16)
            | (inv[(s2 >> 8) & 0xFF] << 8) | inv[s1 & 0xFF]) ^ k[0],
           ((inv[s1 >> 24] << 24) | (inv[(s0 >> 16) & 0xFF] << 16)
            | (inv[(s3 >> 8) & 0xFF] << 8) | inv[s2 & 0xFF]) ^ k[1],
           ((inv[s2 >> 24] << 24) | (inv[(s1 >> 16) & 0xFF] << 16)
            | (inv[(s0 >> 8) & 0xFF] << 8) | inv[s3 & 0xFF]) ^ k[2],
           ((inv[s3 >> 24] << 24) | (inv[(s2 >> 16) & 0xFF] << 16)
            | (inv[(s1 >> 8) & 0xFF] << 8) | inv[s0 & 0xFF]) ^ k[3])
    return struct.pack(">4I", *out)


def aes_decrypt_block(key, block):
    keys, rounds = _expand(key)
    return _decrypt_block(keys, rounds, block)


def aes_cbc_decrypt(key, data):
    """AES-CBC with the IV in front and PKCS#5 padding, as PDF stores it."""
    if len(data) < 32 or len(data) % 16:
        # An empty string is sixteen bytes of IV and nothing else; a length
        # that is not whole blocks is a damaged stream, returned as found.
        return b"" if len(data) == 16 else data
    keys, rounds = _expand(key)
    previous = data[:16]
    out = bytearray()
    for index in range(16, len(data), 16):
        block = data[index:index + 16]
        plain = _decrypt_block(keys, rounds, block)
        out += bytes(a ^ b for a, b in zip(plain, previous))
        previous = block
    padding = out[-1] if out else 0
    if 1 <= padding <= 16 and out[-padding:] == bytes([padding]) * padding:
        del out[-padding:]
    return bytes(out)
