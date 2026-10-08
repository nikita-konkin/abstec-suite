import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from run_absoltec import (
    clean_dat_text,
    count_solved_rows,
    run_cleaned_then_raw,
    run_single_station,
    stage_clean_station_input,
    submit_dockur_job,
)

HEADER = (
    "# Site: aksu\n"
    "# Columns: tsn, hour, el, az, tec.l1l2, tec.c1p2, validity\n"
    "# (I11,1X,F14.11,1X,F10.5,1X,F11.5,1X,F21.3,1X,F10.3,1X,I7)\n"
)


def row(tsn: int, phase: float, code: float, validity: int = 0) -> str:
    return f"{tsn:11d} {tsn / 120:14.11f} {30.0:10.5f} {120.0:11.5f} {phase:21.3f} {code:10.3f} {validity:7d}\n"


def result_file(folder: Path, name: str, solved: int, rows: int = 48) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["# UT  I_v  G_lon  G_lat  G_q_lon  G_q_lat  G_t  G_q_t\n"]
    for i in range(rows):
        tec = 12.5 if i < solved else 0.0
        lines.append(f"{i / 2:9.3f} {tec:10.3f} {0.0:10.3f} {0.0:10.3f}\n")
    (folder / name).write_text("".join(lines), encoding="utf-8")


class CleanDatTextTests(unittest.TestCase):
    def test_drops_phase_placeholders_and_repeated_epochs(self) -> None:
        text = HEADER + row(1, -30.1, 25.0) + row(2, 0.0, 26.0, 65610) + row(2, 0.0, 26.0, 65610) + row(3, -30.3, 24.0)

        cleaned, rows, dropped = clean_dat_text(text)

        self.assertEqual(cleaned, HEADER + row(1, -30.1, 25.0) + row(3, -30.3, 24.0))
        self.assertEqual((rows, dropped), (2, 2))

    def test_repeated_epoch_with_phase_keeps_the_first(self) -> None:
        text = HEADER + row(5, -10.0, 3.0) + row(5, -10.2, 3.1) + row(6, -10.1, 3.0)

        cleaned, rows, dropped = clean_dat_text(text)

        self.assertEqual(cleaned, HEADER + row(5, -10.0, 3.0) + row(6, -10.1, 3.0))
        self.assertEqual((rows, dropped), (2, 1))

    def test_keeps_line_endings_and_rows_with_zero_code(self) -> None:
        # Zero code with valid phase is usable data: absolTEC levels it with code from other satellites.
        text = (HEADER + row(1, -30.1, 0.0) + row(2, -30.2, 0.0)).replace("\n", "\r\n")

        cleaned, rows, dropped = clean_dat_text(text)

        self.assertEqual(cleaned, text)
        self.assertEqual((rows, dropped), (2, 0))


class StageCleanStationInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.station = Path(self._tmp.name) / "in" / "aksu"
        self.station.mkdir(parents=True)
        self.destination = Path(self._tmp.name) / "staged" / "aksu"

    def write(self, name: str, *rows: str) -> None:
        (self.station / name).write_bytes((HEADER + "".join(rows)).encode("ascii"))

    def test_cleans_every_file_including_dup_sessions(self) -> None:
        self.write("aksu_G01_100_26.dat", row(1, -30.1, 25.0), row(2, 0.0, 25.0))
        self.write("aksu_G01_100_26__dup1.dat", row(9, 0.0, 22.0), row(10, -12.0, 22.5))

        dropped = stage_clean_station_input(self.station, self.destination)

        self.assertEqual(dropped, 2)
        self.assertEqual(sorted(p.name for p in self.destination.iterdir()),
                         ["aksu_G01_100_26.dat", "aksu_G01_100_26__dup1.dat"])
        self.assertEqual((self.destination / "aksu_G01_100_26__dup1.dat").read_text(),
                         HEADER + row(10, -12.0, 22.5))

    def test_file_left_without_data_rows_is_omitted(self) -> None:
        self.write("aksu_G01_100_26.dat", row(1, -30.1, 25.0), row(2, 0.0, 25.0))
        self.write("aksu_E05_100_26.dat", row(1, 0.0, 0.0), row(2, 0.0, 0.0))

        stage_clean_station_input(self.station, self.destination)

        self.assertEqual([p.name for p in self.destination.iterdir()], ["aksu_G01_100_26.dat"])

    def test_nothing_to_drop_means_no_cleaned_run(self) -> None:
        self.write("aksu_G01_100_26.dat", row(1, -30.1, 25.0), row(2, -30.2, 25.1))

        self.assertIsNone(stage_clean_station_input(self.station, self.destination))

    def test_no_code_left_means_no_cleaned_run(self) -> None:
        # Code arrives only on a satellite without phase; cleaning would remove all of it.
        self.write("aksu_G01_100_26.dat", row(1, -30.1, 0.0), row(2, -30.2, 0.0))
        self.write("aksu_G02_100_26.dat", row(1, 0.0, 25.0), row(2, 0.0, 25.4))

        self.assertIsNone(stage_clean_station_input(self.station, self.destination))


class CountSolvedRowsTests(unittest.TestCase):
    def test_counts_non_zero_tec_rows_of_the_result_file_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "aksu"
            result_file(folder, "aksu_100_2026.dat", solved=30)
            result_file(folder, "DCB_aksu_100_2026.dat", solved=48)

            self.assertEqual(count_solved_rows(folder), 30)

    def test_missing_folder_counts_zero(self) -> None:
        self.assertEqual(count_solved_rows(Path(tempfile.gettempdir()) / "no-such-station-output"), 0)


class RunCleanedThenRawTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.destination = root / "2026" / "100" / "aksu"
        self.hold = root / "_stage"
        self.calls: list[str] = []

    def runner(self, cleaned_solved: int | Exception | None, raw_solved: int | Exception | None):
        def run_once(cleaned: bool) -> None:
            self.calls.append("cleaned" if cleaned else "raw")
            outcome = cleaned_solved if cleaned else raw_solved
            if isinstance(outcome, Exception):
                raise outcome
            if outcome is not None:
                result_file(self.destination, "aksu_100_2026.dat", solved=outcome)
        return run_once

    def run_case(self, cleaned_solved, raw_solved, **kwargs) -> str:
        return run_cleaned_then_raw(
            self.runner(cleaned_solved, raw_solved),
            destination=self.destination,
            hold_dir=self.hold,
            expected_rows=48,
            label="aksu",
            **kwargs,
        )

    def held(self) -> list[str]:
        return sorted(p.name for p in self.hold.iterdir()) if self.hold.exists() else []

    def test_complete_cleaned_result_skips_the_raw_run(self) -> None:
        self.assertEqual(self.run_case(48, 10), "cleaned")
        self.assertEqual(self.calls, ["cleaned"])
        self.assertEqual(count_solved_rows(self.destination), 48)

    def test_better_raw_result_is_kept(self) -> None:
        self.assertEqual(self.run_case(5, 20), "raw")
        self.assertEqual(self.calls, ["cleaned", "raw"])
        self.assertEqual(count_solved_rows(self.destination), 20)
        self.assertEqual(self.held(), [])

    def test_better_cleaned_result_is_kept(self) -> None:
        self.assertEqual(self.run_case(30, 8), "cleaned")
        self.assertEqual(count_solved_rows(self.destination), 30)
        self.assertEqual(self.held(), [])

    def test_failed_cleaned_run_falls_back_to_raw(self) -> None:
        failures: list[str] = []

        kept = self.run_case(RuntimeError("absolTEC exited with code 64"), 20,
                             after_failure=lambda: failures.append("cleanup"))

        self.assertEqual(kept, "raw")
        self.assertEqual(failures, ["cleanup"])
        self.assertEqual(count_solved_rows(self.destination), 20)

    def test_failed_raw_run_keeps_the_cleaned_result(self) -> None:
        self.assertEqual(self.run_case(30, RuntimeError("boom")), "cleaned")
        self.assertEqual(count_solved_rows(self.destination), 30)

    def test_raw_failure_without_a_cleaned_result_is_raised(self) -> None:
        with self.assertRaises(RuntimeError):
            self.run_case(None, RuntimeError("boom"))

    def test_earlier_result_is_replaced_by_the_new_one(self) -> None:
        result_file(self.destination, "aksu_100_2026.dat", solved=40)

        self.run_case(5, 20)

        self.assertEqual(count_solved_rows(self.destination), 20)
        self.assertEqual(self.held(), [])

    def test_earlier_result_survives_when_neither_run_produces_one(self) -> None:
        result_file(self.destination, "aksu_100_2026.dat", solved=40)

        self.run_case(None, None)

        self.assertEqual(count_solved_rows(self.destination), 40)
        self.assertEqual(self.held(), [])


class SubmitDockurJobInputTests(unittest.TestCase):
    def test_staged_input_travels_in_the_job_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "staged"
            (staged / "2026" / "100" / "aksu").mkdir(parents=True)
            (staged / "2026" / "100" / "aksu" / "aksu_G01_100_26.dat").write_text(HEADER, encoding="ascii")
            dia = "W:\\in\\\n10.0\n2026\n100\naksu\n0.5\n0.97\n"

            job_dir = submit_dockur_job(Path(tmp) / "jobs", dia, 2026, "2026_100_aksu", input_root=staged)

            self.assertTrue((job_dir / "in" / "2026" / "100" / "aksu" / "aksu_G01_100_26.dat").is_file())
            lines = (job_dir / "absolTEC.dia").read_text(encoding="utf-8").split("\n")
            self.assertEqual(lines[0], f"W:\\jobs\\{job_dir.name}\\in\\")
            self.assertEqual(lines[1:5], ["10.0", "2026", "100", "aksu"])
            self.assertTrue((job_dir / "job.ready").exists())


class RunSingleStationCleanInputTests(unittest.TestCase):
    """--clean-input end to end with the direct runner and a stand-in absolTEC."""

    def test_cleaned_run_reads_the_staged_copy_and_raw_run_the_original(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            station = root / "in" / "2026" / "100" / "aksu"
            station.mkdir(parents=True)
            (station / "aksu_G01_100_26.dat").write_text(
                HEADER + row(1, -30.1, 25.0) + row(2, 0.0, 25.0) + row(3, -30.3, 24.0), encoding="ascii"
            )
            workdir = root / "work"
            workdir.mkdir()
            dia_path = workdir / "absolTEC.dia"
            output_dir = root / "out"
            seen_inputs: list[str] = []

            def fake_absoltec(exe_path, runner, timeout_seconds=None) -> None:
                input_root = Path(dia_path.read_text(encoding="utf-8").split("\n")[0].rstrip("\\/"))
                staged = input_root != root / "in"
                seen_inputs.append("cleaned" if staged else "raw")
                data = (input_root / "2026" / "100" / "aksu" / "aksu_G01_100_26.dat").read_text()
                # The stand-in solves fewer half-hours on the cleaned copy, so both runs happen.
                self.assertEqual(" 0.000 " in data, not staged)
                result_file(workdir / "2026" / "aksu", "aksu_100_2026.dat", solved=10 if staged else 20)

            with patch("run_absoltec.run_absoltec", side_effect=fake_absoltec):
                status = run_single_station(
                    workdir=workdir, dia_path=dia_path, exe_path=workdir / "absolTEC.exe",
                    dat_path_obj=root / "in", output_dir=output_dir, year=2026, day_of_year=100,
                    site="aksu", elevation_cutoff=10.0, time_step_hours=0.5, correction_coefficient=0.97,
                    dry_run=False, runner="direct", execution_timeout_seconds=0, organize_by_day=True,
                    clean_input=True,
                )

            self.assertEqual(status, "ok")
            self.assertEqual(seen_inputs, ["cleaned", "raw"])
            self.assertEqual(count_solved_rows(output_dir / "2026" / "100" / "aksu"), 20)
            self.assertEqual(list((output_dir / "_stage").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
