import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import atomic_io
from atomic_io import atomic_to_csv


class _SuccessfulFrame:
    def to_csv(self, handle, *, index):
        self.index = index
        handle.write("code,name\nrb2610,螺纹钢\n")


class _FailingFrame:
    def to_csv(self, handle, *, index):
        handle.write("code,name\npartial")
        handle.flush()
        raise RuntimeError("simulated producer failure")


class _PausedFrame:
    def __init__(self, partial_ready, finish_write):
        self.partial_ready = partial_ready
        self.finish_write = finish_write

    def to_csv(self, handle, *, index):
        handle.write("code,name\nrb2610,complete-row\n")
        handle.flush()
        self.partial_ready.set()
        if not self.finish_write.wait(timeout=5):
            raise TimeoutError("test did not release writer")
        handle.write("au2610,second-row\n")


class AtomicCsvTests(unittest.TestCase):
    def test_directory_sync_is_a_noop_without_platform_directory_flag(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            atomic_io.os, "O_DIRECTORY", None
        ), mock.patch("atomic_io.os.open") as open_mock:
            atomic_io._sync_directory(Path(directory))

        open_mock.assert_not_called()

    def test_new_snapshot_keeps_private_mkstemp_permissions_under_strict_umask(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "latest.csv"
            previous_umask = os.umask(0o077)
            try:
                atomic_to_csv(_SuccessfulFrame(), target, index=False)
            finally:
                os.umask(previous_umask)

            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_existing_snapshot_permissions_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "latest.csv"
            target.write_text("old\n", encoding="utf-8")
            target.chmod(0o600)

            atomic_to_csv(_SuccessfulFrame(), target, index=False)

            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_directory_sync_failure_warns_after_successful_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "latest.csv"
            target.write_text("old\n", encoding="utf-8")
            with mock.patch(
                "atomic_io._sync_directory", side_effect=OSError("no dir fsync")
            ):
                with self.assertWarnsRegex(RuntimeWarning, "directory metadata"):
                    result = atomic_to_csv(_SuccessfulFrame(), target, index=False)

            self.assertEqual(result, target)
            self.assertIn("rb2610", target.read_text(encoding="utf-8-sig"))

    def test_success_atomically_replaces_target_and_removes_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested/latest.csv"
            frame = _SuccessfulFrame()

            atomic_to_csv(frame, target, index=False, encoding="utf-8-sig")

            self.assertFalse(frame.index)
            self.assertEqual(
                target.read_text(encoding="utf-8-sig"),
                "code,name\nrb2610,螺纹钢\n",
            )
            self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_reader_never_sees_paused_partial_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "latest.csv"
            target.write_text("old,complete\n", encoding="utf-8")
            partial_ready = threading.Event()
            finish_write = threading.Event()
            failures = []

            def publish():
                try:
                    atomic_to_csv(
                        _PausedFrame(partial_ready, finish_write),
                        target,
                        index=False,
                        encoding="utf-8",
                    )
                except BaseException as exc:
                    failures.append(exc)

            writer = threading.Thread(target=publish)
            writer.start()
            self.assertTrue(partial_ready.wait(timeout=5))
            for _ in range(100):
                self.assertEqual(
                    target.read_text(encoding="utf-8"), "old,complete\n"
                )
            finish_write.set()
            writer.join(timeout=5)

            self.assertFalse(writer.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "code,name\nrb2610,complete-row\nau2610,second-row\n",
            )

    def test_failed_write_keeps_previous_snapshot_and_removes_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "latest.csv"
            target.write_text("old,complete\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "producer failure"):
                atomic_to_csv(_FailingFrame(), target, index=False)

            self.assertEqual(target.read_text(encoding="utf-8"), "old,complete\n")
            self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
