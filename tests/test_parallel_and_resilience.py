import tempfile
import threading
import unittest
from pathlib import Path

from run_absoltec import (
    DOCKUR_STAGE_DIR_NAME,
    WineStartupFailureError,
    collect_dockur_stage_output,
    execute_station_runs,
    scan_dat_file,
    station_output_exists,
)


def _tasks(count: int) -> list[tuple[int, str]]:
    return [(8, f"st{index:02d}") for index in range(1, count + 1)]


class ExecuteStationRunsTests(unittest.TestCase):
    def test_runtime_error_skips_station_instead_of_aborting_batch(self) -> None:
        # absolTEC exiting non-zero raises a plain RuntimeError. It used to
        # escape the batch loop and end an 11k-station run partway through.
        seen: list[str] = []

        def run_station(day_of_year: int, site: str) -> str:
            seen.append(site)
            if site == "st02":
                raise RuntimeError("absolTEC exited with code 64 in the XP guest")
            return "ok"

        total, skipped = execute_station_runs(
            _tasks(4), run_station, year=2025, jobs=1, max_consecutive_failures=0
        )

        self.assertEqual(seen, ["st01", "st02", "st03", "st04"])
        self.assertEqual((total, skipped), (4, 1))

    def test_each_handled_error_type_is_skipped(self) -> None:
        errors = {
            "st01": FileNotFoundError("no input"),
            "st02": ValueError("bad data"),
            "st03": WineStartupFailureError("wine died"),
            "st04": RuntimeError("absolTEC exited with code 64"),
        }

        def run_station(day_of_year: int, site: str) -> str:
            raise errors[site]

        total, skipped = execute_station_runs(
            _tasks(4), run_station, year=2025, jobs=1, max_consecutive_failures=0
        )

        self.assertEqual((total, skipped), (4, 4))

    def test_consecutive_failures_abort_the_batch(self) -> None:
        # Many failures in a row mean something systemic is broken, so the run
        # must stop rather than silently "skipping" every remaining station.
        attempted: list[str] = []

        def run_station(day_of_year: int, site: str) -> str:
            attempted.append(site)
            raise RuntimeError("guest watcher is gone")

        with self.assertRaises(RuntimeError) as ctx:
            execute_station_runs(
                _tasks(50), run_station, year=2025, jobs=1, max_consecutive_failures=3
            )

        self.assertIn("consecutive station failures", str(ctx.exception))
        self.assertEqual(len(attempted), 3)

    def test_success_resets_the_consecutive_failure_counter(self) -> None:
        def run_station(day_of_year: int, site: str) -> str:
            if site in {"st01", "st02", "st04", "st05"}:
                raise RuntimeError("bad station")
            return "ok"

        total, skipped = execute_station_runs(
            _tasks(6), run_station, year=2025, jobs=1, max_consecutive_failures=3
        )

        self.assertEqual((total, skipped), (6, 4))

    def test_parallel_jobs_run_concurrently_and_cover_every_task(self) -> None:
        started = threading.Barrier(3, timeout=10)
        completed: list[str] = []
        lock = threading.Lock()

        def run_station(day_of_year: int, site: str) -> str:
            # Blocks until three stations are in flight at once, so this fails
            # if the executor is really running them one after another.
            started.wait()
            with lock:
                completed.append(site)
            return "ok"

        total, skipped = execute_station_runs(
            _tasks(6), run_station, year=2025, jobs=3, max_consecutive_failures=0
        )

        self.assertEqual((total, skipped), (6, 0))
        self.assertEqual(sorted(completed), [f"st{i:02d}" for i in range(1, 7)])

    def test_already_processed_stations_count_as_skipped(self) -> None:
        # --skip-existing returns this status; the summary must not report a
        # resumed batch as if it had reprocessed everything.
        def run_station(day_of_year: int, site: str) -> str:
            return "skipped-existing" if site in {"st01", "st02"} else "ok"

        total, skipped = execute_station_runs(
            _tasks(4), run_station, year=2025, jobs=1, max_consecutive_failures=3
        )

        self.assertEqual((total, skipped), (4, 2))

    def test_skipped_existing_does_not_trip_the_failure_breaker(self) -> None:
        def run_station(day_of_year: int, site: str) -> str:
            return "skipped-existing"

        total, skipped = execute_station_runs(
            _tasks(10), run_station, year=2025, jobs=1, max_consecutive_failures=3
        )

        self.assertEqual((total, skipped), (10, 10))

    def test_parallel_failures_are_skipped_not_raised(self) -> None:
        def run_station(day_of_year: int, site: str) -> str:
            if site.endswith(("2", "5")):
                raise RuntimeError("absolTEC exited with code 64")
            return "ok"

        total, skipped = execute_station_runs(
            _tasks(6), run_station, year=2025, jobs=3, max_consecutive_failures=0
        )

        self.assertEqual((total, skipped), (6, 2))


class CollectDockurStageOutputTests(unittest.TestCase):
    def test_only_this_jobs_staged_folder_is_collected(self) -> None:
        # absolTEC names its result folder after the 4-character site prefix, so
        # two concurrent stations sharing a prefix would otherwise collide.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for job, payload in (("job-a", "aaa"), ("job-b", "bbb")):
                staged = out / DOCKUR_STAGE_DIR_NAME / job / "2025" / "kudi"
                staged.mkdir(parents=True)
                (staged / "result.dat").write_text(payload, encoding="utf-8")

            first = collect_dockur_stage_output(out, "job-a", 2025, 8, "kudi0080", True)
            second = collect_dockur_stage_output(out, "job-b", 2025, 8, "kudi0081", True)

            self.assertEqual(first, out / "2025" / "008" / "kudi0080")
            self.assertEqual(second, out / "2025" / "008" / "kudi0081")
            self.assertEqual((first / "result.dat").read_text(encoding="utf-8"), "aaa")
            self.assertEqual((second / "result.dat").read_text(encoding="utf-8"), "bbb")

    def test_staging_folder_is_removed_after_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            staged = out / DOCKUR_STAGE_DIR_NAME / "job-a" / "2025" / "aksu"
            staged.mkdir(parents=True)
            (staged / "result.dat").write_text("x", encoding="utf-8")

            collect_dockur_stage_output(out, "job-a", 2025, 8, "aksu0080", True)

            self.assertFalse((out / DOCKUR_STAGE_DIR_NAME / "job-a").exists())

    def test_returns_none_when_the_guest_produced_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.assertIsNone(
                collect_dockur_stage_output(out, "job-missing", 2025, 8, "aksu0080", True)
            )

    def test_four_character_site_keeps_its_own_folder(self) -> None:
        # The old rename step returned None for 4-character sites and logged a
        # scary "no raw station output found" warning even on success.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            staged = out / DOCKUR_STAGE_DIR_NAME / "job-a" / "2025" / "ktiv"
            staged.mkdir(parents=True)
            (staged / "result.dat").write_text("x", encoding="utf-8")

            collected = collect_dockur_stage_output(out, "job-a", 2025, 8, "ktiv", True)

            self.assertEqual(collected, out / "2025" / "008" / "ktiv")


class StationOutputExistsTests(unittest.TestCase):
    def test_detects_populated_output_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            done = out / "2025" / "008" / "aksu0080"
            done.mkdir(parents=True)
            (done / "result.dat").write_text("x", encoding="utf-8")

            self.assertTrue(station_output_exists(out, 2025, 8, "aksu0080", True))
            self.assertFalse(station_output_exists(out, 2025, 8, "bala0080", True))

    def test_empty_folder_does_not_count_as_processed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "2025" / "008" / "aksu0080").mkdir(parents=True)

            self.assertFalse(station_output_exists(out, 2025, 8, "aksu0080", True))


class ScanDatFileTests(unittest.TestCase):
    HEADER = (
        " tsn, hour, el, az, tec.l1l2, tec.c1p2, validity\n"
        "# (I11,1X,F14.11,1X,F10.5,1X,F11.5,1X,F21.3,1X,F10.3,1X,I7)\n"
    )
    ROW = "        338  2.81666666667   40.38404    60.78731    23.516     -8.376   59395\n"

    def _scan(self, text: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kudi_G07_008_25.dat"
            path.write_text(text, encoding="utf-8")
            return scan_dat_file(path)

    def test_counts_data_rows_and_ignores_headers(self) -> None:
        rows, nonzero, problems = self._scan(self.HEADER + self.ROW * 3)

        self.assertEqual(rows, 3)
        self.assertEqual(nonzero, 3)
        self.assertEqual(problems, [])

    def test_title_line_without_hash_is_not_reported_as_malformed(self) -> None:
        _, _, problems = self._scan(self.HEADER + self.ROW)

        self.assertEqual(problems, [])

    def test_non_numeric_row_is_reported(self) -> None:
        rows, _, problems = self._scan(self.HEADER + self.ROW + "  a  b  c  d  e  f  g\n")

        self.assertEqual(rows, 1)
        self.assertEqual(len(problems), 1)
        self.assertIn("non-numeric field", problems[0])

    def test_wrong_column_count_is_reported(self) -> None:
        rows, _, problems = self._scan(self.HEADER + self.ROW + "  1.0  2.0  3.0\n")

        self.assertEqual(rows, 1)
        self.assertEqual(len(problems), 1)
        self.assertIn("expected 7 columns", problems[0])

    def test_zero_bias_rows_are_counted_separately(self) -> None:
        zero_bias = "        338  2.81666666667   40.38404    60.78731    23.516      0.000   59395\n"
        rows, nonzero, _ = self._scan(self.HEADER + zero_bias * 2)

        self.assertEqual(rows, 2)
        self.assertEqual(nonzero, 0)


if __name__ == "__main__":
    unittest.main()
