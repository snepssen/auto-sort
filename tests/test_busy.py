"""A daemon half-way through a batch is busy, not gone.

The log page, the tray and every command are answered between cycles, and
a cycle can be reading files. Taken for "not running", `status` said so
about a daemon that was working, `restart` took a busy daemon for a stopped
one, and the menu entry started a second. And the batches are limited by
time, because what a file costs to read varies a hundredfold.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import daemon                                            # noqa: E402
import ledger                                            # noqa: E402


class Probing(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-busy-")
        self.state = os.path.join(self.dir, "state.db")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def record(self, port):
        with ledger.Ledger(self.state) as journal:
            journal.set_state("daemon_port", str(port))

    def test_listening_but_not_answering_is_busy(self):
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen(4)            # accepted by the kernel, never read
        self.addCleanup(busy.close)
        port = busy.getsockname()[1]
        self.record(port)
        self.assertEqual(daemon.probe(self.state), (port, False))
        self.assertEqual(daemon.running_port(self.state), port)
        self.assertTrue(daemon.wake(self.state, "stop"))

    def test_nothing_listening_is_not_running(self):
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()
        self.record(port)
        self.assertEqual(daemon.probe(self.state), (None, False))
        self.assertIsNone(daemon.running_port(self.state))
        self.assertFalse(daemon.wake(self.state))

    def test_a_ledger_no_daemon_ran_from_has_none(self):
        self.assertEqual(daemon.probe(self.state), (None, False))


if __name__ == "__main__":
    unittest.main()
