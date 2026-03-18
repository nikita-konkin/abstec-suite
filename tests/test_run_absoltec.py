import subprocess
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import MagicMock, patch

from run_absoltec import (
    build_dia_content,
    capture_workdir_state,
    discover_stations_for_day,
    find_wine_binary,
    is_windows_path,
    move_absoltec_results,
    normalize_dat_path,
    organize_station_output_by_day,
    parse_days_list,
    rename_station_output,
    resolve_runner_command,
    should_use_wine,
    to_wine_windows_path,
    run_absoltec,
    update_dia_file,
    validate_wine_runtime,
    validate_dat_inputs,
)


class RunAbsoltecTests(unittest.TestCase):
    def test_parse_days_list_supports_csv_and_ranges(self) -> None:
        parsed = parse_days_list("001, 002, 010-012")
        self.assertEqual(parsed, [1, 2, 10, 11, 12])

    def test_parse_days_list_deduplicates_preserving_order(self) -> None:
        parsed = parse_days_list("001,001,003,002,003")
        self.assertEqual(parsed, [1, 3, 2])

    def test_parse_days_list_rejects_invalid_range(self) -> None:
        with self.assertRaises(ValueError):
            parse_days_list("005-002")

    def test_discover_stations_for_day_returns_sorted_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dat_root = Path(temp_dir)
            day_root = dat_root / "2026" / "001"
            (day_root / "zeta0010").mkdir(parents=True)
            (day_root / "alfa0010").mkdir(parents=True)
            (day_root / "note.txt").write_text("x", encoding="utf-8")

            stations = discover_stations_for_day(dat_root, 2026, 1)

            self.assertEqual(stations, ["alfa0010", "zeta0010"])

    def test_discover_stations_for_day_skips_prefix_duplicate_when_full_station_has_same_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dat_root = Path(temp_dir)
            day_root = dat_root / "2026" / "001"
            alex_dir = day_root / "alex"
            alex_full_dir = day_root / "alex0010"
            alex_dir.mkdir(parents=True)
            alex_full_dir.mkdir(parents=True)
            (day_root / "alfa0010").mkdir(parents=True)
            (alex_dir / "alex_G01_001_26.dat").write_text("1 0.1 10 20 42.7 1.234 0\n", encoding="utf-8")
            (alex_full_dir / "alex_G01_001_26.dat").write_text("1 0.1 10 20 42.7 1.234 0\n", encoding="utf-8")

            stations = discover_stations_for_day(dat_root, 2026, 1)

            self.assertEqual(stations, ["alex0010", "alfa0010"])

    def test_discover_stations_for_day_keeps_4char_station_when_longer_folder_has_different_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dat_root = Path(temp_dir)
            day_root = dat_root / "2026" / "001"
            alex_dir = day_root / "alex"
            alex_full_dir = day_root / "alex0010"
            alex_dir.mkdir(parents=True)
            alex_full_dir.mkdir(parents=True)
            (alex_dir / "alex_G01_001_26.dat").write_text("1 0.1 10 20 42.7 1.234 0\n", encoding="utf-8")
            (alex_full_dir / "alex_G02_001_26.dat").write_text("1 0.1 10 20 42.7 1.234 0\n", encoding="utf-8")

            stations = discover_stations_for_day(dat_root, 2026, 1)

            self.assertEqual(stations, ["alex", "alex0010"])

    def test_discover_stations_for_day_returns_empty_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dat_root = Path(temp_dir)
            stations = discover_stations_for_day(dat_root, 2026, 1)
            self.assertEqual(stations, [])

    def test_normalize_dat_path_adds_trailing_backslash(self) -> None:
        self.assertEqual(normalize_dat_path("c:\\dat"), "c:\\dat\\")

    def test_normalize_dat_path_keeps_existing_separator(self) -> None:
        self.assertEqual(normalize_dat_path("c:\\dat\\"), "c:\\dat\\")

    def test_is_windows_path_detects_drive_paths(self) -> None:
        self.assertTrue(is_windows_path("C:\\data"))
        self.assertFalse(is_windows_path("/Users/w/data"))

    def test_to_wine_windows_path_keeps_windows_path(self) -> None:
        self.assertEqual(to_wine_windows_path("D:\\tec\\out"), "D:\\tec\\out")

    def test_to_wine_windows_path_converts_posix_path(self) -> None:
        converted = to_wine_windows_path("/tmp")
        self.assertTrue(converted.startswith("Z:\\"))

    def test_should_use_wine_for_auto_non_windows_exe(self) -> None:
        with patch("run_absoltec.platform.system", return_value="Darwin"):
            self.assertTrue(should_use_wine(Path("/tmp/absolTEC.exe"), "auto"))

    def test_build_dia_content_has_expected_order(self) -> None:
        content = build_dia_content(
            dat_path="c:\\dat",
            elevation_cutoff=10,
            year=2026,
            day_of_year=1,
            site="aksu0010",
            time_step_hours=0.5,
            correction_coefficient=0.97,
        )
        expected = "\n".join(
            ["c:\\dat\\", "10", "2026", "1", "aksu0010", "0.5", "0.97", ""]
        )
        self.assertEqual(content, expected)

    def test_update_dia_file_writes_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dia_path = Path(temp_dir) / "absolTEC.dia"
            update_dia_file(
                dia_path=dia_path,
                dat_path="c:\\dat",
                elevation_cutoff=15,
                year=2026,
                day_of_year=45,
                site="test0001",
                time_step_hours=1,
                correction_coefficient=0.94,
            )
            content = dia_path.read_text(encoding="utf-8")

        self.assertIn("test0001", content)
        self.assertIn("45", content)
        self.assertTrue(content.endswith("\n"))

    def test_validate_dat_inputs_raises_when_no_pattern_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dat_dir = Path(temp_dir)
            station_dir = dat_dir / "2026" / "001" / "aksu"
            station_dir.mkdir(parents=True)
            (station_dir / "other_G01_001_26.dat").write_text("1 0.1 10 20 1 2 1\n", encoding="utf-8")

            with self.assertRaises(FileNotFoundError):
                validate_dat_inputs(dat_dir, site="aksu", day_of_year=1, year=2026)

    def test_validate_dat_inputs_raises_when_bias_column_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dat_dir = Path(temp_dir)
            station_dir = dat_dir / "2026" / "001" / "aksu"
            station_dir.mkdir(parents=True)
            (station_dir / "aksu_G01_001_26.dat").write_text(
                "# header\n1 0.1 10 20 42.7 0.000 0\n2 0.2 11 21 42.8 0.000 0\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                validate_dat_inputs(dat_dir, site="aksu", day_of_year=1, year=2026)

    def test_validate_dat_inputs_accepts_non_zero_bias(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dat_dir = Path(temp_dir)
            station_dir = dat_dir / "2026" / "001" / "aksu"
            station_dir.mkdir(parents=True)
            (station_dir / "aksu_G01_001_26.dat").write_text(
                "1 0.1 10 20 42.7 1.234 0\n",
                encoding="utf-8",
            )

            validate_dat_inputs(dat_dir, site="aksu", day_of_year=1, year=2026)

    def test_resolve_runner_command_auto_windows_runs_direct(self) -> None:
        exe_path = Path("/tmp/absolTEC.exe")
        with patch("run_absoltec.platform.system", return_value="Windows"):
            command = resolve_runner_command(exe_path, "auto")
        self.assertEqual(command, [str(exe_path)])

    def test_resolve_runner_command_auto_non_windows_uses_wine(self) -> None:
        exe_path = Path("/tmp/absolTEC.exe")
        with (
            patch("run_absoltec.platform.system", return_value="Darwin"),
            patch("run_absoltec.shutil.which", side_effect=["/usr/local/bin/wine64", None]),
            patch("run_absoltec.validate_wine_runtime"),
        ):
            command = resolve_runner_command(exe_path, "auto")
        self.assertEqual(command, ["/usr/local/bin/wine64", str(exe_path)])

    def test_resolve_runner_command_auto_non_windows_without_wine_raises(self) -> None:
        exe_path = Path("/tmp/absolTEC.exe")
        with (
            patch("run_absoltec.platform.system", return_value="Darwin"),
            patch("run_absoltec.shutil.which", return_value=None),
            patch("run_absoltec.Path.exists", return_value=False),
        ):
            with self.assertRaises(RuntimeError):
                resolve_runner_command(exe_path, "auto")

    def test_resolve_runner_command_explicit_wine_uses_wine_binary(self) -> None:
        exe_path = Path("/tmp/absolTEC.exe")
        with (
            patch("run_absoltec.shutil.which", side_effect=["/usr/bin/wine64", None]),
            patch("run_absoltec.validate_wine_runtime"),
        ):
            command = resolve_runner_command(exe_path, "wine")
        self.assertEqual(command, ["/usr/bin/wine64", str(exe_path)])

    def test_resolve_runner_command_explicit_wine_without_wine_raises(self) -> None:
        exe_path = Path("/tmp/absolTEC.exe")
        with (
            patch("run_absoltec.shutil.which", return_value=None),
            patch("run_absoltec.Path.exists", return_value=False),
        ):
            with self.assertRaises(RuntimeError):
                resolve_runner_command(exe_path, "wine")

    def test_find_wine_binary_falls_back_to_known_path(self) -> None:
        with (
            patch("run_absoltec.shutil.which", return_value=None),
            patch("run_absoltec.Path.exists", autospec=True) as mock_exists,
        ):
            def fake_exists(path_self: Path) -> bool:
                return str(path_self).replace("\\", "/") == "/usr/lib/wine/wine"

            mock_exists.side_effect = fake_exists
            found = find_wine_binary()

        if found is None:
            self.fail("Expected wine fallback path to be detected")
        self.assertEqual(found.replace("\\", "/"), "/usr/lib/wine/wine")

    def test_resolve_runner_command_explicit_wine_uses_known_path_fallback(self) -> None:
        exe_path = Path("/tmp/absolTEC.exe")
        with (
            patch("run_absoltec.shutil.which", return_value=None),
            patch("run_absoltec.Path.exists", autospec=True) as mock_exists,
            patch("run_absoltec.validate_wine_runtime"),
        ):
            def fake_exists(path_self: Path) -> bool:
                return str(path_self).replace("\\", "/") == "/usr/lib/wine/wine"

            mock_exists.side_effect = fake_exists
            command = resolve_runner_command(exe_path, "wine")

        self.assertEqual(command[0].replace("\\", "/"), "/usr/lib/wine/wine")
        self.assertEqual(command[1], str(exe_path))

    def test_validate_wine_runtime_ok(self) -> None:
        with patch(
            "run_absoltec.subprocess.run",
            return_value=CompletedProcess(args=["wine", "--version"], returncode=0, stdout="wine-9.0", stderr=""),
        ):
            validate_wine_runtime("/opt/homebrew/bin/wine")

    def test_run_absoltec_linux_arm64_exe_raises_clear_error(self) -> None:
        exe_path = Path("/tmp/absolTEC.exe")
        with (
            patch("run_absoltec.platform.system", return_value="Linux"),
            patch("run_absoltec.platform.machine", return_value="arm64"),
        ):
            with self.assertRaisesRegex(RuntimeError, "linux/amd64"):
                run_absoltec(exe_path, "wine")

    def test_validate_wine_runtime_sigkill_raises(self) -> None:
        with patch(
            "run_absoltec.subprocess.run",
            return_value=CompletedProcess(args=["wine", "--version"], returncode=-9, stdout="", stderr=""),
        ):
            with self.assertRaises(RuntimeError):
                validate_wine_runtime("/opt/homebrew/bin/wine")

    def test_run_absoltec_passes_timeout_to_subprocess(self) -> None:
        exe_path = Path("/tmp/absolTEC.exe")
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("ok", "")
        mock_proc.returncode = 0
        with (
            patch("run_absoltec.resolve_runner_command", return_value=["wine", str(exe_path)]),
            patch("run_absoltec.subprocess.Popen", return_value=mock_proc),
        ):
            run_absoltec(exe_path, "wine", timeout_seconds=30)

        mock_proc.communicate.assert_called_once_with(timeout=30)

    def test_run_absoltec_timeout_raises_runtime_error(self) -> None:
        exe_path = Path("/tmp/absolTEC.exe")
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.communicate.side_effect = [
            TimeoutExpired(cmd=["wine", str(exe_path)], timeout=5),
            ("", ""),
        ]
        with (
            patch("run_absoltec.resolve_runner_command", return_value=["wine", str(exe_path)]),
            patch("run_absoltec.subprocess.Popen", return_value=mock_proc),
            patch("run_absoltec._kill_wine_process_group"),
        ):
            with self.assertRaises(RuntimeError):
                run_absoltec(exe_path, "wine", timeout_seconds=5)

    def test_capture_workdir_state_ignores_binary_and_dia(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir)
            (workdir / "absolTEC.exe").write_text("x", encoding="utf-8")
            (workdir / "absolTEC.dia").write_text("x", encoding="utf-8")
            (workdir / "2026").mkdir()

            state = capture_workdir_state(workdir)

        self.assertNotIn("absolTEC.exe", state)
        self.assertNotIn("absolTEC.dia", state)
        self.assertIn("2026", state)

    def test_move_absoltec_results_moves_year_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workdir = root / "work"
            output = root / "output"
            year_dir = workdir / "2026"
            year_dir.mkdir(parents=True)
            (year_dir / "result.txt").write_text("ok", encoding="utf-8")

            moved = move_absoltec_results(workdir, output, 2026, before_state={})

            self.assertEqual([path.name for path in moved], ["2026"])
            self.assertTrue((output / "2026" / "result.txt").exists())
            self.assertFalse((workdir / "2026" / "result.txt").exists())

    def test_rename_station_output_renames_4char_prefix_to_full_site(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            station_dir = output / "2026" / "aksu"
            station_dir.mkdir(parents=True)
            (station_dir / "result.txt").write_text("ok", encoding="utf-8")

            renamed = rename_station_output(output, 2026, "aksu0010")

            self.assertIsNotNone(renamed)
            self.assertEqual(renamed.name, "aksu0010")
            self.assertTrue((output / "2026" / "aksu0010" / "result.txt").exists())
            self.assertFalse((output / "2026" / "aksu").exists())

    def test_rename_station_output_no_op_when_site_is_4chars(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            station_dir = output / "2026" / "aksu"
            station_dir.mkdir(parents=True)

            result = rename_station_output(output, 2026, "aksu")

            self.assertIsNone(result)
            self.assertTrue((output / "2026" / "aksu").exists())

    def test_rename_station_output_no_op_when_src_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            (output / "2026").mkdir(parents=True)

            result = rename_station_output(output, 2026, "aksu0010")

            self.assertIsNone(result)

    def test_organize_station_output_by_day_moves_station_under_doy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            station_dir = output / "2026" / "aksu0010"
            station_dir.mkdir(parents=True)
            (station_dir / "result.txt").write_text("ok", encoding="utf-8")

            organized = organize_station_output_by_day(output, 2026, 1, "aksu0010")

            self.assertIsNotNone(organized)
            self.assertEqual(organized, output / "2026" / "001" / "aksu0010")
            self.assertTrue((output / "2026" / "001" / "aksu0010" / "result.txt").exists())
            self.assertFalse((output / "2026" / "aksu0010").exists())

    def test_organize_station_output_by_day_no_op_when_station_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            (output / "2026").mkdir(parents=True)

            organized = organize_station_output_by_day(output, 2026, 1, "aksu0010")

            self.assertIsNone(organized)

    def test_move_absoltec_results_uses_changed_items_when_year_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workdir = root / "work"
            output = root / "output"
            workdir.mkdir(parents=True)
            log_file = workdir / "absolTEC.log"
            log_file.write_text("v1", encoding="utf-8")

            before = capture_workdir_state(workdir)
            log_file.write_text("v2", encoding="utf-8")

            moved = move_absoltec_results(workdir, output, 2026, before_state=before)

            self.assertEqual([path.name for path in moved], ["absolTEC.log"])
            self.assertTrue((output / "absolTEC.log").exists())
            self.assertFalse(log_file.exists())


if __name__ == "__main__":
    unittest.main()