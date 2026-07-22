import csv
import os
import tempfile
import time
import unittest
from pathlib import Path

from run_absoltec import (
    DOCKUR_STAGE_DIR_NAME,
    RunManifest,
    execute_station_runs,
    prune_dockur_artifacts,
)


class RunManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "_manifest.csv"

    def test_records_one_row_per_station_with_header(self) -> None:
        manifest = RunManifest(self.path)
        manifest.record(year=2025, day_of_year=8, site="aksu0080", status="ok", duration_seconds=12.3)
        manifest.record(year=2025, day_of_year=8, site="kudi0080", status="failed-runtime",
                        reason="RuntimeError: exit code 64")

        with self.path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["site"], "aksu0080")
        self.assertEqual(rows[0]["status"], "ok")
        self.assertEqual(rows[0]["duration_seconds"], "12.3")
        self.assertEqual(rows[1]["day_of_year"], "008")
        self.assertIn("exit code 64", rows[1]["reason"])

    def test_multiline_reason_stays_on_one_row(self) -> None:
        # absolTEC failure text is a multi-line Fortran traceback.
        manifest = RunManifest(self.path)
        manifest.record(year=2025, day_of_year=8, site="kudi0080", status="failed-runtime",
                        reason="forrtl: severe (64)\n  absolTEC.exe 00472E5A\n  kernel32.dll")

        with self.path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)
        self.assertNotIn("\n", rows[0]["reason"])

    def test_completed_stations_are_recognised_on_reload(self) -> None:
        manifest = RunManifest(self.path)
        manifest.record(year=2025, day_of_year=8, site="aksu0080", status="ok")
        manifest.record(year=2025, day_of_year=8, site="kudi0080", status="failed-runtime")

        resumed = RunManifest(self.path)
        self.assertEqual(resumed.load(), 2)
        self.assertEqual(resumed.already_done(2025, 8, "aksu0080"), "ok")
        # Failures must be retried, not treated as finished.
        self.assertIsNone(resumed.already_done(2025, 8, "kudi0080"))
        self.assertIsNone(resumed.already_done(2025, 8, "never0080"))

    def test_station_lookup_is_case_insensitive(self) -> None:
        manifest = RunManifest(self.path)
        manifest.record(year=2025, day_of_year=8, site="AKSU0080", status="ok")

        resumed = RunManifest(self.path)
        resumed.load()

        self.assertEqual(resumed.already_done(2025, 8, "aksu0080"), "ok")

    def test_latest_outcome_wins_after_a_retry(self) -> None:
        manifest = RunManifest(self.path)
        manifest.record(year=2025, day_of_year=8, site="kudi0080", status="failed-runtime")
        manifest.record(year=2025, day_of_year=8, site="kudi0080", status="ok")

        resumed = RunManifest(self.path)
        resumed.load()

        self.assertEqual(resumed.already_done(2025, 8, "kudi0080"), "ok")

    def test_missing_manifest_loads_as_empty(self) -> None:
        self.assertEqual(RunManifest(self.path).load(), 0)

    def test_write_failure_does_not_raise(self) -> None:
        # Bookkeeping must never take down an 11k-station batch. A regular file
        # standing where a parent directory should be makes mkdir fail on every
        # platform.
        blocker = Path(self._tmp.name) / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")

        manifest = RunManifest(blocker / "sub" / "_manifest.csv")
        manifest.record(year=2025, day_of_year=8, site="aksu0080", status="ok")


class ManifestResumeTests(unittest.TestCase):
    def test_resumed_run_skips_completed_stations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "_manifest.csv"
            tasks = [(8, "st01"), (8, "st02"), (8, "st03")]

            first_seen: list[str] = []
            manifest = RunManifest(path)
            execute_station_runs(
                tasks,
                lambda d, s: (first_seen.append(s), "ok")[1],
                year=2025, jobs=1, max_consecutive_failures=0, manifest=manifest,
            )
            self.assertEqual(first_seen, ["st01", "st02", "st03"])

            # Second run: everything already recorded, so nothing re-runs.
            second_seen: list[str] = []
            resumed = RunManifest(path)
            resumed.load()
            total, skipped = execute_station_runs(
                tasks,
                lambda d, s: (second_seen.append(s), "ok")[1],
                year=2025, jobs=1, max_consecutive_failures=0, manifest=resumed,
            )

            self.assertEqual(second_seen, [])
            self.assertEqual((total, skipped), (3, 3))

    def test_failed_station_is_retried_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "_manifest.csv"
            tasks = [(8, "st01"), (8, "st02")]

            manifest = RunManifest(path)
            execute_station_runs(
                tasks,
                lambda d, s: (_ for _ in ()).throw(RuntimeError("boom")) if s == "st02" else "ok",
                year=2025, jobs=1, max_consecutive_failures=0, manifest=manifest,
            )

            retried: list[str] = []
            resumed = RunManifest(path)
            resumed.load()
            execute_station_runs(
                tasks,
                lambda d, s: (retried.append(s), "ok")[1],
                year=2025, jobs=1, max_consecutive_failures=0, manifest=resumed,
            )

            self.assertEqual(retried, ["st02"])


class PruneDockurArtifactsTests(unittest.TestCase):
    def _aged_dir(self, parent: Path, name: str, hours_old: float) -> Path:
        path = parent / name
        path.mkdir(parents=True)
        (path / "marker").write_text("x", encoding="utf-8")
        old = time.time() - hours_old * 3600
        os.utime(path, (old, old))
        return path

    def test_removes_only_stale_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp) / "jobs"
            jobs.mkdir()
            stale = self._aged_dir(jobs, "old_job", 100)
            fresh = self._aged_dir(jobs, "new_job", 1)

            removed_jobs, _ = prune_dockur_artifacts(jobs, None, retention_hours=48)

            self.assertEqual(removed_jobs, 1)
            self.assertFalse(stale.exists())
            self.assertTrue(fresh.exists())

    def test_prunes_leaked_stage_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            stage_root = out / DOCKUR_STAGE_DIR_NAME
            stage_root.mkdir(parents=True)
            leaked = self._aged_dir(stage_root, "dead_job", 100)

            _, removed_stage = prune_dockur_artifacts(None, out, retention_hours=48)

            self.assertEqual(removed_stage, 1)
            self.assertFalse(leaked.exists())

    def test_zero_retention_disables_pruning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp) / "jobs"
            jobs.mkdir()
            stale = self._aged_dir(jobs, "old_job", 500)

            self.assertEqual(prune_dockur_artifacts(jobs, None, retention_hours=0), (0, 0))
            self.assertTrue(stale.exists())

    def test_missing_directories_are_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            absent = Path(tmp) / "nothing-here"
            self.assertEqual(prune_dockur_artifacts(absent, absent, retention_hours=48), (0, 0))


if __name__ == "__main__":
    unittest.main()
