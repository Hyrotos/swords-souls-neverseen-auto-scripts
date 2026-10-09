"""Capture a chosen game window through the Wayland ScreenCast portal."""

from __future__ import annotations

import asyncio
import os
import select
import subprocess
import threading
import time
import uuid
from shutil import which

import numpy as np
from dbus_next import Message, MessageType, Variant
from dbus_next.aio import MessageBus


_PORTAL_BUS = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_SCREENCAST = "org.freedesktop.portal.ScreenCast"


def _unwrap(value):
    return value.value if isinstance(value, Variant) else value


class WaylandWindowCapture:
    """Read a user-authorized, selected window from PipeWire as BGRX frames."""

    def __init__(self, width: int, height: int) -> None:
        if width < 400 or height < 300:
            raise ValueError(f"invalid game capture size: {width}x{height}")
        self.width = width
        self.height = height
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="wayland-screen-capture",
            daemon=True,
        )
        self._thread.start()
        self._bus = None
        self._pending: dict[str, asyncio.Future] = {}
        self._session_handle: str | None = None
        self._pipewire_fd: int | None = None
        self._process: subprocess.Popen | None = None

        future = asyncio.run_coroutine_threadsafe(
            self._start_session(), self._loop
        )
        try:
            future.result(timeout=180)
        except Exception:
            self.close()
            raise

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        self._loop.close()

    def _on_message(self, message: Message) -> bool:
        if (
            message.message_type == MessageType.SIGNAL
            and message.interface == "org.freedesktop.portal.Request"
            and message.member == "Response"
        ):
            future = self._pending.get(message.path)
            if future is not None and not future.done():
                future.set_result(message.body)
        return False

    async def _portal_request(
        self,
        member: str,
        signature: str,
        body: list,
    ) -> dict:
        token = uuid.uuid4().hex
        sender = self._bus.unique_name[1:].replace(".", "_")
        expected_path = f"{_PORTAL_PATH}/request/{sender}/{token}"
        future = self._loop.create_future()
        self._pending[expected_path] = future
        message = Message(
            destination=_PORTAL_BUS,
            path=_PORTAL_PATH,
            interface=_SCREENCAST,
            member=member,
            signature=signature,
            body=body,
        )
        try:
            reply = await self._bus.call(message)
            if reply.message_type == MessageType.ERROR:
                raise RuntimeError(
                    f"portal {member} failed: {reply.error_name}: {reply.body}"
                )
            request_path = reply.body[0]
            self._pending[request_path] = future
            response = await asyncio.wait_for(future, timeout=180)
            status, results = response
            if status != 0:
                detail = {key: _unwrap(value) for key, value in results.items()}
                raise RuntimeError(
                    f"screen sharing request {member} was cancelled or denied "
                    f"(status {status}): {detail}"
                )
            return {key: _unwrap(value) for key, value in results.items()}
        finally:
            self._pending.pop(expected_path, None)
            if "request_path" in locals():
                self._pending.pop(request_path, None)

    async def _start_session(self) -> None:
        self._bus = await MessageBus(negotiate_unix_fd=True).connect()
        self._bus.add_message_handler(self._on_message)

        handle_token = uuid.uuid4().hex
        session_token = uuid.uuid4().hex
        created = await self._portal_request(
            "CreateSession",
            "a{sv}",
            [
                {
                    "handle_token": Variant("s", handle_token),
                    "session_handle_token": Variant("s", session_token),
                }
            ],
        )
        self._session_handle = created["session_handle"]

        selected = await self._portal_request(
            "SelectSources",
            "oa{sv}",
            [
                self._session_handle,
                {
                    # Source type 2 is a window; never request the whole desktop.
                    "types": Variant("u", 2),
                    "multiple": Variant("b", False),
                    "cursor_mode": Variant("u", 4),
                },
            ],
        )
        del selected

        started = await self._portal_request(
            "Start", "osa{sv}", [self._session_handle, "", {}]
        )
        streams = started.get("streams", [])
        if not streams:
            raise RuntimeError("the screen-sharing portal returned no window stream")
        node_id, properties = streams[0]
        properties = {key: _unwrap(value) for key, value in properties.items()}
        size = properties.get("size")
        if size and len(size) == 2:
            self.width, self.height = int(size[0]), int(size[1])
        print(
            f"Wayland portal stream: node={node_id}, "
            f"size={self.width}x{self.height}, properties={properties}",
            flush=True,
        )

        if self.width < 400 or self.height < 300:
            raise RuntimeError(
                f"the screen-sharing portal returned an invalid frame size: "
                f"{self.width}x{self.height}"
            )

        remote = await self._bus.call(
            Message(
                destination=_PORTAL_BUS,
                path=_PORTAL_PATH,
                interface=_SCREENCAST,
                member="OpenPipeWireRemote",
                signature="oa{sv}",
                body=[self._session_handle, {}],
            )
        )
        if remote.message_type == MessageType.ERROR:
            raise RuntimeError(
                f"could not open portal PipeWire stream: "
                f"{remote.error_name}: {remote.body}"
            )
        fd_index = int(remote.body[0])
        self._pipewire_fd = os.dup(remote.unix_fds[fd_index])

        if which("gst-launch-1.0") is None:
            raise RuntimeError(
                "gst-launch-1.0 is missing; install GStreamer and its PipeWire plugin"
            )
        pipeline = [
            "gst-launch-1.0",
            "-q",
            "pipewiresrc",
            f"fd={self._pipewire_fd}",
            f"path={node_id}",
            "do-timestamp=true",
            "!",
            "videoconvert",
            "!",
            "videoscale",
            "!",
            f"video/x-raw,format=BGRx,width={self.width},height={self.height}",
            "!",
            "queue",
            "max-size-buffers=1",
            "max-size-bytes=0",
            "max-size-time=0",
            "leaky=downstream",
            "!",
            "fdsink",
            "fd=1",
            "sync=false",
        ]
        self._process = subprocess.Popen(
            pipeline,
            pass_fds=(self._pipewire_fd,),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        first_frame = np.frombuffer(
            self._read_frame(timeout=15.0), dtype=np.uint8
        ).reshape(self.height, self.width, 4)[:, :, :3]
        if not np.any(first_frame):
            raise RuntimeError(
                "the Wayland portal stream opened but delivered a black frame; "
                "select the game window itself in the sharing dialog"
            )
        print(
            f"First Wayland frame received: "
            f"{np.count_nonzero(first_frame)} non-black color channels.",
            flush=True,
        )

    def grab(self) -> np.ndarray:
        """Return one fresh frame in BGR channel order."""
        raw = self._read_frame(timeout=2.0)
        return np.frombuffer(raw, dtype=np.uint8).reshape(
            self.height, self.width, 4
        )[:, :, :3]

    def _read_frame(self, timeout: float) -> bytes:
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("the Wayland capture stream is not running")
        frame_size = self.width * self.height * 4
        frame = bytearray(frame_size)
        view = memoryview(frame)
        offset = 0
        deadline = time.monotonic() + timeout
        fd = self._process.stdout.fileno()
        while offset < frame_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for a PipeWire video frame")
            readable, _, _ = select.select([fd], [], [], remaining)
            if not readable:
                raise TimeoutError("timed out waiting for a PipeWire video frame")
            chunk = os.read(fd, frame_size - offset)
            if not chunk:
                status = self._process.poll()
                error_output = b""
                if self._process.stderr is not None:
                    error_output = self._process.stderr.read()
                raise RuntimeError(
                    f"the GStreamer PipeWire stream ended unexpectedly "
                    f"(exit code {status}): "
                    f"{error_output.decode(errors='replace').strip()}"
                )
            view[offset : offset + len(chunk)] = chunk
            offset += len(chunk)
        return bytes(frame)

    async def _close_session(self) -> None:
        try:
            if self._session_handle is not None and self._bus is not None:
                await self._bus.call(
                    Message(
                        destination=_PORTAL_BUS,
                        path=self._session_handle,
                        interface="org.freedesktop.portal.Session",
                        member="Close",
                    )
                )
        except Exception:
            pass
        finally:
            self._session_handle = None
            if self._bus is not None:
                self._bus.disconnect()
                self._bus = None

    def close(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._process = None
        if self._pipewire_fd is not None:
            os.close(self._pipewire_fd)
            self._pipewire_fd = None
        if self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._close_session(), self._loop
            )
            try:
                future.result(timeout=3)
            except Exception:
                future.cancel()
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread.is_alive():
            self._thread.join(timeout=3)
