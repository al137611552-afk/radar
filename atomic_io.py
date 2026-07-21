"""Atomic publication helpers for dashboard-consumed output files."""

from __future__ import annotations

import os
import stat
import tempfile
import warnings
from pathlib import Path


def _sync_directory(path: Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    descriptor = os.open(path, os.O_RDONLY | directory_flag)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_to_csv(frame, target: str | Path, *, index: bool, encoding="utf-8-sig"):
    """Write fully, then atomically publish a dataframe-like CSV snapshot.

    ``os.replace`` is the commit point. Errors before it preserve the previous
    snapshot. A directory-sync error after commit is reported as a warning,
    because the new snapshot is already visible and cannot be truthfully rolled
    back as an unpublished write.
    """
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing_mode = stat.S_IMODE(path.stat().st_mode) & 0o777
    except FileNotFoundError:
        existing_mode = None

    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        if existing_mode is not None and hasattr(os, "fchmod"):
            os.fchmod(descriptor, existing_mode)
        with os.fdopen(descriptor, "w", encoding=encoding, newline="") as handle:
            frame.to_csv(handle, index=index)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise

    try:
        _sync_directory(path.parent)
    except OSError as exc:
        warnings.warn(
            f"snapshot published but directory metadata sync failed: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
    return path
