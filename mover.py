"""Filesystem primitives that never overwrite and verify before removing.

The fast path is a same-volume rename (or hard-link-and-unlink for a regular
file), which preserves the inode and all metadata.  The cross-volume path is
copy to a private temporary name, hash it, install it without replacement,
and only then remove the source.  There is intentionally no call to
``shutil.move``: its cross-volume fallback hides precisely the ordering this
project needs to guarantee.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import shutil
import stat
import sys
import uuid

import paths


class MoveError(Exception):
    pass


class TransferResult(object):
    def __init__(self, copied=False, source_removed=False):
        self.copied = copied
        self.source_removed = source_removed


_COPYFILE_ALL = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3)
_COPYFILE_RECURSIVE = 1 << 15
_COPYFILE_EXCL = 1 << 17
_COPYFILE_NOFOLLOW_SRC = 1 << 18
_COPYFILE_CLONE = 1 << 24


def hash_path(path):
    """A stable SHA-256 for a file or opaque package directory."""
    digest = hashlib.sha256()
    status = os.lstat(path)
    if stat.S_ISLNK(status.st_mode):
        digest.update(b"symlink\0")
        digest.update(os.fsencode(os.readlink(path)))
        return digest.hexdigest()
    if not stat.S_ISDIR(status.st_mode):
        _hash_file(path, digest)
        return digest.hexdigest()

    digest.update(b"directory\0")
    for directory, subdirectories, filenames in os.walk(path,
                                                          followlinks=False):
        subdirectories.sort()
        filenames.sort()
        relative_dir = os.path.relpath(directory, path)
        digest.update(os.fsencode(relative_dir))
        digest.update(b"\0")
        for name in list(subdirectories) + filenames:
            member = os.path.join(directory, name)
            relative = os.path.relpath(member, path)
            member_status = os.lstat(member)
            digest.update(os.fsencode(relative))
            digest.update(b"\0")
            digest.update(str(stat.S_IFMT(member_status.st_mode)).encode("ascii"))
            digest.update(b"\0")
            if stat.S_ISLNK(member_status.st_mode):
                digest.update(os.fsencode(os.readlink(member)))
            elif stat.S_ISREG(member_status.st_mode):
                _hash_file(member, digest)
            digest.update(b"\0")
    return digest.hexdigest()


def _hash_file(filename, digest):
    with open(filename, "rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)


def size_path(path):
    status = os.lstat(path)
    if not stat.S_ISDIR(status.st_mode):
        return status.st_size
    total = 0
    for directory, subdirectories, filenames in os.walk(path,
                                                          followlinks=False):
        subdirectories.sort()
        for name in filenames:
            try:
                member_status = os.lstat(os.path.join(directory, name))
            except OSError:
                continue
            if stat.S_ISREG(member_status.st_mode):
                total += member_status.st_size
    return total


def unsafe_to_copy(path):
    """Why byte-copying this path would silently change its storage identity."""
    try:
        status = os.lstat(path)
    except OSError as error:
        return "cannot inspect source: %s" % error
    if stat.S_ISLNK(status.st_mode):
        return "symbolic links are never followed or copied"
    if not (stat.S_ISREG(status.st_mode) or stat.S_ISDIR(status.st_mode)):
        return "special filesystem entries are not copied"
    if stat.S_ISREG(status.st_mode) and status.st_nlink > 1:
        return "file has %d hard links" % status.st_nlink
    if stat.S_ISDIR(status.st_mode):
        for directory, subdirectories, filenames in os.walk(
                path, followlinks=False):
            for name in list(subdirectories) + filenames:
                member = os.path.join(directory, name)
                try:
                    member_status = os.lstat(member)
                except OSError as error:
                    return "cannot inspect package member: %s" % error
                if stat.S_ISREG(member_status.st_mode) \
                        and member_status.st_nlink > 1:
                    return "package contains a hard-linked file"
                if not (stat.S_ISREG(member_status.st_mode)
                        or stat.S_ISDIR(member_status.st_mode)
                        or stat.S_ISLNK(member_status.st_mode)):
                    return "package contains a special filesystem entry"
    return None


def same_volume(source, destination):
    parent = nearest_existing(os.path.dirname(destination))
    try:
        return os.lstat(source).st_dev == os.stat(parent).st_dev
    except OSError:
        return False


def exclusively_available(path):
    """Whether Windows can open a regular file without sharing the handle.

    Settle timestamps catch most partial downloads.  Windows gives us one
    extra useful signal: a writer that still has the file open will normally
    make this zero-share open fail.  Other platforms have no portable
    equivalent, so they rely on repeated stat fingerprints and final hashes.
    """
    if os.name != "nt" or not os.path.isfile(path):
        return True
    import winapi                                     # pragma: no cover
    kernel = winapi.load("kernel32")                  # pragma: no cover
    handle = kernel.CreateFileW(path, 0x80000000, 0, None, 3, 0, None)
    invalid = ctypes.c_void_p(-1).value
    if handle is None or handle == invalid:           # pragma: no cover
        return False
    kernel.CloseHandle(handle)                        # pragma: no cover
    return True


def nearest_existing(path):
    candidate = os.path.abspath(path)
    while not os.path.exists(candidate):
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    return candidate


def transfer(source, destination, operation="move", expected_hash=None,
             preserve_dates=True):
    """Move or copy one member, returning what happened.

    The caller must have chosen a collision-free destination.  This function
    checks again at the last responsible moment and will never replace it.
    """
    source = os.path.abspath(source)
    destination = os.path.abspath(destination)
    if operation not in ("move", "copy"):
        raise MoveError("unknown operation %r" % operation)
    if not os.path.lexists(source):
        raise MoveError("source disappeared: %s" % source)
    if os.path.lexists(destination):
        raise MoveError("destination appeared: %s" % destination)
    status = os.lstat(source)
    if stat.S_ISLNK(status.st_mode):
        raise MoveError("refusing to follow symbolic link: %s" % source)
    if expected_hash is not None:
        current_hash = hash_path(source)
        if current_hash != expected_hash:
            raise MoveError("source changed after it was planned: %s" % source)

    paths.ensure(os.path.dirname(destination))
    if operation == "move" and same_volume(source, destination):
        _move_same_volume(source, destination)
        return TransferResult(copied=False, source_removed=True)

    unsafe = unsafe_to_copy(source)
    if unsafe:
        raise MoveError("refusing byte copy: %s" % unsafe)
    copied_hash = _copy_verified(source, destination, expected_hash,
                                 preserve_dates)
    if expected_hash is not None and copied_hash != expected_hash:
        raise MoveError("copy verification failed: %s" % destination)
    if operation == "copy":
        return TransferResult(copied=True, source_removed=False)

    try:
        _remove_source(source)
    except OSError as error:
        # The verified destination is valuable evidence and may be the only
        # copy if a removable source vanished.  Never delete it to make the
        # operation look atomic; report that the source could not be removed.
        raise MoveError("verified copy installed, but source remains: %s"
                        % error)
    return TransferResult(copied=True, source_removed=True)


def _move_same_volume(source, destination):
    status = os.lstat(source)
    if stat.S_ISREG(status.st_mode):
        try:
            os.link(paths.long_path(source), paths.long_path(destination),
                    follow_symlinks=False)
        except (OSError, NotImplementedError) as error:
            # Filesystems without hard links still get the platform's
            # no-replace rename where one exists.  Do not hide an existing
            # destination behind the fallback.
            if os.path.lexists(destination):
                raise MoveError("destination appeared: %s" % destination)
            if isinstance(error, OSError) and error.errno in (
                    errno.EEXIST, errno.ENOTEMPTY):
                raise MoveError("destination appeared: %s" % destination)
        else:
            try:
                os.unlink(paths.long_path(source))
            except OSError as error:
                # Undo the link we just created so the failed operation leaves
                # exactly the namespace it found.
                try:
                    os.unlink(paths.long_path(destination))
                except OSError:
                    pass
                raise MoveError("linked destination but could not remove "
                                "source: %s" % error)
            return
    _rename_noreplace(source, destination)


def _rename_noreplace(source, destination):
    if os.path.lexists(destination):
        raise MoveError("destination appeared: %s" % destination)

    if os.name == "nt":                                    # pragma: no cover
        import winapi
        # Flags 0: fails rather than replacing, and fails across volumes,
        # which the caller handles by copying.
        if winapi.load("kernel32").MoveFileExW(source, destination, 0):
            return
        raise MoveError("rename failed (%d): %s"
                        % (winapi.last_error(), source))

    libc = None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError:
        pass
    encoded_source = os.fsencode(source)
    encoded_destination = os.fsencode(destination)

    if sys.platform == "darwin" and libc is not None \
            and hasattr(libc, "renamex_np"):               # pragma: no branch
        # RENAME_EXCL: fail if the destination exists.
        if libc.renamex_np(encoded_source, encoded_destination, 0x00000004) == 0:
            return
        error = ctypes.get_errno()
        raise MoveError("rename failed: %s" % os.strerror(error))

    if sys.platform.startswith("linux") and libc is not None \
            and hasattr(libc, "renameat2"):                # pragma: no cover
        at_fdcwd = -100
        if libc.renameat2(at_fdcwd, encoded_source, at_fdcwd,
                          encoded_destination, 1) == 0:
            return
        error = ctypes.get_errno()
        if error not in (errno.ENOSYS, errno.EINVAL):
            raise MoveError("rename failed: %s" % os.strerror(error))

    # Old Unix fallback.  The existence check is not atomic, but os.rename is
    # only reached for directories and unusual filesystems without the native
    # no-replace operation.  Re-check immediately before it and never use
    # os.replace.
    if os.path.lexists(destination):
        raise MoveError("destination appeared: %s" % destination)
    try:
        os.rename(source, destination)
    except OSError as error:
        raise MoveError("rename failed: %s" % error)


def _copy_verified(source, destination, expected_hash, preserve_dates):
    parent = os.path.dirname(destination)
    temporary = os.path.join(parent, ".autosort-%s.tmp" % uuid.uuid4().hex)
    try:
        if not _macos_copy(source, temporary):
            if os.path.isdir(source):
                shutil.copytree(source, temporary, symlinks=True,
                                copy_function=shutil.copy2)
            else:
                # copy2 also preserves mode and extended attributes where
                # Python exposes them.  Those are not optional provenance.
                shutil.copy2(source, temporary)
        if not preserve_dates:
            _touch_tree(temporary)
        copied_hash = hash_path(temporary)
        if expected_hash is not None and copied_hash != expected_hash:
            raise MoveError("temporary copy did not match source")
        _rename_noreplace(temporary, destination)
        return copied_hash
    except Exception:
        _remove_temporary(temporary)
        raise


def _macos_copy(source, destination):
    """Use copyfile(3) when Python cannot expose macOS metadata itself."""
    if sys.platform != "darwin":
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        function = libc.copyfile
        function.restype = ctypes.c_int
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                             ctypes.c_void_p, ctypes.c_uint32]
        flags = (_COPYFILE_ALL | _COPYFILE_EXCL | _COPYFILE_NOFOLLOW_SRC
                 | _COPYFILE_CLONE)
        if os.path.isdir(source):
            flags |= _COPYFILE_RECURSIVE
        result = function(os.fsencode(source), os.fsencode(destination),
                          None, flags)
    except (AttributeError, OSError):
        return False
    if result != 0:
        error = ctypes.get_errno()
        raise MoveError("native metadata-preserving copy failed: %s"
                        % os.strerror(error))
    return True


def _touch_tree(path):
    moment = None
    if os.path.isdir(path):
        for directory, subdirectories, filenames in os.walk(path,
                                                              followlinks=False):
            for name in filenames:
                member = os.path.join(directory, name)
                if not os.path.islink(member):
                    os.utime(member, moment)
            for name in subdirectories:
                member = os.path.join(directory, name)
                if not os.path.islink(member):
                    os.utime(member, moment)
    os.utime(path, moment)


def _remove_source(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def _remove_temporary(path):
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.lexists(path):
            os.unlink(path)
    except OSError:
        pass
