# Diagnostic privacy contract

The automatically generated diagnostics contain no file names, paths,
document text or rule names. Nothing is uploaded. This contract covers `diagnose`,
`diagnose --json`, the Desktop report and the short reports made by
`REPORT-A-PROBLEM` when Python or auto-sort cannot start.

| Source | Included in the report |
| --- | --- |
| Application and Python | Numeric versions, Git commit when available, known interpreter label and bitness |
| Operating system | Known OS/distribution label, numeric version, numeric Linux kernel prefix, known Mac architecture |
| Desktop environment | Known desktop/session labels; display and bus addresses only as set/not set |
| Session bus | Reachability, numeric error code when available, watcher presence, host registration, known notification-server label and numeric versions |
| Daemon and ledger | Running/paused state, counts grouped by known statuses |
| Saved tray report | Known backend and watcher labels, boolean states, counts of known protocol methods |
| Rules | Whether they load, rule and watched-folder counts, validated setting choices |
| Standard folders | Available or missing/inaccessible, without their locations |
| Optional programs | Known program labels and installed/not-installed state |
| Recent log problems | Fixed categories, timestamps and repetition counts; no original message text |
| Fallback scripts | Known OS/session label, Python availability and whether the full report failed |

Unknown strings are omitted or replaced with a fixed label. In particular,
reports do not copy exception messages, traceback source lines, distribution
pretty names, custom kernel suffixes, notification vendor strings or raw
saved tray reasons. Rules can contain words learned from private documents,
so even parser errors must not be copied. The shell and batch fallbacks
discard stderr without saving or attaching it; they cannot depend on a
working Python installation to sanitise it.

The local daemon log and rules are not public reports. They still contain
the details needed to investigate locally. Descriptions and screenshots
added by a person are outside the generated-data guarantee and should be
reviewed before posting. Versions, timestamps and counts are diagnostic
information, so this is not a promise of anonymity.

## Maintaining the boundary

Sanitisation happens in the collectors, before text or JSON rendering.
Adding a field requires selecting known values or a constrained numeric
format. Do not pass through arbitrary strings or add an exception-message
fallback. Unknown log messages must remain a fixed category.

Run `python3 -m unittest discover -s tests -p test_diagnose.py -v`.
The checks exercise real log shapes, redirected folders, invalid rules,
stored tray data and system metadata with synthetic private values. They
also run the shell fallback with a failed or missing interpreter, and the
batch fallback with a broken installation on Windows. Windows execution is
covered by the existing CI matrix, not simulated on macOS.
