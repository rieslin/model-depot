"""SHA-256 of model files, computed in a worker thread: it can be paused, and stopping the program in the middle of
a file loses only that file's progress (finished files are already in the index)."""
import hashlib
import os
import threading

CHUNK_BYTES = 4 * 1024 * 1024


class Aborted(Exception):
    """The program is stopping."""


class Changed(Exception):
    """The file is not the one the index described (it was replaced or is still being written)."""


class Control:
    """Pause, resume and stop, shared between the server and the hashing threads."""

    def __init__(self):
        self._go = threading.Event()
        self._go.set()
        self._abort = threading.Event()

    @property
    def paused(self):
        return not self._go.is_set()

    @property
    def aborted(self):
        return self._abort.is_set()

    def pause(self):
        self._go.clear()

    def resume(self):
        self._go.set()

    def abort(self):
        self._abort.set()
        self._go.set()

    def checkpoint(self):
        """Called by the hashing thread between chunks: waits while paused, raises once stopping."""
        if self._abort.is_set():
            raise Aborted()
        if not self._go.is_set():
            self._go.wait()
            if self._abort.is_set():
                raise Aborted()


def sha256_file(path, expected_bytes, expected_mtime_ns, control=None, on_bytes=None):
    """Lower-case hex SHA-256 of the file, provided it still has the size and modification time the index recorded,
    before and after reading."""
    st = os.stat(path)
    if st.st_size != expected_bytes or st.st_mtime_ns != expected_mtime_ns:
        raise Changed(path)
    digest = hashlib.sha256()
    buf = bytearray(CHUNK_BYTES)
    view = memoryview(buf)
    with open(path, "rb", buffering=0) as f:
        while True:
            if control is not None:
                control.checkpoint()
            n = f.readinto(view)
            if not n:
                break
            digest.update(view[:n])
            if on_bytes is not None:
                on_bytes(n)
    st = os.stat(path)
    if st.st_size != expected_bytes or st.st_mtime_ns != expected_mtime_ns:
        raise Changed(path)
    return digest.hexdigest()
