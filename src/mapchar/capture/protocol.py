"""The line protocol between mapchar and the scripts in the emulator, and the
steps capture's long work is made of.

Capture's work is written as generators: a step yields ``WAIT`` while it waits
on an emulator (a line not yet arrived, a process not yet finished) and
``None`` after a piece of its own work, and returns its result. The session
resumes them from the UI's timer a bounded while at a time, so the GUI thread
never blocks; :func:`drive` runs one to the end for a test or a script.

Lines are whole in both directions: a line that arrives in pieces is kept
until its newline, and every wait has a deadline.
"""

from __future__ import annotations

import socket
import subprocess
import time
from collections.abc import Callable, Generator
from typing import Any, TypeVar

WAIT = True
"""What a step yields while it waits on something outside."""

T = TypeVar("T")
Step = Generator[Any, None, T]


class CaptureError(Exception):
    """Something outside mapchar failed: the emulator, a script, a socket."""


class Timeout(CaptureError):
    """No answer by the deadline."""


class Closed(CaptureError):
    """The other end has gone."""


def drive(step: Step[T], timeout: float | None = None) -> T:
    """Run a step to its end, sleeping briefly while it waits."""
    end = None if timeout is None else time.monotonic() + timeout
    while True:
        try:
            y = next(step)
        except StopIteration as stop:
            return stop.value
        if y is WAIT:
            time.sleep(0.002)
        if end is not None and time.monotonic() > end:
            step.close()
            raise Timeout("the step did not finish in time")


def wait_process(proc: subprocess.Popen, deadline: float) -> Step[int | None]:
    """Wait for a process to end: its exit status, or None at the deadline
    with the process left as it is — what ends it is the caller's to say."""
    while proc.poll() is None:
        if time.monotonic() > deadline:
            return None
        yield WAIT
    return proc.returncode


class Lines:
    """One connection, read and written a whole line at a time."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        sock.setblocking(False)
        try:  # a probe is a short line each way: no waiting to fill a packet
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        self.buf = b""
        self.closed = False

    def send(self, line: str) -> None:
        self.sock.setblocking(True)
        self.sock.settimeout(30)
        try:
            self.sock.sendall(line.encode() + b"\n")
        except OSError as e:
            raise Closed(str(e)) from e
        finally:
            self.sock.setblocking(False)

    def poll(self) -> str | None:
        """The next whole line if one is in, else None; raises Closed once the
        other end has gone and nothing is left."""
        while True:
            nl = self.buf.find(b"\n")
            if nl >= 0:
                line, self.buf = self.buf[:nl], self.buf[nl + 1 :]
                return line.decode("utf-8", "replace").rstrip("\r")
            if self.closed:
                raise Closed("the emulator closed the connection")
            try:
                chunk = self.sock.recv(1 << 20)
            except (BlockingIOError, InterruptedError):
                return None
            except OSError as e:
                self.closed = True
                raise Closed(str(e)) from e
            if not chunk:
                self.closed = True
                continue
            self.buf += chunk

    def readline(
        self, deadline: float, alive: Callable[[], bool] | None = None
    ) -> Step[str]:
        """The next whole line, waiting for it until ``deadline``; ``alive``
        says whether the other end can still answer."""
        while True:
            line = self.poll()
            if line is not None:
                return line
            if time.monotonic() > deadline:
                raise Timeout("no answer from the emulator in time")
            if alive is not None and not alive():
                raise Closed("the emulator has stopped")
            yield WAIT

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Listener:
    """A port on 127.0.0.1 a script connects back to."""

    def __init__(self, port: int = 0):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(4)
        self.sock.setblocking(False)
        self.port = self.sock.getsockname()[1]

    def poll(self) -> Lines | None:
        try:
            conn, _ = self.sock.accept()
        except (BlockingIOError, InterruptedError):
            return None
        return Lines(conn)

    def accept(
        self, deadline: float, alive: Callable[[], bool] | None = None
    ) -> Step[Lines]:
        while True:
            conn = self.poll()
            if conn is not None:
                return conn
            if time.monotonic() > deadline:
                raise Timeout("the emulator's script never connected")
            if alive is not None and not alive():
                raise Closed("the emulator stopped before its script connected")
            yield WAIT

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
