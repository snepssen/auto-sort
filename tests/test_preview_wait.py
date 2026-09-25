"""A preview nobody answers starts sorting by itself, after a while.

The daemon used to show where everything would go and then wait for a
click. Somebody who installed it and walked away never made one: it
proposed, slept for ever, and was deleted in exasperation. Now a preview
waits `preview_wait` -- fifteen minutes in the starter file -- and then
counts as approved. A pause somebody chose is never lifted by anything but
them, and `never` waits as it used to.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import daemon                                            # noqa: E402
import fixtures                                          # noqa: E402

RULES = """
[settings]
dry_run = no
settle_seconds = 0
preview_wait = {wait}

[watch]
folders = {root}

[rule: images]
when = kind = image
into = {out}/Pictures
"""


class PreviewWait(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autosort-previewwait-")
        self.root = os.path.join(self.dir, "Downloads")
        self.out = os.path.join(self.dir, "out")
        self.rules = os.path.join(self.dir, "rules.ini")
        self.state = os.path.join(self.dir, "state.db")
        os.makedirs(self.root)
        self.photo = fixtures.png(os.path.join(self.root, "photo.png"))
        old = time.time() - 600
        os.utime(self.photo, (old, old))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def service(self, wait, messages):
        with open(self.rules, "w") as handle:
            handle.write(RULES.format(wait=wait, root=self.root,
                                      out=self.out))
        return daemon.PollingDaemon(self.rules, self.state, port=0,
                                    output=messages.append)

    def previewed(self, service):
        """Cycle until the preview has paused it."""
        for moment in range(100, 110):
            service.cycle(now_value=moment)
            if service.journal.paused():
                return
        self.fail("no preview was made")

    def age_the_pause(self, service, seconds):
        service.journal.set_state("paused_at", repr(time.time() - seconds))

    def moved(self):
        return os.path.exists(os.path.join(self.out, "Pictures", "photo.png"))

    def test_after_the_wait_it_sorts_by_itself(self):
        messages = []
        with self.service("15m", messages) as service:
            self.previewed(service)
            self.assertEqual(service.journal.get_state("paused_by"),
                             "preview")
            service.cycle(now_value=200)
            self.assertTrue(service.journal.paused(), "not yet: 15 minutes")
            self.age_the_pause(service, 16 * 60)
            for moment in range(300, 305):
                service.cycle(now_value=moment)
        self.assertTrue(self.moved(), messages)
        self.assertTrue(any("Nobody paused the preview in 15 minutes"
                            in message for message in messages), messages)

    def test_a_pause_somebody_chose_is_theirs(self):
        messages = []
        with self.service("15m", messages) as service:
            self.previewed(service)
            service.journal.set_paused(True)            # "Keep previewing"
            self.age_the_pause(service, 24 * 3600)
            for moment in range(300, 305):
                service.cycle(now_value=moment)
            self.assertTrue(service.journal.paused())
        self.assertFalse(self.moved())

    def test_never_waits_for_resume(self):
        messages = []
        with self.service("never", messages) as service:
            self.previewed(service)
            self.age_the_pause(service, 30 * 24 * 3600)
            for moment in range(300, 305):
                service.cycle(now_value=moment)
            self.assertTrue(service.journal.paused())
        self.assertFalse(self.moved())


if __name__ == "__main__":
    unittest.main()
