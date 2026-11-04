"""A video example on its way to the cache folder, played while it arrives.

One fetch per video, whoever asks: the bytes go to a .part file, each viewer reads from that file at its own pace, and a
complete file takes its final name. When the last viewer leaves, a fetch nobody else needs stops (flicking through the
examples leaves nothing downloading), while one that filling in the details asked for (`keep`) carries on.
"""
import asyncio
import logging
import os

import aiohttp

from .civitai import CivitaiError

log = logging.getLogger("depot.videos")

_PIECE = 256 * 1024
_MAX_BYTES = 1024 ** 3      # a transcoded example; an answer larger than this is not one
_HEAD = 12


def _is_video(head):
    return head[4:8] == b"ftyp" or head[:4] == b"\x1a\x45\xdf\xa3"      # MP4, or WebM


class VideoFetch:
    def __init__(self, client, url, path, interactive=False, force=False, on_end=None):
        self.path = path
        self.part = path + ".part"
        self.size = None            # as the answer states it
        self.written = 0
        self.done = False
        self.error = None
        self.keep = False           # goes on when nobody is watching
        self.watchers = 0
        self.abandoned = False
        self._client, self._url, self._interactive, self._force, self._on_end = client, url, interactive, force, on_end
        self._news = asyncio.Event()
        self.task = asyncio.ensure_future(self._run())

    async def _run(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            async with self._client.open_video(self._url, self._interactive, self._force) as resp:
                if resp.content_length is not None:
                    self.size = resp.content_length
                head = await resp.content.readexactly(_HEAD)
                if not _is_video(head):
                    raise CivitaiError("not_a_file", "not a video")
                with open(self.part, "wb") as f:
                    f.write(head)
                    f.flush()
                    self.written = len(head)
                    self._news.set()
                    async for piece in resp.content.iter_chunked(_PIECE):
                        if self.written + len(piece) > _MAX_BYTES:
                            raise CivitaiError("too_large", "video")
                        f.write(piece)
                        f.flush()
                        self.written += len(piece)
                        self._news.set()
            if self.size is not None and self.written != self.size:
                raise CivitaiError("network", "the video was cut short")
            os.replace(self.part, self.path)
            self.done = True
        except CivitaiError as e:
            self.error = e
        except (aiohttp.ClientError, asyncio.TimeoutError, EOFError, OSError) as e:
            self.error = CivitaiError("network", type(e).__name__)
        finally:
            if not self.done:
                self._remove_part()
                if self.error is None:      # stopped
                    self.error = CivitaiError("network", "stopped")
            if self.error is not None:
                log.info("video %s not fetched: %s", os.path.basename(self.path), self.error)
            if self._on_end is not None:
                self._on_end()
            self._news.set()

    def _remove_part(self):
        try:
            os.remove(self.part)
        except OSError:
            pass

    async def _wait(self):
        self._news.clear()
        await self._news.wait()

    async def started(self):
        """Back once the first bytes are here; raises what stopped the fetch before that."""
        while not self.written and not self.done:
            if self.error is not None:
                raise self.error
            await self._wait()

    async def ended(self):
        """Back when the fetch is over, however it ended."""
        await asyncio.wait([self.task])

    async def follow(self):
        """The file's bytes from the start, as they arrive. Raises what stopped the fetch."""
        sent = 0
        while True:
            if sent < self.written:
                # Opened and closed each time: Windows does not rename a file that is open.
                with open(self.path if self.done else self.part, "rb") as f:
                    f.seek(sent)
                    piece = f.read(_PIECE)
                sent += len(piece)
                yield piece
            elif self.done:
                return
            elif self.error is not None:
                raise self.error
            else:
                await self._wait()

    def leave(self):
        """A viewer is gone."""
        self.watchers -= 1
        if self.watchers <= 0 and not self.keep and not self.task.done():
            self.abandoned = True
            self.task.cancel()
