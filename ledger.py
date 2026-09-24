"""The durable account of every action auto-sort planned and completed.

Filesystem operations are not database transactions.  The closest honest
equivalent is to write intent before touching a path, update the row after the
operation, and retain enough evidence to reconcile or undo it after a crash.
This module deliberately knows nothing about classification or rules; it is a
small SQLite journal for runs and their member-level operations.
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import time

import paths


SCHEMA_VERSION = 10


def _days_ago(days):
    return (datetime.datetime.now()
            - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")


def _larger(fresh, kept):
    """The bigger of two readings, where either may be absent.

    Absent is not zero. A platform that could not measure memory last week
    and can this week should end up with this week's number, not with a
    comparison against a nought that nobody ever observed.
    """
    if fresh is None:
        return kept
    if kept is None:
        return fresh
    return max(fresh, kept)


# A placement is over once this program itself took the file on from where
# it was put: a regroup out of `Unfiled`, a duplicate sent to the bin. Left
# standing, the old placement counts the file a second time -- a rule
# report read 169 regrouped contracts as 338 -- and says it sits somewhere
# it does not, which is exactly what somebody moving it would look like.
# Undoing that later move makes the placement stand again, by itself.
_STILL_THERE = (
    " AND NOT EXISTS (SELECT 1 FROM moves later"
    "  WHERE later.source = m.destination AND later.id > m.id"
    "    AND later.status = 'done' AND later.undone_at IS NULL"
    "    AND later.operation IN ('move', 'rename', 'trash'))")


class Ledger(object):
    def __init__(self, filename=None):
        self.filename = os.path.abspath(filename or paths.ledger_file())
        paths.ensure(os.path.dirname(self.filename))
        self.connection = sqlite3.connect(self.filename, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, _kind, _value, _traceback):
        self.close()

    def _migrate(self):
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("state database is from a newer auto-sort")
        if version == 0:
            with self.connection:
                self.connection.executescript("""
                    CREATE TABLE runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        action TEXT NOT NULL,
                        source_root TEXT,
                        rules_hash TEXT,
                        dry_run INTEGER NOT NULL DEFAULT 1,
                        status TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        summary TEXT
                    );

                    CREATE TABLE moves (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id INTEGER NOT NULL REFERENCES runs(id),
                        item_number INTEGER NOT NULL,
                        member_number INTEGER NOT NULL,
                        operation TEXT NOT NULL,
                        rule_name TEXT NOT NULL,
                        source TEXT NOT NULL,
                        destination TEXT NOT NULL,
                        size INTEGER NOT NULL,
                        sha256 TEXT NOT NULL,
                        status TEXT NOT NULL,
                        error TEXT,
                        created_at TEXT NOT NULL,
                        completed_at TEXT,
                        undone_at TEXT,
                        restored_to TEXT
                    );

                    CREATE INDEX moves_by_run ON moves(run_id, id);
                    CREATE INDEX moves_by_status ON moves(status);

                    CREATE TABLE previews (
                        source_root TEXT PRIMARY KEY,
                        rules_hash TEXT NOT NULL,
                        run_id INTEGER NOT NULL REFERENCES runs(id),
                        previewed_at TEXT NOT NULL
                    );

                    CREATE TABLE state (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    );

                    PRAGMA user_version = 1;
                """)
            version = 1
        if version == 1:
            with self.connection:
                self.connection.executescript("""
                    CREATE TABLE queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_root TEXT NOT NULL,
                        primary_path TEXT NOT NULL,
                        members_json TEXT NOT NULL,
                        is_dir INTEGER NOT NULL DEFAULT 0,
                        reason TEXT,
                        sequence_count INTEGER NOT NULL DEFAULT 0,
                        fingerprint TEXT NOT NULL,
                        rules_hash TEXT NOT NULL,
                        stable_since REAL NOT NULL,
                        last_seen REAL NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        attempts INTEGER NOT NULL DEFAULT 0,
                        retry_at REAL,
                        error TEXT,
                        UNIQUE(source_root, primary_path)
                    );

                    CREATE INDEX queue_ready ON queue(
                        source_root, status, stable_since, retry_at
                    );

                    PRAGMA user_version = 2;
                """)
            version = 2
        if version == 2:
            with self.connection:
                columns = [row[1] for row in self.connection.execute(
                    "PRAGMA table_info(moves)").fetchall()]
                if "facts_json" not in columns:
                    self.connection.execute(
                        "ALTER TABLE moves ADD COLUMN facts_json TEXT")
                self.connection.execute("PRAGMA user_version = 3")
            version = 3
        if version == 3:
            with self.connection:
                self.connection.executescript("""
                    CREATE TABLE IF NOT EXISTS directories (
                        run_id INTEGER NOT NULL REFERENCES runs(id),
                        path TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        removed_at TEXT,
                        UNIQUE(run_id, path)
                    );

                    PRAGMA user_version = 4;
                """)
            version = 4
        if version == 4:
            with self.connection:
                self.connection.executescript("""
                    CREATE TABLE IF NOT EXISTS corrections (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        move_id INTEGER NOT NULL REFERENCES moves(id),
                        rule_name TEXT,
                        placed_at TEXT NOT NULL,
                        found_at TEXT,
                        outcome TEXT NOT NULL,
                        facts_json TEXT,
                        noticed_at TEXT NOT NULL,
                        UNIQUE(move_id)
                    );

                    CREATE INDEX IF NOT EXISTS corrections_outcome
                        ON corrections(outcome, found_at);

                    PRAGMA user_version = 5;
                """)
            version = 5
        if version == 5:
            with self.connection:
                columns = [row[1] for row in self.connection.execute(
                    "PRAGMA table_info(moves)").fetchall()]
                if "holding" not in columns:
                    # Whether the rule that placed a file considered its
                    # destination provisional. Recorded at the time rather
                    # than worked out later from the rule's name, because
                    # names change every time a rules file is regenerated and
                    # a file's history must not depend on that.
                    self.connection.execute(
                        "ALTER TABLE moves ADD COLUMN holding "
                        "INTEGER NOT NULL DEFAULT 0")
                self.connection.execute("PRAGMA user_version = 6")
            version = 6
        if version == 6:
            with self.connection:
                # A second copy of everything filed, on a disk that is very
                # often asleep, full or in a drawer. So the intention to copy
                # is recorded the moment a file is placed and the copying
                # happens whenever the disk is actually there. Sorting never
                # waits for a backup and never fails because of one.
                self.connection.executescript("""
                    CREATE TABLE IF NOT EXISTS mirror (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        move_id INTEGER REFERENCES moves(id),
                        source TEXT NOT NULL,
                        relative TEXT NOT NULL,
                        size INTEGER NOT NULL DEFAULT 0,
                        sha256 TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        error TEXT,
                        queued_at TEXT NOT NULL,
                        copied_at TEXT,
                        UNIQUE(relative, sha256)
                    );
                    CREATE INDEX IF NOT EXISTS mirror_status
                        ON mirror(status);

                    PRAGMA user_version = 7;
                """)
            version = 7
        if version == 7:
            with self.connection:
                # The only feedback loop this program is allowed to have.
                # There is no crash reporter, so a file that costs an absurd
                # amount to read has to leave its own note or nobody -- the
                # person it happened to least of all -- ever finds out.
                #
                # One row per path rather than one per reading. The question
                # is "which file is doing this", not "how often", and a log
                # of every pass over the same twenty-year folder would grow
                # faster than the thing it is reporting on.
                self.connection.executescript("""
                    CREATE TABLE IF NOT EXISTS costs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        path TEXT NOT NULL UNIQUE,
                        file_name TEXT NOT NULL,
                        size INTEGER NOT NULL DEFAULT 0,
                        seconds REAL NOT NULL DEFAULT 0,
                        growth INTEGER,
                        peak INTEGER,
                        reason TEXT NOT NULL DEFAULT '',
                        readings INTEGER NOT NULL DEFAULT 1,
                        first_seen TEXT NOT NULL,
                        last_seen TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS costs_seconds
                        ON costs(seconds DESC);

                    PRAGMA user_version = 8;
                """)
            version = 8
        if version == 8:
            with self.connection:
                # What an optional program said about a file, so that it is
                # never asked twice about the same unchanged one. Reading a
                # scanned page costs a second and a half and a process; the
                # answer does not change until the file does.
                self.connection.executescript("""
                    CREATE TABLE IF NOT EXISTS readings (
                        path TEXT NOT NULL,
                        tool TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        delta_json TEXT NOT NULL,
                        seconds REAL NOT NULL DEFAULT 0,
                        made_at TEXT NOT NULL,
                        PRIMARY KEY (path, tool)
                    );

                    PRAGMA user_version = 9;
                """)
            version = 9
        if version == 9:
            with self.connection:
                # Finding the move that took a file on from where an earlier
                # one put it. See `_STILL_THERE`.
                self.connection.executescript("""
                    CREATE INDEX IF NOT EXISTS moves_by_source
                        ON moves(source);

                    PRAGMA user_version = 10;
                """)

    def record_directories(self, run_id, directories):
        """Remember the folders a run had to create, so undo can remove them.

        Recorded after the moves rather than before: a directory that was
        planned but never reached — because the item failed — was never
        created and must not be listed as though it were.
        """
        rows = [(run_id, directory, now())
                for directory in sorted(set(directories))
                if os.path.isdir(directory)]
        if not rows:
            return 0
        with self.connection:
            self.connection.executemany(
                "INSERT OR IGNORE INTO directories(run_id, path, created_at) "
                "VALUES (?, ?, ?)", rows)
        return len(rows)

    def directories_for_run(self, run_id):
        return [row[0] for row in self.connection.execute(
            "SELECT path FROM directories WHERE run_id = ? "
            "AND removed_at IS NULL ORDER BY length(path) DESC",
            (run_id,)).fetchall()]

    def made_directory(self, path):
        """Did a run of this program create this folder, and is it still ours?"""
        row = self.connection.execute(
            "SELECT 1 FROM directories WHERE path = ? AND removed_at IS NULL "
            "LIMIT 1", (os.path.abspath(path),)).fetchone()
        return row is not None

    def mark_directories_removed(self, run_id, directories):
        if not directories:
            return
        with self.connection:
            self.connection.executemany(
                "UPDATE directories SET removed_at = ? "
                "WHERE run_id = ? AND path = ?",
                [(now(), run_id, directory) for directory in directories])

    def placed_moves(self, source_root=None, limit=20000):
        """Completed moves that have not been undone, newest first.

        These are the placements this tool is answerable for: if one of them
        is no longer where it was put, somebody disagreed.
        """
        sql = ("SELECT m.*, r.source_root FROM moves m "
               "JOIN runs r ON r.id = m.run_id "
               "WHERE m.status IN ('done', 'copied') "
               "AND m.undone_at IS NULL AND r.action = 'sort'" + _STILL_THERE)
        parameters = []
        if source_root:
            sql += " AND r.source_root = ?"
            parameters.append(source_root)
        sql += " ORDER BY m.id DESC LIMIT ?"
        parameters.append(limit)
        return self.connection.execute(sql, parameters).fetchall()

    def record_correction(self, move_id, rule_name, placed_at, found_at,
                          outcome, facts_json=None):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO corrections(move_id, rule_name, "
                "placed_at, found_at, outcome, facts_json, noticed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (move_id, rule_name, placed_at, found_at, outcome,
                 facts_json, now()))

    def corrections(self, outcome="moved", limit=5000):
        sql = "SELECT * FROM corrections"
        parameters = []
        if outcome:
            sql += " WHERE outcome = ?"
            parameters.append(outcome)
        sql += " ORDER BY id DESC LIMIT ?"
        parameters.append(limit)
        return self.connection.execute(sql, parameters).fetchall()

    def forget_correction(self, move_id):
        with self.connection:
            self.connection.execute(
                "DELETE FROM corrections WHERE move_id = ?", (move_id,))

    def start_run(self, action, source_root=None, rules_hash=None, dry_run=True):
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO runs(action, source_root, rules_hash, dry_run, "
                "status, started_at) VALUES (?, ?, ?, ?, 'running', ?)",
                (action, source_root, rules_hash, int(bool(dry_run)), now()))
        return cursor.lastrowid

    def finish_run(self, run_id, status, summary=None):
        with self.connection:
            self.connection.execute(
                "UPDATE runs SET status = ?, summary = ?, finished_at = ? "
                "WHERE id = ?", (status, summary, now(), run_id))

    def add_move(self, run_id, item_number, member_number, operation,
                 rule_name, source, destination, size, sha256,
                 status="planned", facts=None, holding=False):
        facts_json = json.dumps(facts, sort_keys=True, default=str) \
            if facts else None
        with self.connection:
            cursor = self.connection.execute("""
                INSERT INTO moves(
                    run_id, item_number, member_number, operation, rule_name,
                    source, destination, size, sha256, status, created_at,
                    facts_json, holding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (run_id, item_number, member_number, operation, rule_name,
                    source, destination, int(size), sha256, status, now(),
                    facts_json, int(bool(holding))))
        return cursor.lastrowid

    def update_move(self, move_id, status, error=None, restored_to=None):
        completed = now() if status in (
            "done", "copied", "failed", "rolled-back", "undo-failed") else None
        undone = now() if status == "undone" else None
        with self.connection:
            self.connection.execute("""
                UPDATE moves
                   SET status = ?, error = ?,
                       completed_at = COALESCE(?, completed_at),
                       undone_at = COALESCE(?, undone_at),
                       restored_to = COALESCE(?, restored_to)
                 WHERE id = ?
            """, (status, error, completed, undone, restored_to, move_id))

    def record_preview(self, source_root, rules_hash, run_id):
        with self.connection:
            self.connection.execute("""
                INSERT INTO previews(source_root, rules_hash, run_id, previewed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_root) DO UPDATE SET
                    rules_hash = excluded.rules_hash,
                    run_id = excluded.run_id,
                    previewed_at = excluded.previewed_at
            """, (os.path.realpath(source_root), rules_hash, run_id, now()))

    def has_preview(self, source_root, rules_hash):
        row = self.connection.execute(
            "SELECT rules_hash FROM previews WHERE source_root = ?",
            (os.path.realpath(source_root),)).fetchone()
        return row is not None and row["rules_hash"] == rules_hash

    def run(self, run_id):
        return self.connection.execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()

    def moves(self, run_id, statuses=None, reverse=False):
        sql = "SELECT * FROM moves WHERE run_id = ?"
        parameters = [run_id]
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += " AND status IN (%s)" % placeholders
            parameters.extend(statuses)
        sql += " ORDER BY id %s" % ("DESC" if reverse else "ASC")
        return self.connection.execute(sql, parameters).fetchall()

    def move(self, move_id):
        return self.connection.execute("""
            SELECT m.*, r.action, r.source_root, r.started_at, r.finished_at
              FROM moves m JOIN runs r ON r.id = m.run_id
             WHERE m.id = ?
        """, (int(move_id),)).fetchone()

    def recent_moves(self, limit=100):
        limit = max(1, min(int(limit), 500))
        return self.connection.execute("""
            SELECT m.*, r.action, r.source_root, r.started_at, r.finished_at
              FROM moves m JOIN runs r ON r.id = m.run_id
             ORDER BY m.id DESC LIMIT ?
        """, (limit,)).fetchall()

    def queue_mirror(self, move_id, source, relative, size, digest):
        """Record that a file ought to exist on the second disk too.

        `UNIQUE(relative, sha256)` means re-queueing the same bytes at the
        same place is free, so a backfill can be run as often as somebody
        likes without copying anything twice.
        """
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO mirror(move_id, source, relative, "
                "size, sha256, status, queued_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (move_id, source, relative, int(size or 0), digest or "",
                 now()))

    def pending_mirror(self, limit=200):
        return self.connection.execute(
            "SELECT * FROM mirror WHERE status='pending' "
            "ORDER BY id LIMIT ?", (limit,)).fetchall()

    def mirror_done(self, mirror_id):
        with self.connection:
            self.connection.execute(
                "UPDATE mirror SET status='copied', copied_at=?, error=NULL "
                "WHERE id=?", (now(), mirror_id))

    def mirror_failed(self, mirror_id, error, give_up=False):
        with self.connection:
            self.connection.execute(
                "UPDATE mirror SET status=?, attempts=attempts+1, error=? "
                "WHERE id=?",
                ("failed" if give_up else "pending", str(error)[:300],
                 mirror_id))

    def mirror_counts(self):
        rows = self.connection.execute(
            "SELECT status, COUNT(*) c, COALESCE(SUM(size),0) bytes "
            "FROM mirror GROUP BY status").fetchall()
        return dict((row["status"], {"files": row["c"], "bytes": row["bytes"]})
                    for row in rows)

    def remember_reading(self, path, tool, fingerprint, delta, seconds=0.0):
        """Keep what a tool found, against the file as it was when asked."""
        with self.connection:
            self.connection.execute(
                "INSERT INTO readings (path, tool, fingerprint, delta_json, "
                "seconds, made_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(path, tool) DO UPDATE SET "
                "fingerprint=excluded.fingerprint, "
                "delta_json=excluded.delta_json, seconds=excluded.seconds, "
                "made_at=excluded.made_at",
                (path, tool, fingerprint, json.dumps(delta), float(seconds),
                 now()))

    def recall_reading(self, path, tool, fingerprint):
        """What that tool said last time, if the file is still that file."""
        row = self.connection.execute(
            "SELECT delta_json FROM readings "
            " WHERE path = ? AND tool = ? AND fingerprint = ?",
            (path, tool, fingerprint)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["delta_json"])
        except (TypeError, ValueError):
            return None

    def forget_readings(self, path):
        """Everything remembered about one file, for when it moves away."""
        with self.connection:
            return self.connection.execute(
                "DELETE FROM readings WHERE path = ?", (path,)).rowcount

    def held_moves(self, limit=5000):
        """Files sitting in holding folders, newest first, with their facts."""
        return self.connection.execute(
            "SELECT m.id, m.destination, m.facts_json FROM moves m "
            " WHERE m.holding = 1 AND m.status IN ('done','copied') "
            "   AND m.undone_at IS NULL" + _STILL_THERE +
            " ORDER BY m.id DESC LIMIT ?", (int(limit),)).fetchall()

    def set_facts(self, move_id, facts):
        with self.connection:
            self.connection.execute(
                "UPDATE moves SET facts_json = ? WHERE id = ?",
                (json.dumps(facts, sort_keys=True, default=str), move_id))

    def waiting_for_reading(self, limit=20000):
        """How many filed items are still waiting to be read, and where.

        A page held for OCR is filed -- it went somewhere -- but nothing in
        it was ever read, so it sits in a holding folder with no category.
        Counting them is what lets the log page say "twenty-two pages are
        waiting for a program you have not installed" instead of leaving
        somebody to wonder why their post is not sorting itself.

        Filings only. Every undo is a move too, and on a real machine the
        197 of them that put scans back into Downloads were being counted
        as 197 pages filed without being read.
        """
        rows = self.connection.execute(
            "SELECT m.destination, m.facts_json FROM moves m "
            "  JOIN runs r ON r.id = m.run_id "
            " WHERE m.status IN ('done','copied') AND m.undone_at IS NULL "
            "   AND r.action = 'sort' "
            "   AND m.facts_json LIKE '%needs_ocr%'" + _STILL_THERE +
            " ORDER BY m.id DESC LIMIT ?", (int(limit),)).fetchall()
        waiting = 0
        for row in rows:
            try:
                facts = json.loads(row["facts_json"] or "{}")
            except (TypeError, ValueError):
                continue
            # Only what is still there to be read. A page somebody moved
            # away by hand is theirs now, not a page waiting on a program.
            if (isinstance(facts, dict) and facts.get("needs_ocr")
                    and os.path.exists(row["destination"])):
                waiting += 1
        return waiting

    def record_cost(self, path, file_name, size, seconds,
                    growth=None, peak=None, reason=""):
        """Remember that one file was expensive to read.

        A path already listed keeps its *worst* reading rather than its
        latest. The second pass over a folder usually reads from the page
        cache and looks innocent, and the reading that matters is the one
        taken on the day somebody's machine went quiet.

        `growth` and `peak` are None on a platform that cannot measure them,
        and stay None: a nullable column rather than a zero, because zero is
        a measurement and this is the absence of one.
        """
        stamp = now()
        existing = self.connection.execute(
            "SELECT id, seconds, growth, peak FROM costs WHERE path = ?",
            (path,)).fetchone()
        with self.connection:
            if existing is None:
                self.connection.execute(
                    "INSERT INTO costs (path, file_name, size, seconds, "
                    "growth, peak, reason, readings, first_seen, last_seen) "
                    "VALUES (?,?,?,?,?,?,?,1,?,?)",
                    (path, file_name, size, seconds, growth, peak,
                     reason, stamp, stamp))
                return
            worst_seconds = max(seconds, existing["seconds"] or 0.0)
            worst_growth = _larger(growth, existing["growth"])
            worst_peak = _larger(peak, existing["peak"])
            keep_reason = reason if seconds >= (existing["seconds"] or 0.0) \
                else None
            self.connection.execute(
                "UPDATE costs SET size=?, seconds=?, growth=?, peak=?, "
                "reason=COALESCE(?, reason), readings=readings+1, "
                "last_seen=? WHERE id=?",
                (size, worst_seconds, worst_growth, worst_peak,
                 keep_reason, stamp, existing["id"]))

    def expensive(self, limit=50, slow_seconds=1.0,
                  greedy_bytes=16 * 1024 * 1024):
        """The files that cost the most, worst first.

        Sorted by how far past the threshold each one went rather than by
        either column, so that a PDF which inflated to 171 MB in a third of
        a second is not buried under everything that merely took a while.
        """
        return self.connection.execute(
            "SELECT * FROM costs "
            "ORDER BY MAX(seconds / ?, COALESCE(growth, 0) / ?) DESC, "
            "         seconds DESC LIMIT ?",
            (float(slow_seconds), float(greedy_bytes),
             max(1, min(int(limit), 500)))).fetchall()

    def forget_cost(self, cost_id):
        """Drop one row, for a file the person has dealt with."""
        with self.connection:
            return self.connection.execute(
                "DELETE FROM costs WHERE id = ?", (cost_id,)).rowcount

    def usage(self):
        """How big the ledger is, and how fast it got that way.

        Size alone is the wrong trigger. Fifty thousand moves is nothing if
        it took ten years and a great deal if it took a week -- the same
        number means "this machine is busy and fine" or "this will need its
        own disk by Tuesday". So the rate is measured and projected, and the
        projection is what decides.
        """
        size = 0
        for suffix in ("", "-wal"):
            try:
                size += os.path.getsize(self.filename + suffix)
            except OSError:
                pass
        rows = self.connection.execute(
            "SELECT COUNT(*) FROM moves").fetchone()[0]
        span = self.connection.execute(
            "SELECT MIN(created_at), MAX(created_at) FROM moves").fetchone()
        days = 0.0
        if span and span[0] and span[1]:
            try:
                first = datetime.datetime.strptime(span[0][:19],
                                                   "%Y-%m-%dT%H:%M:%S")
                last = datetime.datetime.strptime(span[1][:19],
                                                  "%Y-%m-%dT%H:%M:%S")
                days = max((last - first).total_seconds() / 86400.0, 0.0)
            except ValueError:
                days = 0.0
        # Under a day of history says nothing about a year of it.
        per_day = size / days if days >= 1 else 0.0
        return {"bytes": size, "rows": rows, "days": days,
                "bytes_per_day": per_day, "projected_year": per_day * 365}

    def should_compact(self, size_limit, year_limit):
        use = self.usage()
        if use["bytes"] >= size_limit:
            return "it has reached %.0f MB" % (use["bytes"] / 1e6)
        if use["projected_year"] >= year_limit:
            return ("it is growing at %.1f MB a day, which is %.0f MB a year"
                    % (use["bytes_per_day"] / 1e6,
                       use["projected_year"] / 1e6))
        return ""

    def compact(self, keep_facts_days=90, keep_dry_run_days=7):
        """Shed the bulk without losing where anything went.

        Three quarters of a ledger row is `facts_json` -- everything that
        was known about the file at the time. That is worth keeping while it
        is still useful and is not worth keeping forever: what somebody
        actually asks this database, years later, is where a file went, and
        that is the source, the destination and the date.

        So nothing is deleted except previews, which moved nothing by
        definition. Old rows keep their history and lose their evidence.
        Rows a `regroup` might still promote keep everything, because that
        is the one thing that reads old facts and acts on them.
        """
        cutoff = _days_ago(keep_facts_days)
        previews = _days_ago(keep_dry_run_days)
        before = self.usage()["bytes"]
        with self.connection:
            dropped = self.connection.execute(
                "DELETE FROM moves WHERE status = 'dry-run' AND created_at < ?",
                (previews,)).rowcount
            thinned = self.connection.execute(
                "UPDATE moves SET facts_json = NULL "
                " WHERE facts_json IS NOT NULL AND created_at < ? "
                "   AND NOT (holding = 1 AND undone_at IS NULL)",
                (cutoff,)).rowcount
            # A file that was expensive once and has not been seen since is
            # usually a file somebody dealt with. Keeping it forever turns a
            # short list worth reading into a long one nobody does.
            stale = self.connection.execute(
                "DELETE FROM costs WHERE last_seen < ?", (cutoff,)).rowcount
            # A remembered reading is an optimisation, not history. Once it
            # is this old the file has either been sorted and moved -- in
            # which case the path is wrong anyway -- or nothing has looked
            # at it in three months and reading it again costs one file.
            self.connection.execute(
                "DELETE FROM readings WHERE made_at < ?", (cutoff,))
        # Outside the transaction: VACUUM cannot run inside one. And the
        # checkpoint afterwards is not optional -- VACUUM rewrites the whole
        # database through the write-ahead log, so without collapsing it the
        # ledger measures *larger* after compaction than before, which is a
        # very confusing thing for a maintenance job to report.
        self.connection.execute("VACUUM")
        self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.set_state("last_compacted", now())
        return {"previews_removed": dropped, "rows_thinned": thinned,
                "costs_forgotten": stale,
                "bytes_before": before, "bytes_after": self.usage()["bytes"]}

    def extra_watch_folders(self):
        """Intake folders added from the log page, newest last.

        Kept here and not in the rules file. That file belongs to whoever
        wrote it and carries their comments; a program that rewrites it to
        add a line is a program that eventually eats one.
        """
        try:
            stored = json.loads(self.get_state("extra_watch") or "[]")
        except (TypeError, ValueError):
            return []
        return [path for path in stored if isinstance(path, str)]

    def set_extra_watch_folders(self, folders):
        self.set_state("extra_watch", json.dumps(
            [str(folder) for folder in folders]))

    def search_moves(self, text, limit=200):
        """Every move whose name, destination or rule contains `text`.

        Across the whole ledger rather than the last page of it. The log
        page used to fetch fifty rows and filter those in the browser, so a
        search for a file moved last week found nothing and said so -- which
        on a page whose entire job is finding a missing file reads as "it is
        gone" rather than "look further back".
        """
        text = (text or "").strip()
        if not text:
            return []
        # `%` and `_` are wildcards to LIKE and ordinary characters to the
        # person typing a filename.
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = "%" + escaped + "%"
        limit = max(1, min(int(limit), 500))
        return self.connection.execute("""
            SELECT m.*, r.action, r.source_root, r.started_at, r.finished_at
              FROM moves m JOIN runs r ON r.id = m.run_id
             WHERE m.source LIKE ? ESCAPE '\\'
                OR m.destination LIKE ? ESCAPE '\\'
                OR m.rule_name LIKE ? ESCAPE '\\'
             ORDER BY m.id DESC LIMIT ?
        """, (like, like, like, limit)).fetchall()

    def undoable_runs(self, actions=("sort",)):
        """Every run that still has completed moves to put back, newest
        first.

        Newest first is not a preference. A file moved twice -- filed, then
        promoted out of holding by a regroup -- has to be walked back in
        the order it was walked forward, or the second undo looks for it
        where the first one has already taken it from.
        """
        places = ",".join("?" for _ in actions)
        return self.connection.execute(
            "SELECT r.id, r.action, r.source_root, COUNT(m.id) AS moves "
            "  FROM runs r JOIN moves m ON m.run_id = r.id "
            " WHERE r.action IN (%s) AND r.dry_run = 0 "
            "   AND m.status = 'done' AND m.operation = 'move' "
            "   AND m.undone_at IS NULL "
            " GROUP BY r.id ORDER BY r.id DESC" % places,
            tuple(actions)).fetchall()

    def latest_undoable_run(self):
        return self.connection.execute("""
            SELECT DISTINCT r.*
              FROM runs r JOIN moves m ON m.run_id = r.id
             WHERE r.action = 'sort' AND m.status = 'done'
             ORDER BY r.id DESC LIMIT 1
        """).fetchone()

    def incomplete_moves(self):
        return self.connection.execute("""
            SELECT m.*, r.dry_run
              FROM moves m JOIN runs r ON r.id = m.run_id
             WHERE m.status IN ('planned', 'moving', 'copying')
               AND r.dry_run = 0
             ORDER BY m.id
        """).fetchall()

    def running_runs(self):
        return self.connection.execute(
            "SELECT * FROM runs WHERE status = 'running' ORDER BY id"
        ).fetchall()

    def move_statuses(self, run_id):
        return self.connection.execute("""
            SELECT status, COUNT(*) AS count
              FROM moves WHERE run_id = ? GROUP BY status
        """, (run_id,)).fetchall()

    # -- persistent daemon state ------------------------------------------

    def get_state(self, key, default=None):
        row = self.connection.execute(
            "SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return default if row is None else row["value"]

    def set_state(self, key, value):
        with self.connection:
            self.connection.execute("""
                INSERT INTO state(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (key, str(value)))

    def paused(self):
        return self.get_state("paused", "0") == "1"

    def set_paused(self, paused, by="user"):
        """Pause or resume, recording who asked.

        Who matters: a pause the daemon took because its rules would not
        parse has to lift by itself once they do, and a pause somebody asked
        for must never be lifted by anything but them.
        """
        self.set_state("paused", "1" if paused else "0")
        self.set_state("paused_by", by if paused else "")

    # -- persistent work queue -------------------------------------------

    def observe_item(self, source_root, primary_path, members, is_dir, reason,
                     sequence, fingerprint, rules_hash, observed_at=None):
        observed_at = time.time() if observed_at is None else observed_at
        source_root = os.path.abspath(source_root)
        primary_path = os.path.abspath(primary_path)
        row = self.connection.execute("""
            SELECT * FROM queue
             WHERE source_root = ? AND primary_path = ?
        """, (source_root, primary_path)).fetchone()
        members_json = json.dumps(list(members), ensure_ascii=False)
        changed = (row is None or row["fingerprint"] != fingerprint
                   or row["rules_hash"] != rules_hash)
        with self.connection:
            if row is None:
                cursor = self.connection.execute("""
                    INSERT INTO queue(
                        source_root, primary_path, members_json, is_dir, reason,
                        sequence_count, fingerprint, rules_hash, stable_since,
                        last_seen, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """, (source_root, primary_path, members_json, int(is_dir),
                        reason, int(sequence), fingerprint, rules_hash,
                        observed_at, observed_at))
                return cursor.lastrowid, True
            if changed:
                self.connection.execute("""
                    UPDATE queue SET
                        members_json = ?, is_dir = ?, reason = ?,
                        sequence_count = ?, fingerprint = ?, rules_hash = ?,
                        stable_since = ?, last_seen = ?, status = 'pending',
                        attempts = 0, retry_at = NULL, error = NULL
                     WHERE id = ?
                """, (members_json, int(is_dir), reason, int(sequence),
                        fingerprint, rules_hash, observed_at, observed_at,
                        row["id"]))
            else:
                self.connection.execute("""
                    UPDATE queue SET members_json = ?, is_dir = ?, reason = ?,
                        sequence_count = ?, last_seen = ? WHERE id = ?
                """, (members_json, int(is_dir), reason, int(sequence),
                        observed_at, row["id"]))
        return row["id"], changed

    def remove_unseen_items(self, source_root, observed_at):
        with self.connection:
            self.connection.execute(
                "DELETE FROM queue WHERE source_root = ? AND last_seen < ?",
                (os.path.abspath(source_root), observed_at))

    def retain_queue_roots(self, source_roots):
        roots = [os.path.abspath(root) for root in source_roots]
        with self.connection:
            if not roots:
                return self.connection.execute("DELETE FROM queue").rowcount
            placeholders = ",".join("?" for _root in roots)
            return self.connection.execute(
                "DELETE FROM queue WHERE source_root NOT IN (%s)"
                % placeholders, roots).rowcount

    def ready_items(self, source_root, stable_before, rules_hash,
                    now_value=None, limit=100):
        now_value = time.time() if now_value is None else now_value
        return self.connection.execute("""
            SELECT * FROM queue
             WHERE source_root = ? AND rules_hash = ?
               AND status IN ('pending', 'failed')
               AND stable_since <= ?
               AND (retry_at IS NULL OR retry_at <= ?)
             ORDER BY stable_since, id LIMIT ?
        """, (os.path.abspath(source_root), rules_hash, stable_before,
                now_value, int(limit))).fetchall()

    def set_queue_status(self, queue_id, status, error=None, retry_at=None,
                         increment_attempt=False):
        with self.connection:
            self.connection.execute("""
                UPDATE queue SET status = ?, error = ?, retry_at = ?,
                    attempts = attempts + ? WHERE id = ?
            """, (status, error, retry_at, int(bool(increment_attempt)),
                    queue_id))

    def recover_processing_queue(self):
        """Make work claimed by a dead daemon eligible for a safe retry."""
        with self.connection:
            cursor = self.connection.execute("""
                UPDATE queue SET status = 'failed', retry_at = NULL,
                    error = 'daemon stopped while processing'
                 WHERE status = 'processing'
            """)
        return cursor.rowcount

    def queue_counts(self):
        return self.connection.execute("""
            SELECT status, COUNT(*) AS count FROM queue GROUP BY status
        """).fetchall()

    def queued_item(self, queue_id):
        return self.connection.execute(
            "SELECT * FROM queue WHERE id = ?", (queue_id,)).fetchone()

    def completed_destinations(self, source_root=None):
        sql = """
            SELECT DISTINCT m.destination
              FROM moves m JOIN runs r ON r.id = m.run_id
             WHERE r.action = 'sort' AND m.status IN ('done', 'copied')
        """
        parameters = ()
        if source_root is not None:
            sql += " AND r.source_root = ?"
            parameters = (os.path.abspath(source_root),)
        return [row["destination"] for row in
                self.connection.execute(sql, parameters).fetchall()]
