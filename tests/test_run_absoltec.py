import tempfile
import unittest
from pathlib import Path

from run_absoltec import (
    build_dia_content,
    normalize_dat_path,
    update_dia_file,
    validate_dat_inputs,
)


class RunAbsoltecTests(unittest.TestCase):
    def test_normalize_dat_path_adds_trailing_backslash(self) -> None:
        self.assertEqual(normalize_dat_path("c:\\dat"), "c:\\dat\\")

    def test_normalize_dat_path_keeps_existing_separator(self) -> None:
        self.assertEqual(normalize_dat_path("c:\\dat\\"), "c:\\dat\\")

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


if __name__ == "__main__":
    unittest.main()