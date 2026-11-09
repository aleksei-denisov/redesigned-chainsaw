"""Parallel evaluation of independent work units (Qt-free, used by the auto-tuner).

One interface, three back-ends chosen by :func:`make_pool`:

``ProcessPool``
    ``concurrent.futures.ProcessPoolExecutor`` – desktop (Windows/macOS/Linux).
``ForkPool``
    A small pool of ``os.fork()``-ed workers talking over pipes. Used inside an
    Android app, where ``ProcessPoolExecutor`` cannot work: its queues need POSIX
    named semaphores (``sem_open``), which Android's C library does not implement,
    and the *spawn* start method needs a Python executable, which an APK does not
    have. Children only run NumPy/Python on plain data, never touch Qt or Java, and
    leave with ``os._exit``. A self-test at start-up proves the pool works on the
    device; otherwise the caller falls back to …
``SerialPool``
    Everything in the calling thread.

Work units are ``(function, args)`` with a picklable top-level function and plain
arguments. :meth:`Pool.imap_unordered` yields ``(index, result)`` as units finish;
:meth:`Pool.cancel` stops outstanding work promptly (worker processes are killed).
"""

from __future__ import annotations

import os
import pickle
import select
import signal
import struct
import sys
import traceback
import warnings
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from typing import Any, Callable, Iterable, Iterator

Task = tuple[Callable[..., Any], tuple]


class PoolUnavailable(RuntimeError):
    """The requested parallel back-end cannot run on this platform."""


class WorkerError(RuntimeError):
    """A work unit raised in a worker; the message carries the worker's traceback."""


def is_android() -> bool:
    return hasattr(sys, "getandroidapilevel") or sys.platform == "android"


def cpu_count() -> int:
    try:
        return len(os.sched_getaffinity(0)) if not is_android() else (os.cpu_count() or 1)
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def default_workers() -> int:
    """All cores but one (the UI keeps a core), at least 1, at most 8."""
    return max(1, min(8, cpu_count() - 1))


class Pool:
    kind = "serial"
    workers = 1

    def imap_unordered(self, tasks: list[Task]) -> Iterator[tuple[int, Any]]:
        raise NotImplementedError

    def cancel(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self) -> "Pool":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def describe(self) -> str:
        if self.workers == 1:
            return "1 CPU core"
        return f"{self.workers} CPU cores"


class SerialPool(Pool):
    kind = "serial"

    def __init__(self, reason: str = "") -> None:
        self.reason = reason            # why parallel execution is not used ("" = by choice)
        self._cancelled = False

    def imap_unordered(self, tasks: list[Task]) -> Iterator[tuple[int, Any]]:
        self._cancelled = False
        for i, (fn, args) in enumerate(tasks):
            if self._cancelled:
                return
            yield i, fn(*args)

    def cancel(self) -> None:
        self._cancelled = True


class ProcessPool(Pool):
    kind = "processes"

    def __init__(self, workers: int) -> None:
        self.workers = workers
        try:
            self._executor = ProcessPoolExecutor(max_workers=workers)
        except (OSError, NotImplementedError, ValueError, ImportError) as exc:
            raise PoolUnavailable(str(exc)) from exc
        self._cancelled = False

    def imap_unordered(self, tasks: list[Task]) -> Iterator[tuple[int, Any]]:
        self._cancelled = False
        futures = {self._executor.submit(fn, *args): i for i, (fn, args) in enumerate(tasks)}
        pending = set(futures)
        while pending and not self._cancelled:
            done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            for f in done:
                try:
                    value = f.result()
                except Exception as exc:  # noqa: BLE001 - same error type as ForkPool
                    raise WorkerError("".join(traceback.format_exception(exc))) from exc
                yield futures[f], value
        for f in pending:
            f.cancel()

    def cancel(self) -> None:
        self._cancelled = True
        self._kill()

    def _kill(self) -> None:
        processes = getattr(self._executor, "_processes", None) or {}
        for p in list(processes.values()):
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
        self._executor.shutdown(wait=False, cancel_futures=True)

    def close(self) -> None:
        if self._cancelled:
            self._executor.shutdown(wait=False, cancel_futures=True)
        else:
            self._executor.shutdown(wait=True, cancel_futures=True)


# ------------------------------------------------------------------ fork pool
_HEADER = struct.Struct("<Q")


def _send(fd: int, obj: Any) -> None:
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    view = memoryview(_HEADER.pack(len(data)) + data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def _recv_exact(fd: int, n: int) -> bytes:
    chunks, remaining = [], n
    while remaining:
        chunk = os.read(fd, remaining)
        if not chunk:
            raise EOFError
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv(fd: int) -> Any:
    (size,) = _HEADER.unpack(_recv_exact(fd, _HEADER.size))
    return pickle.loads(_recv_exact(fd, size))


def _selftest(x: int) -> int:
    import numpy as np

    return int(np.arange(x + 1).sum())


def _worker_main(task_fd: int, result_fd: int, cpus: set[int] | None) -> None:
    """Child process loop: receive (index, fn, args), send (index, ok, payload)."""
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if cpus and hasattr(os, "sched_setaffinity"):
            try:
                os.sched_setaffinity(0, cpus)   # not limited to the parent thread's cores
            except OSError:
                pass
        while True:
            msg = _recv(task_fd)
            if msg is None:
                break
            index, fn, args = msg
            try:
                _send(result_fd, (index, True, fn(*args)))
            except BaseException:  # noqa: BLE001 - reported to the parent
                _send(result_fd, (index, False, traceback.format_exc()))
    except BaseException:  # noqa: BLE001
        pass
    finally:
        os._exit(0)


class ForkPool(Pool):
    kind = "fork"

    def __init__(self, workers: int, selftest_timeout: float = 10.0) -> None:
        if not hasattr(os, "fork"):
            raise PoolUnavailable("os.fork is not available")
        self.workers = workers
        self._procs: list[tuple[int, int, int]] = []   # (pid, task_w, result_r)
        try:
            cpus = set(range(os.cpu_count() or 1))
        except Exception:  # noqa: BLE001
            cpus = None
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            for _ in range(workers):
                task_r, task_w = os.pipe()
                result_r, result_w = os.pipe()
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)   # fork in a threaded process
                    pid = os.fork()
                if pid == 0:                                  # child
                    os.close(task_w)
                    os.close(result_r)
                    for _pid, tw, rr in self._procs:          # siblings' pipe ends
                        for fd in (tw, rr):
                            try:
                                os.close(fd)
                            except OSError:
                                pass
                    _worker_main(task_r, result_w, cpus)
                os.close(task_r)
                os.close(result_w)
                self._procs.append((pid, task_w, result_r))
            self._check(selftest_timeout)
        except PoolUnavailable:
            self.cancel()
            raise
        except Exception as exc:  # noqa: BLE001
            self.cancel()
            raise PoolUnavailable(f"worker processes failed to start: {exc}") from exc

    def _check(self, timeout: float) -> None:
        tasks = [(_selftest, (100 + k,)) for k in range(self.workers)]
        got = {}
        for i, value in self._run(tasks, timeout=timeout):
            got[i] = value
        if got != {k: _selftest(100 + k) for k in range(self.workers)}:
            raise PoolUnavailable("worker self-test returned wrong results")

    def imap_unordered(self, tasks: list[Task]) -> Iterator[tuple[int, Any]]:
        yield from self._run(tasks, timeout=None)

    def _run(self, tasks: list[Task], timeout: float | None) -> Iterator[tuple[int, Any]]:
        if not self._procs:
            raise PoolUnavailable("pool was cancelled")
        queue = list(enumerate(tasks))
        queue.reverse()
        busy: dict[int, tuple[int, int, int]] = {}         # result fd → worker
        idle = list(self._procs)
        remaining = len(tasks)
        while remaining:
            while idle and queue:
                worker = idle.pop()
                index, (fn, args) = queue.pop()
                _send(worker[1], (index, fn, args))
                busy[worker[2]] = worker
            ready, _, _ = select.select(list(busy), [], [], timeout if timeout is not None else 0.5)
            if not ready:
                if timeout is not None:
                    raise PoolUnavailable("worker self-test timed out")
                self._reap_check()
                continue
            for fd in ready:
                try:
                    index, ok, payload = _recv(fd)
                except EOFError as exc:
                    raise PoolUnavailable("a worker process stopped unexpectedly") from exc
                idle.append(busy.pop(fd))
                remaining -= 1
                if not ok:
                    raise WorkerError(payload)
                yield index, payload

    def _reap_check(self) -> None:
        for pid, _tw, _rr in self._procs:
            try:
                done, _status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                done = pid
            if done:
                raise PoolUnavailable("a worker process stopped unexpectedly")

    def cancel(self) -> None:
        """Kill all workers at once (prompt cancellation)."""
        procs, self._procs = self._procs, []
        for pid, tw, rr in procs:
            for fd in (tw, rr):
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        for pid, _tw, _rr in procs:
            try:
                os.waitpid(pid, 0)
            except OSError:
                pass

    def close(self) -> None:
        procs = list(self._procs)
        for _pid, tw, _rr in procs:
            try:
                _send(tw, None)
            except OSError:
                pass
        self.cancel()

    def describe(self) -> str:
        return f"{self.workers} CPU cores (worker processes)"


def make_pool(workers: int | None = None) -> Pool:
    """Best available pool for ``workers`` (``None``/0 = :func:`default_workers`).

    Never raises: falls back to :class:`SerialPool` with the reason in ``.reason``.
    """
    n = workers or default_workers()
    if n <= 1:
        return SerialPool()
    if is_android():
        try:
            return ForkPool(n)
        except PoolUnavailable as exc:
            return SerialPool(f"parallel workers unavailable on this device ({exc})")
    try:
        return ProcessPool(n)
    except PoolUnavailable as exc:
        if hasattr(os, "fork"):
            try:
                return ForkPool(n)
            except PoolUnavailable:
                pass
        return SerialPool(f"parallel workers unavailable ({exc})")


def run_all(pool: Pool, tasks: Iterable[Task]) -> list[Any]:
    """Convenience: results in task order."""
    tasks = list(tasks)
    out: list[Any] = [None] * len(tasks)
    for i, value in pool.imap_unordered(tasks):
        out[i] = value
    return out
