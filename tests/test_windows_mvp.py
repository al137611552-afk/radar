import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsMvpTests(unittest.TestCase):
    def test_option_scan_separates_candidates_from_dashboard_signals(self):
        script = (ROOT / "windows_mvp.ps1").read_text(encoding="utf-8")

        self.assertIn(
            "--snapshot-csv output/options_candidates_latest.csv", script
        )
        self.assertIn("--filtered-csv output/options_latest.csv", script)


if __name__ == "__main__":
    unittest.main()
