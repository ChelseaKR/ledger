"""Single-host advisory file locking for the JSON workflow stores.

The consent, review, and dual-control stores each persist as one JSON file that a
mutation rewrites *whole* (read the list -> modify it -> write a temp file -> atomic
rename over the target). The atomic rename means a reader never sees a torn file and
a crash mid-write leaves the prior file intact -- but it does **not** serialize two
mutations that interleave. Under the threaded browse server
(:class:`~http.server.ThreadingHTTPServer`) two concurrent POSTs can both read the
same starting file, each append its own change, and the second rename clobbers the
first -- silently dropping, for example, a consent *withdrawal* request. A lost
withdrawal is the worst class of bug this project can have, so the read-modify-write
critical section must be serialized.

This module provides a tiny advisory lock built on :func:`fcntl.flock`, held for the
whole critical section by wrapping a store's mutate path. Scope and caveats:

* **Single host.** ``flock`` serializes both threads and processes on one machine.
  The several request threads of the browse server each open the lock file
  independently (distinct open file descriptions), so they mutually exclude -- which
  is exactly the contention this closes. It does **not** coordinate across NFS or
  multiple hosts; a multi-writer, multi-host deployment must serialize another way
  (documented for adopters in ``ADOPTING.md``).
* **Locks a sibling file, never the data file.** The lock is taken on a stable
  ``<name>.lock`` beside the store, never on the store file itself, because the
  store is replaced by ``os.replace`` on every write; a lock held on the old inode
  would not exclude a thread that opens the new one.
* **Fail-safe where ``fcntl`` is absent** (e.g. Windows): the lock degrades to a
  no-op so the library still imports and runs single-threaded. The atomic rename
  still prevents a torn file; only the lost-update window reopens, which is
  documented as a single-host POSIX guarantee.
* **Content-free.** The lock file carries no content -- it is a pure mutex handle --
  so its existence and emptiness carry no information a no-outing audit would need
  to worry about.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

try:  # POSIX only; absent on Windows.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

__all__ = ["file_lock"]

_LOCK_SUFFIX = ".lock"


@contextlib.contextmanager
def file_lock(target: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock guarding read-modify-write on ``target``.

    Use around a store's whole mutate path so two concurrent mutations serialize
    instead of racing::

        with file_lock(self._path):
            items = self._read()
            items.append(...)
            self._write(items)

    The lock is taken on a sibling ``<target-name>.lock`` file (created on demand and
    left in place; it carries no content, so it is safe under the no-outing rule) so
    it survives the atomic rename that replaces ``target``. On a platform without
    :mod:`fcntl` the lock is a no-op (see the module docstring).
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None:  # pragma: no cover - non-POSIX fallback
        yield  # type: ignore[unreachable]
        return
    lock_path = target.with_name(f"{target.name}{_LOCK_SUFFIX}")
    with open(lock_path, "w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
