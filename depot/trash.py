"""Moving files to the recycle bin. Model Depot never deletes a file outright: what it removes goes where Windows' own
Delete key would put it, so it can be restored from there. A location without a recycle bin (a network share, a USB
stick) is refused rather than deleted from for good, and so is every system other than Windows, for now.
"""
import ctypes
import os
import sys


class TrashError(Exception):
    """code: unsupported (not Windows) | no_recycle_bin (the drive has none) | failed (Windows refused; the files are
    probably in use)"""

    def __init__(self, code, detail=""):
        super().__init__("%s %s" % (code, detail))
        self.code = code
        self.detail = detail


_DRIVE_FIXED = 3


def has_recycle_bin(path):
    """Does the drive this path is on keep a recycle bin? Only fixed local drives do."""
    if sys.platform != "win32":
        return False
    root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    if root.startswith("\\\\"):        # \\server\share: a network location
        return False
    return ctypes.windll.kernel32.GetDriveTypeW(root) == _DRIVE_FIXED


def to_recycle_bin(paths):
    """Move these files to the recycle bin in one go. Raises TrashError; on success every file has left its folder."""
    paths = [os.path.abspath(p) for p in paths]
    if not paths:
        return
    if sys.platform != "win32":
        raise TrashError("unsupported", sys.platform)
    for p in paths:
        if not has_recycle_bin(p):
            raise TrashError("no_recycle_bin", p)
    _shell_delete(paths)
    left = [p for p in paths if os.path.exists(p)]
    if left:
        raise TrashError("failed", "; ".join(left))


def _shell_delete(paths):
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE = 3
    # to the recycle bin (ALLOWUNDO), with no dialog of any kind: no question, no progress, no error box
    flags = 0x0040 | 0x0010 | 0x0004 | 0x0400 | 0x0200     # ALLOWUNDO NOCONFIRMATION SILENT NOERRORUI NOCONFIRMMKDIR
    # the list ends with two NULs: one after each name, and one more after the last
    names = ctypes.create_unicode_buffer("\0".join(paths) + "\0\0")
    op = SHFILEOPSTRUCTW(None, FO_DELETE, ctypes.cast(names, wintypes.LPCWSTR), None, flags, False, None, None)
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0 or op.fAnyOperationsAborted:
        raise TrashError("failed", "SHFileOperation 0x%x" % (result & 0xFFFFFFFF))
