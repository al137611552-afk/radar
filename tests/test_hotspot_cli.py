import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import hotspot_cli


class HotspotCliTests(unittest.TestCase):
    def setUp(self):
        self.source = pd.DataFrame([
            {
                "code": "rb2610", "name": "螺纹钢", "side": "多", "side_rank": 1,
                "turnover_yi": 305.05, "change_pct": 1.25, "oi_change": 20000,
                "oi_change_pct": 2.5, "position_action": "多头增仓",
                "turnover_share_pct": 12.345,
            },
            {
                "code": "m2609", "name": "豆粕", "side": "空", "side_rank": 1,
                "turnover_yi": 200.0, "change_pct": -1.5, "oi_change": 10000,
                "oi_change_pct": 1.2, "position_action": "空头增仓",
                "turnover_share_pct": 8.0,
            },
        ])

    def test_builds_long_turnover_leaderboard(self):
        display = hotspot_cli.build_side_table(self.source, "多", top=10)

        self.assertEqual(display.columns.tolist(), [
            "排名", "代码", "名称", "成交额(亿)", "涨跌%", "持仓变化",
            "持仓变化%", "资金结构", "市场占比%",
        ])
        self.assertEqual(display["代码"].tolist(), ["rb2610"])
        self.assertEqual(display.loc[0, "成交额(亿)"], 305.05)
        self.assertEqual(display.loc[0, "资金结构"], "多头增仓")

    def test_renders_standalone_turnover_heatmap(self):
        html = hotspot_cli.render_heatmap_html(
            self.source, title="商品期货热点雷达"
        )

        self.assertIn("<!doctype html>", html.lower())
        self.assertIn("商品期货热点雷达", html)
        self.assertIn("rb2610", html)
        self.assertIn("m2609", html)
        self.assertIn('class="tile bull"', html)
        self.assertIn('class="tile bear"', html)
        self.assertIn("按成交额排序", html)
        self.assertNotIn("面积代表成交额", html)
        self.assertNotIn("cdn.", html.lower())

    def test_main_writes_csv_and_html_artifacts(self):
        metadata = [{
            "code": "rb2610", "name": "螺纹钢", "exchange_code": "SHFE",
            "multiplier": 10,
        }]
        bars = pd.DataFrame({
            "datetime": pd.to_datetime(["2026-07-14", "2026-07-15"]),
            "close": [3000, 3030], "volume": [100, 200],
            "money": [1_000_000, 2_000_000], "open_interest": [1000, 1100],
        })

        class Client:
            def main_contracts(self):
                return {"rb9999": "rb2610"}

            def search(self, **kwargs):
                return metadata

            def get_klines_by_count(self, codes, interval, count):
                return {"rb2610": bars}

        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "radar.csv"
            html_path = Path(directory) / "radar.html"
            output = StringIO()
            with redirect_stdout(output):
                code = hotspot_cli.main([
                    "--top", "5", "--csv", str(csv_path),
                    "--html", str(html_path),
                ], client=Client())

            self.assertEqual(code, 0)
            self.assertTrue(csv_path.exists())
            self.assertTrue(html_path.exists())
            self.assertIn("多头成交额TOP", output.getvalue())


if __name__ == "__main__":
    unittest.main()
