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


SCHEMA_VERSION = 2


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")


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
                 status="planned"):
        with self.connection:
            cursor = self.connection.execute("""
                INSERT INTO moves(
                    run_id, item_number, member_number, operation, rule_name,
                    source, destination, size, sha256, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (run_id, item_number, member_number, operation, rule_name,
                    source, destination, int(size), sha256, status, now()))
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

    def set_paused(self, paused):
        self.set_state("paused", "1" if paused else "0")

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
