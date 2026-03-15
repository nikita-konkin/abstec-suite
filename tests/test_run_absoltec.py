import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from run_absoltec import (
    build_dia_content,
    find_wine_binary,
    is_windows_path,
    normalize_dat_path,
    resolve_runner_command,
    should_use_wine,
    to_wine_windows_path,
    update_dia_file,
    validate_wine_runtime,
    validate_dat_inputs,
)


class RunAbsoltecTests(unittest.TestCase):
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
        with patch("run_absoltec.shutil.which", return_value=None):
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

    def test_validate_wine_runtime_sigkill_raises(self) -> None:
        with patch(
            "run_absoltec.subprocess.run",
            return_value=CompletedProcess(args=["wine", "--version"], returncode=-9, stdout="", stderr=""),
        ):
            with self.assertRaises(RuntimeError):
                validate_wine_runtime("/opt/homebrew/bin/wine")


if __name__ == "__main__":
    unittest.main()