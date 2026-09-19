"""The ISO base media box tree, which audio and video both live inside.

An `.m4a` and an `.mp4` are the same container with different tracks in it, so
the walker belongs to neither reader. Keeping it here also keeps the recursion
depth and result limits in one place: these files come off the internet, and a
box tree that claims to nest four hundred deep is a file that should be
declined rather than followed.
"""

from __future__ import annotations

CONTAINERS = {"moov", "trak", "mdia", "minf", "stbl", "udta", "meta", "ilst",
              "edts", "mvex", "tref", "iprp", "ipco"}


def atoms(data, want, limit=64, max_depth=8):
    """[(path, body)] for every box whose name is in `want`."""
    results = []

    def walk(buffer, depth, trail):
        cursor = 0
        while cursor + 8 <= len(buffer) and depth < max_depth \
                and len(results) < limit:
            try:
                size = int.from_bytes(buffer[cursor:cursor + 4], "big")
                name = buffer[cursor + 4:cursor + 8].decode("latin-1")
            except (ValueError, IndexError):
                return
            header = 8
            if size == 1:
                size = int.from_bytes(buffer[cursor + 8:cursor + 16], "big")
                header = 16
            if size < header or cursor + size > len(buffer) + 8:
                size = len(buffer) - cursor
                if size < header:
                    return
            body = buffer[cursor + header:cursor + size]
            path = trail + "/" + name
            if name in want:
                results.append((path, body))
            if name in CONTAINERS:
                # `meta` is a full box: four bytes of version and flags sit
                # between the header and the first child.
                walk(body[4:] if name == "meta" else body, depth + 1, path)
            cursor += max(size, 8)

    walk(data, 0, "")
    return results
