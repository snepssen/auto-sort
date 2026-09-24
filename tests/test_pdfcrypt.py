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

    def test_an_unencrypted_file_has_no_handler(self):
        self.assertIsNone(pdfcrypt.handler(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"))


if __name__ == "__main__":
    unittest.main()
