"""Encrypted PDFs that open without a password.

Nineteen payslips on one machine were encrypted by the portal that issued
them, eleven of them with no password to open -- the encryption forbade
printing, not reading. To this reader they were bytes. PDFKit and this
module agree on which is which: the eight it will not open are the eight a
Mac asks a password for.
"""

from __future__ import annotations

import hashlib
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from readers import pdfcrypt                             # noqa: E402
from readers import pdftext                              # noqa: E402

OWNER = bytes(range(32))
IDENT = bytes.fromhex("00112233445566778899aabbccddeeff")
PAGE = (b"BT /F1 14 Tf 1 0 0 1 72 700 Tm (Rechnung Stadtwerke Muenchen fuer "
        b"den Monat Mai mit allen Positionen) Tj ET")
# PAGE, encrypted with AES-128-CBC for object 4 by OpenSSL, IV in front.
AES_PAGE = bytes.fromhex(
    "6465666768696a6b6c6d6e6f707172730805f4cd3b1c80713b56f5a063b367856e"
    "ea3e67acc3062fb0a75e0792583a33339df3c80ef7ca3f08d6a3c3e361e2334029"
    "b995b1f1579144ec3d2409c7b22ac66109b390f6beaf8b2b5cd01565e5c624d725"
    "59992382cbaa649f2b607c0bbe37627994a96a99166f3bfb26596bee10")


def _key(permissions=-4, padded=None, owner=None):
    digest = hashlib.md5((padded or pdfcrypt._PAD) + (owner or OWNER)
                         + struct.pack("<i", permissions) + IDENT).digest()
    for _round in range(50):
        digest = hashlib.md5(digest[:16]).digest()
    return digest[:16]


def _user_value(key):
    check = hashlib.md5(pdfcrypt._PAD + IDENT).digest()
    for count in range(20):
        check = pdfcrypt.rc4(bytes(byte ^ count for byte in key), check)
    return check + bytes(16)


def _pdf(encrypt, body):
    return (b"%PDF-1.6\n"
            b"1 0 obj\n<</Type/Catalog/Pages 2 0 R>>\nendobj\n"
            b"2 0 obj\n<</Type/Pages/Kids[3 0 R]/Count 1>>\nendobj\n"
            b"3 0 obj\n<</Type/Page/Parent 2 0 R/Contents 4 0 R>>\nendobj\n"
            + (b"4 0 obj\n<</Length %d>>\nstream\n" % len(body)) + body
            + b"\nendstream\nendobj\n"
            b"5 0 obj\n" + encrypt + b"\nendobj\n"
            b"trailer\n<</Root 1 0 R/Encrypt 5 0 R/ID[<"
            + IDENT.hex().encode() + b"><" + IDENT.hex().encode()
            + b">]>>\n%%EOF\n")


def _rc4_pdf(user=None):
    key = _key()
    user = user if user is not None else _user_value(key)
    handler = pdfcrypt.Handler(key, False, "V2", "V2")
    encrypt = (b"<</Filter/Standard/V 2/R 3/Length 128/P -4/O <"
               + OWNER.hex().encode() + b">/U <" + user.hex().encode()
               + b">>>")
    return _pdf(encrypt, handler.decrypt(4, 0, PAGE))


def _aes_pdf():
    encrypt = (b"<</Filter/Standard/V 4/R 4/Length 128/P -4"
               b"/CF<</StdCF<</CFM/AESV2/Length 16/AuthEvent/DocOpen>>>>"
               b"/StmF/StdCF/StrF/StdCF/O <" + OWNER.hex().encode()
               + b">/U <" + _user_value(_key()).hex().encode() + b">>>")
    return _pdf(encrypt, AES_PAGE)


def _locked_pdf(user_password, owner_password):
    """RC4, revision 3, needing `user_password` to open (Algorithms 2-5)."""
    user_padded = pdfcrypt._padded(user_password)
    owner_key = hashlib.md5(pdfcrypt._padded(owner_password)).digest()
    for _round in range(50):
        owner_key = hashlib.md5(owner_key).digest()
    owner = user_padded
    for count in range(20):
        owner = pdfcrypt.rc4(bytes(byte ^ count for byte in owner_key[:16]),
                             owner)
    key = _key(padded=user_padded, owner=owner)
    handler = pdfcrypt.Handler(key, False, "V2", "V2")
    encrypt = (b"<</Filter/Standard/V 2/R 3/Length 128/P -4/O <"
               + owner.hex().encode() + b">/U <"
               + _user_value(key).hex().encode() + b">>>")
    return _pdf(encrypt, handler.decrypt(4, 0, PAGE))


# Written by a Mac's own PDFKit, not by anything here: RC4-128, revision 3,
# owner password "owner"; the second also needs "secret" to open. An
# independent implementation to be checked against, rather than fixtures
# made by the same understanding of the standard that is being tested.
APPLE_OPEN = bytes.fromhex(
    "255044462d312e340a25c4e5f2e5eba7f3a0d0c4c60a312030206f626a0a3c3c202f"
    "5061676573203220302052202f54797065202f436174616c6f67203e3e0a656e646f"
    "626a0a322030206f626a0a3c3c202f436f756e742031202f4b696473205b20332030"
    "2052205d202f54797065202f5061676573203e3e0a656e646f626a0a332030206f62"
    "6a0a3c3c202f436f6e74656e7473203420302052202f5265736f7572636573203c3c"
    "202f466f6e74203c3c202f4631203520302052203e3e203e3e202f4d65646961426f"
    "78205b203020300a36313220373932205d202f506172656e74203220302052202f54"
    "797065202f50616765203e3e0a656e646f626a0a342030206f626a0a3c3c202f4669"
    "6c746572202f466c6174654465636f6465202f4c656e677468203938203e3e200a73"
    "747265616d0ad394a0f75c88452b2cc7119098db0417bf4035a0f11e5d1fa8e51c17"
    "a0ca2d0c74d2c6003c9adeb6c669a715b64e3efb1b8b2aca6d183d4916e39373e3ea"
    "d454a6be73fa4b9f0703f302ece8326994754544d51ace67e6c1cd0f34af1957e74d"
    "e8a90a656e6473747265616d0a656e646f626a0a352030206f626a0a3c3c202f4261"
    "7365466f6e74202f48656c766574696361202f53756274797065202f547970653120"
    "2f54797065202f466f6e74203e3e0a656e646f626a0a362030206f626a0a3c3c202f"
    "50726f64756365722028d1c44d64f66aab22c4f15a38ace9a7e59b5e32e2555c3033"
    "365c303330f9dd5c303237ed5c30313472d6d95c30303789cc345c303337885c3030"
    "362390747ddb5f3c313af08ae1ce2eb8ff264523fae0dd5f886f46a5358d2c290a3e"
    "3e0a656e646f626a0a372030206f626a0a3c3c202f50202d34202f55203c65323461"
    "37396163613136386439616139643732613563643135336238386166303030303030"
    "30303030303030303030303030303030303030303030303030303e0a2f562032202f"
    "4f203c35363666613837336565333363373937636433623930346664616466383134"
    "6166613334646639613338663665643431623938346532633664613261613666353e"
    "0a2f4c656e67746820313238202f522033202f46696c746572202f5374616e646172"
    "64203e3e0a656e646f626a0a787265660a3020380a30303030303030303030203635"
    "3533352066200a30303030303030303232203030303030206e200a30303030303030"
    "303731203030303030206e200a30303030303030313330203030303030206e200a30"
    "303030303030323538203030303030206e200a303030303030303432382030303030"
    "30206e200a30303030303030343938203030303030206e200a303030303030303632"
    "31203030303030206e200a0a747261696c65720a3c3c202f456e6372797074203720"
    "302052202f496e666f203620302052202f4944205b203c6561353065656630633564"
    "6536643539633662316230666664636262363738633e203c65613530656566306335"
    "646536643539633662316230666664636262363738633e0a5d202f526f6f74203120"
    "302052202f53697a652038203e3e200a7374617274787265660a3832380a2525454f"
    "460a")
APPLE_LOCKED = bytes.fromhex(
    "255044462d312e340a25c4e5f2e5eba7f3a0d0c4c60a312030206f626a0a3c3c202f"
    "5061676573203220302052202f54797065202f436174616c6f67203e3e0a656e646f"
    "626a0a322030206f626a0a3c3c202f436f756e742031202f4b696473205b20332030"
    "2052205d202f54797065202f5061676573203e3e0a656e646f626a0a332030206f62"
    "6a0a3c3c202f436f6e74656e7473203420302052202f5265736f7572636573203c3c"
    "202f466f6e74203c3c202f4631203520302052203e3e203e3e202f4d65646961426f"
    "78205b203020300a36313220373932205d202f506172656e74203220302052202f54"
    "797065202f50616765203e3e0a656e646f626a0a342030206f626a0a3c3c202f4669"
    "6c746572202f466c6174654465636f6465202f4c656e677468203938203e3e200a73"
    "747265616d0a0518639567d1859b8f2129357cf9c9d4ca7dc648be908d13f034bb74"
    "bfb95821d48d89b980fd86ff9073fbfbbc066b766208f68f6a25e356106f09675fe3"
    "af451264889f11739215cc42dbeb0f11753f0cf3fbfa4f991245e504b96c32891869"
    "c03b0a656e6473747265616d0a656e646f626a0a352030206f626a0a3c3c202f4261"
    "7365466f6e74202f48656c766574696361202f53756274797065202f547970653120"
    "2f54797065202f466f6e74203e3e0a656e646f626a0a362030206f626a0a3c3c202f"
    "50726f647563657220285c3033325c3031315c3032312eb7214cb9c1b762745c3033"
    "35ee263673e837abb8995c3033313b54f35ac3c9fc415c303132794d3d6c7d5eb764"
    "606956a586725f5b36b3f9c05c303137dcd0f73958afeee9d9f8c7318ac89f290a3e"
    "3e0a656e646f626a0a372030206f626a0a3c3c202f50202d34202f55203c65643434"
    "63323066646430653337346137323537633532653839643462346638303030303030"
    "30303030303030303030303030303030303030303030303030303e0a2f562032202f"
    "4f203c30646235383535666335333236353639653736353930366361663634653434"
    "3239613463323064366539393666646566393633653962353038306639653038333e"
    "0a2f4c656e67746820313238202f522033202f46696c746572202f5374616e646172"
    "64203e3e0a656e646f626a0a787265660a3020380a30303030303030303030203635"
    "3533352066200a30303030303030303232203030303030206e200a30303030303030"
    "303731203030303030206e200a30303030303030313330203030303030206e200a30"
    "303030303030323538203030303030206e200a303030303030303432382030303030"
    "30206e200a30303030303030343938203030303030206e200a303030303030303632"
    "31203030303030206e200a0a747261696c65720a3c3c202f456e6372797074203720"
    "302052202f496e666f203620302052202f4944205b203c3336376665303561666562"
    "3235373234383733316138356463333365633738313e203c33363766653035616665"
    "623235373234383733316138356463333365633738313e0a5d202f526f6f74203120"
    "302052202f53697a652038203e3e200a7374617274787265660a3832380a2525454f"
    "460a")


class _Peek:
    def __init__(self, data):
        self.data = data

    def at(self, offset, size):
        return self.data[offset:offset + size]


class TheCiphers(unittest.TestCase):

    def test_aes_128_against_fips_197(self):
        self.assertEqual(pdfcrypt.aes_decrypt_block(
            bytes(range(16)),
            bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")).hex(),
            "00112233445566778899aabbccddeeff")

    def test_aes_256_against_fips_197(self):
        self.assertEqual(pdfcrypt.aes_decrypt_block(
            bytes(range(32)),
            bytes.fromhex("8ea2b7ca516745bfeafc49904b496089")).hex(),
            "00112233445566778899aabbccddeeff")

    def test_rc4(self):
        self.assertEqual(pdfcrypt.rc4(b"Key", b"Plaintext").hex(),
                         "bbf316e8d940af0ad3")


class OpeningWithoutAPassword(unittest.TestCase):

    def setUp(self):
        # Never the Keychain of whoever runs the tests.
        pdfcrypt._known = []

    def tearDown(self):
        pdfcrypt.forget()

    def test_an_rc4_file_reads(self):
        text, image_only = pdftext.extract(_Peek(_rc4_pdf()))
        self.assertIn("Rechnung Stadtwerke", text)
        self.assertFalse(image_only)

    def test_an_aes_file_reads(self):
        text, _image_only = pdftext.extract(_Peek(_aes_pdf()))
        self.assertIn("Rechnung Stadtwerke", text)

    def test_a_file_that_needs_a_password_is_not_opened(self):
        """No password is guessed: the empty one is checked, and that is all."""
        locked = _rc4_pdf(user=bytes(32))
        self.assertIsNone(pdfcrypt.handler(locked))
        self.assertEqual(pdftext.extract(_Peek(locked)), ("", False))

    def test_the_owners_password_opens_it(self):
        locked = _locked_pdf("letmein", "boss")
        self.assertIsNone(pdfcrypt.handler(locked, passwords=[]))
        opened = pdfcrypt.handler(locked, passwords=["letmein"])
        self.assertIsNotNone(opened)
        text, _image_only = pdftext.extract(_Peek(
            pdfcrypt.decrypted(locked, opened).replace(b"/Encrypt",
                                                       b"/Xncrypt")))
        self.assertIn("Rechnung Stadtwerke", text)

    def test_the_password_that_owns_it_opens_it_too(self):
        locked = _locked_pdf("letmein", "boss")
        self.assertIsNotNone(pdfcrypt.handler(locked, passwords=["boss"]))

    def test_a_wrong_password_is_not_enough(self):
        locked = _locked_pdf("letmein", "boss")
        self.assertIsNone(pdfcrypt.handler(locked, passwords=["guess"]))

    def test_what_a_mac_writes_opens_as_a_mac_opens_it(self):
        for data, passwords, opens in ((APPLE_OPEN, [], True),
                                       (APPLE_LOCKED, [], False),
                                       (APPLE_LOCKED, ["secret"], True),
                                       (APPLE_LOCKED, ["owner"], True),
                                       (APPLE_LOCKED, ["wrong"], False)):
            found = pdfcrypt.handler(data, passwords=passwords)
            self.assertEqual(found is not None, opens, passwords)
            if found:
                text, _image_only = pdftext.extract(_Peek(
                    pdfcrypt.decrypted(data, found).replace(b"/Encrypt",
                                                            b"/Xncrypt")))
                self.assertIn("Rechnung Stadtwerke", text)

    def test_an_unencrypted_file_has_no_handler(self):
        self.assertIsNone(pdfcrypt.handler(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"))


if __name__ == "__main__":
    unittest.main()
