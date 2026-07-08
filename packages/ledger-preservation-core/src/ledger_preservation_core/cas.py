"""A content-addressed store (CAS) for archived payload bytes.

Objects are named by the hash of their content, so an object's name *is* its
fixity check. Design choices and the quality attributes they serve:

* **Content addressing** -> integrity and dedupe (efficiency): identical bytes
  always map to one address and are stored once; a changed byte is a different
  address, so silent drift is impossible.
* **Atomic replace** (write a temp file in the same directory, then
  :func:`os.replace`) -> fault-tolerance and recoverability: a reader never sees
  a half-written object, and an interrupted write leaves only an orphan temp file,
  never a corrupt one at the real address.
* **Sharded layout** (two nested hex prefixes) -> scalability: no single directory
  accumulates millions of entries.

No-outing: addresses are derived from content, never from a contributor; nothing
here logs, names, or returns payload contents or identity. Exceptions name only
the content address.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import BinaryIO

from ledger_preservation_core.errors import ObjectNotFound
from ledger_preservation_core.fixity import hash_bytes, hash_file
from ledger_preservation_core.models import ContentAddress, FixityResult, HashAlgo


class ContentStore:
    """A filesystem-backed content-addressed object store.

    Layout: ``root/objects/<algo>/<aa>/<bb>/<full-hex>``, where ``aa`` and ``bb``
    are the first two byte-pairs of the hex digest. The store is content-addressed
    under a single configurable ``address_algo`` (SHA-256 by default).
    """

    def __init__(self, root: Path, *, address_algo: HashAlgo = HashAlgo.SHA256) -> None:
        """Bind the store to ``root`` and fix its addressing algorithm.

        The root is created lazily on first write, so constructing a store is
        side-effect-light and safe to do speculatively.
        """
        self.root = root
        self.address_algo = address_algo
        self._objects_dir = root / "objects" / address_algo.value

    def path_for(self, addr: ContentAddress) -> Path:
        """Return the deterministic on-disk path for ``addr``.

        Pure and total: it computes the sharded location from the digest and does
        not touch the filesystem, so callers can reason about placement without
        requiring the object to exist (determinability).
        """
        digest = addr.digest
        shard_a = digest[0:2]
        shard_b = digest[2:4]
        return self._objects_dir.parent / addr.algo.value / shard_a / shard_b / digest

    def _store(self, digest: str) -> ContentAddress:
        """Build the :class:`~ledger_preservation_core.models.ContentAddress` for a stored digest."""
        return ContentAddress(algo=self.address_algo, digest=digest)

    def _write_atomic(self, dest: Path, source_reader: BinaryIO) -> None:
        """Stream ``source_reader`` into ``dest`` via a same-directory temp + replace.

        Writing the temp file in the destination directory guarantees the final
        :func:`os.replace` is a same-filesystem atomic rename (fault-tolerance).
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in iter(lambda: source_reader.read(1024 * 1024), b""):
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, dest)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def put_bytes(self, data: bytes) -> ContentAddress:
        """Store ``data`` and return its content address.

        Idempotent: if an object with this address already exists it is not
        rewritten (dedupe -> efficiency; no needless I/O).
        """
        digest = hash_bytes(data, self.address_algo)
        addr = self._store(digest)
        dest = self.path_for(addr)
        if dest.exists():
            return addr
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, dest)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return addr

    def put_file(self, src: Path) -> ContentAddress:
        """Store the file at ``src`` and return its content address.

        Streams the source so arbitrarily large payloads cost constant memory
        (scalability). Idempotent on an already-present address (dedupe).
        """
        digest = hash_file(src, self.address_algo)
        addr = self._store(digest)
        dest = self.path_for(addr)
        if dest.exists():
            return addr
        with src.open("rb") as reader:
            self._write_atomic(dest, reader)
        return addr

    def get_path(self, addr: ContentAddress) -> Path:
        """Return the path for ``addr``, raising if no object is stored there.

        Raises :class:`~ledger_preservation_core.errors.ObjectNotFound` (naming only the address,
        never any content) when the object is absent.
        """
        dest = self.path_for(addr)
        if not dest.exists():
            raise ObjectNotFound(str(addr))
        return dest

    def read_bytes(self, addr: ContentAddress) -> bytes:
        """Return the full contents of the object at ``addr``.

        Raises :class:`~ledger_preservation_core.errors.ObjectNotFound` if it is absent.
        """
        return self.get_path(addr).read_bytes()

    def open(self, addr: ContentAddress) -> BinaryIO:
        """Open the object at ``addr`` for binary reading.

        The caller owns the returned handle and must close it. Raises
        :class:`~ledger_preservation_core.errors.ObjectNotFound` if the object is absent.
        """
        return self.get_path(addr).open("rb")

    def exists(self, addr: ContentAddress) -> bool:
        """Return whether an object is stored at ``addr``."""
        return self.path_for(addr).exists()

    def verify(self, addr: ContentAddress) -> FixityResult:
        """Re-hash the stored object and compare against its own address.

        Because the address *is* the expected digest, this proves the bytes on
        disk still hash to the name they are filed under (integrity). Raises
        :class:`~ledger_preservation_core.errors.ObjectNotFound` if the object is absent.
        """
        dest = self.get_path(addr)
        actual = hash_file(dest, addr.algo)
        return FixityResult(path=str(addr), algo=addr.algo, expected=addr.digest, actual=actual)
