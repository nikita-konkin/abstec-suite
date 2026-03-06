import tempfile
import unittest
from pathlib import Path

from generate_absoltec_launchers import discover_stations, render_launcher_content


class GenerateLaunchersTests(unittest.TestCase):
    def test_discover_stations_returns_sorted_directory_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "zeta0010").mkdir()
            (root / "alfa0010").mkdir()
            (root / "notes.txt").write_text("x", encoding="utf-8")

            stations = discover_stations(root)

        self.assertEqual(stations, ["alfa0010", "zeta0010"])

    def test_render_launcher_content_contains_station_and_flags(self) -> None:
        content = render_launcher_content(
            site="aksu0010",
            dat_path="c:\\dat",
            year=2026,
            day_of_year=1,
            workdir="TayAbsTEC_24.04.17",
            dry_run=True,
            python_exe=r"C:\\Python\\Python312\\python.exe",
        )
        self.assertIn("set \"SITE=aksu0010\"", content)
        self.assertIn("set \"DRY_RUN=1\"", content)
        self.assertIn("--day-of-year \"%DAY_OF_YEAR%\"", content)


if __name__ == "__main__":
    unittest.main()