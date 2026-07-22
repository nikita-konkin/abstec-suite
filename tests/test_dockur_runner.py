import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from run_absoltec import (
    DOCKUR_DEFAULT_GUEST_DAT_PATH,
    _read_dockur_exit_code,
    build_dockur_job_bat,
    run_absoltec_dockur,
    should_use_wine,
    submit_dockur_job,
    wait_for_dockur_job,
)


class DockurJobBatTests(unittest.TestCase):
    def test_job_bat_uses_crlf_line_endings(self) -> None:
        content = build_dockur_job_bat(2026, "20260101_000000_2026_001_aksu_deadbeef")

        self.assertTrue(content.endswith("\r\n"))
        self.assertNotIn("\n", content.replace("\r\n", ""))

    def test_job_bat_embeds_year(self) -> None:
        content = build_dockur_job_bat(2026, "20260101_000000_2026_001_aksu_deadbeef")

        self.assertIn('set "YEAR=2026"', content)

    def test_job_bat_writes_exit_code_with_leading_redirection(self) -> None:
        # `echo %CODE%> file` would parse a single-digit code as a file
        # descriptor redirect on XP cmd, so the redirection must come first.
        content = build_dockur_job_bat(2026, "20260101_000000_2026_001_aksu_deadbeef")

        self.assertIn('>"%JOB%job.done" echo %CODE%', content)
        self.assertNotIn('echo %CODE%>', content)

    def test_job_bat_is_ascii_only(self) -> None:
        build_dockur_job_bat(2026, "20260101_000000_2026_001_aksu_deadbeef").encode("ascii")


class SubmitDockurJobTests(unittest.TestCase):
    def test_submit_creates_job_folder_with_marker_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp) / "jobs"

            job_dir = submit_dockur_job(jobs_dir, "W:\\in\\\n10.0\n", 2026, "2026_001_aksu")

            self.assertTrue(job_dir.is_dir())
            self.assertEqual((job_dir / "absolTEC.dia").read_text(encoding="utf-8"), "W:\\in\\\n10.0\n")
            self.assertTrue((job_dir / "job.bat").exists())
            self.assertTrue((job_dir / "job.ready").exists())

    def test_submit_sanitizes_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp) / "jobs"

            job_dir = submit_dockur_job(jobs_dir, "content", 2026, "site with spaces/slash")

            self.assertNotIn(" ", job_dir.name)
            self.assertNotIn("/", job_dir.name)


class WaitForDockurJobTests(unittest.TestCase):
    def test_wait_returns_exit_code_and_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "job.log").write_text("Completed 5 / 10\n", encoding="ascii")
            (job_dir / "job.done").write_text("0\r\n", encoding="ascii")

            exit_code, output = wait_for_dockur_job(job_dir, timeout_seconds=5, poll_seconds=0.01)

            self.assertEqual(exit_code, 0)
            self.assertIn("Completed 5 / 10", output)

    def test_wait_picks_up_done_file_written_later(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)

            def finish_job() -> None:
                (job_dir / "job.log").write_text("working\n", encoding="ascii")
                (job_dir / "job.done").write_text("7\r\n", encoding="ascii")

            timer = threading.Timer(0.1, finish_job)
            timer.start()
            try:
                exit_code, output = wait_for_dockur_job(job_dir, timeout_seconds=5, poll_seconds=0.01)
            finally:
                timer.cancel()

            self.assertEqual(exit_code, 7)
            self.assertIn("working", output)

    def test_wait_timeout_writes_kill_flag_and_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)

            with self.assertRaises(RuntimeError) as ctx:
                wait_for_dockur_job(
                    job_dir,
                    timeout_seconds=0.05,
                    poll_seconds=0.01,
                    kill_grace_seconds=0.05,
                )

            self.assertTrue((job_dir / "job.kill").exists())
            self.assertIn("did not finish", str(ctx.exception))


class ReadDockurExitCodeTests(unittest.TestCase):
    def test_transient_nul_bytes_resolve_to_exit_code(self) -> None:
        # SMB can expose job.done with its size allocated but data unflushed,
        # reading as NUL bytes; the real content must be picked up on retry.
        with tempfile.TemporaryDirectory() as tmp:
            done_path = Path(tmp) / "job.done"
            done_path.write_bytes(b"\x00\x00\x00")
            timer = threading.Timer(0.15, lambda: done_path.write_bytes(b"0\r\n"))
            timer.start()
            try:
                code = _read_dockur_exit_code(done_path, poll_seconds=0.02)
            finally:
                timer.cancel()
            self.assertEqual(code, 0)

    def test_persistent_nul_bytes_raise_after_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            done_path = Path(tmp) / "job.done"
            done_path.write_bytes(b"\x00\x00\x00")
            with self.assertRaises(RuntimeError) as ctx:
                _read_dockur_exit_code(
                    done_path, poll_seconds=0.02, visibility_timeout_seconds=0.1
                )
            self.assertIn("unflushed", str(ctx.exception))

    def test_persistent_garbage_raises_after_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            done_path = Path(tmp) / "job.done"
            done_path.write_bytes(b"not-a-number")
            with self.assertRaises(RuntimeError) as ctx:
                _read_dockur_exit_code(
                    done_path, poll_seconds=0.02, visibility_timeout_seconds=0.1
                )
            self.assertIn("Unreadable exit code", str(ctx.exception))


class RunAbsoltecDockurTests(unittest.TestCase):
    def test_success_removes_job_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)

            with patch("run_absoltec.wait_for_dockur_job", return_value=(0, "all good")):
                run_absoltec_dockur(
                    jobs_dir=jobs_dir,
                    dia_content="content",
                    year=2026,
                    label="2026_001_aksu",
                    timeout_seconds=5,
                )

            self.assertEqual(list(jobs_dir.iterdir()), [])

    def test_success_waits_for_watcher_ack_before_cleanup(self) -> None:
        # The guest watcher deletes job.running once it has seen job.done;
        # cleanup must not race ahead of that or the watcher stalls forever.
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)

            def fake_wait(job_dir, timeout_seconds):
                (job_dir / "job.running").write_text("running", encoding="ascii")
                threading.Timer(0.2, (job_dir / "job.running").unlink).start()
                return (0, "all good")

            with patch("run_absoltec.wait_for_dockur_job", side_effect=fake_wait):
                run_absoltec_dockur(
                    jobs_dir=jobs_dir,
                    dia_content="content",
                    year=2026,
                    label="2026_001_aksu",
                    timeout_seconds=5,
                    ack_grace_seconds=5,
                )

            self.assertEqual(list(jobs_dir.iterdir()), [])

    def test_success_keeps_folder_when_watcher_never_acks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)

            def fake_wait(job_dir, timeout_seconds):
                (job_dir / "job.running").write_text("running", encoding="ascii")
                return (0, "all good")

            with patch("run_absoltec.wait_for_dockur_job", side_effect=fake_wait):
                run_absoltec_dockur(
                    jobs_dir=jobs_dir,
                    dia_content="content",
                    year=2026,
                    label="2026_001_aksu",
                    timeout_seconds=5,
                    ack_grace_seconds=0.1,
                )

            self.assertEqual(len(list(jobs_dir.iterdir())), 1)

    def test_nonzero_exit_raises_and_keeps_job_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)

            with patch("run_absoltec.wait_for_dockur_job", return_value=(2, "boom")):
                with self.assertRaises(RuntimeError) as ctx:
                    run_absoltec_dockur(
                        jobs_dir=jobs_dir,
                        dia_content="content",
                        year=2026,
                        label="2026_001_aksu",
                        timeout_seconds=5,
                    )

            self.assertIn("code 2", str(ctx.exception))
            self.assertEqual(len(list(jobs_dir.iterdir())), 1)

    def test_no_files_error_raises_even_with_zero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)

            with patch("run_absoltec.wait_for_dockur_job", return_value=(0, "Error, no files")):
                with self.assertRaises(RuntimeError) as ctx:
                    run_absoltec_dockur(
                        jobs_dir=jobs_dir,
                        dia_content="content",
                        year=2026,
                        label="2026_001_aksu",
                        timeout_seconds=5,
                    )

            self.assertIn("no files", str(ctx.exception).lower())


class DockurRunnerSelectionTests(unittest.TestCase):
    def test_should_use_wine_is_false_for_dockur(self) -> None:
        self.assertFalse(should_use_wine(Path("absolTEC.exe"), "dockur"))

    def test_default_guest_dat_path_is_mapped_drive(self) -> None:
        self.assertEqual(DOCKUR_DEFAULT_GUEST_DAT_PATH, "W:\\in\\")


if __name__ == "__main__":
    unittest.main()
