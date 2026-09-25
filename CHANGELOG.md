# Changes

## Unreleased

- Problem reports now select known diagnostic fields instead of attempting
  to redact arbitrary text. Folder paths, learned names, raw errors and
  unknown system labels are omitted in text, JSON and fallback reports.
- Regression checks cover paths with spaces, unquoted document headings,
  invalid rules, saved tray errors, desktop metadata and broken-install
  reporting. The batch fallback check runs on Windows CI.

## 0.9.0 — the first release

auto-sort empties a Downloads folder and keeps it empty. It works out what
each file is from its bytes rather than its name, learns the kinds of
document you keep from what the documents call themselves, files everything
into the folders your computer already has, and writes down every move so
that all of it can be undone. Standard library Python, no dependencies, no
account, no network calls.

### Install

macOS and Linux, in a terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/snepssen/auto-sort/main/install.sh | sh
```

Windows, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/snepssen/auto-sort/main/install.ps1 | iex"
```

Run the same line again to update. The first sort is shown as a preview on
the log page and starts by itself fifteen minutes later unless you pause it;
every move can be undone.

### Why 0.9 and not 1.0

| | |
| --- | --- |
| macOS | Run for real, daily. |
| Linux, KDE Plasma | Run for real on a Steam Deck: tests, first run, tray icon and menu checked on screen. |
| Linux, other trays | Built to the StatusNotifierItem specification, not yet seen. |
| Windows | Built to the Win32 documentation; tests and the install line pass on GitHub's Windows machines. Not yet seen on a Windows desktop. |

If it does not work for you, double-click `REPORT-A-PROBLEM` in the folder
it was installed in and send what it writes to your Desktop. It names none
of your files.

### In this release

- **It learns by itself.** Documents waiting in a holding folder that turn
  out to share a heading — three invoices, four bank statements — become a
  folder of their own, and move into it. A category you delete stays
  deleted.
- **It goes back for what it filed.** When the rules or the reader change,
  filed files are judged again and moved where they now belong, a few at a
  time so the tray keeps answering.
- **It reads what documents say**: PDFs (including encrypted ones that open
  without a password, and scans, with the Mac's own text recognition or
  tesseract), Word old and new, OpenDocument, RTF, Pages, Markdown, email,
  calendar invites, web pages, and spreadsheets old and new.
- **One line to install**, on every system, with the optional programs,
  starting at login, and updating offered as it goes.
- **A tray icon on Linux**, spoken to D-Bus with the standard library.
- **REPORT-A-PROBLEM** and `auto-sort diagnose`, for the machines nobody
  here has.
- **Continuous testing** on Windows, Ubuntu and macOS, Python 3.8 and 3.13.

### Known limits

- Windows: deleted files go to a `Trash (auto-sort)` folder, not the
  Recycle Bin, until the Recycle Bin code can be checked on a real machine.
- PDFs encrypted with AES-256, or locked with a password auto-sort does not
  have, are set aside in `Documents/PDF/Encrypted/<day>` rather than read.
- A learnt folder is named after the phrase its documents share; where
  that phrase runs on into a small word ("Steuerbescheid für"), the name
  does too.
