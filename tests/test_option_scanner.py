import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import option_scanner


class OptionUniverseTests(unittest.TestCase):
    def test_selects_active_commodity_options_inside_dte_window(self):
        base = {
            "category_type": 7, "variety_type": 1, "exchange_code": "SHFE",
            "start_date": "2026-01-01 00:00:00", "options_cp_type": 1,
            "options_target_code": "cu2608", "options_exercise_price": 100000,
        }
        instruments = [
            {**base, "code": "near1", "expire_time": "2026-07-15 00:00:00"},
            {**base, "code": "near14", "expire_time": "2026-07-28 00:00:00"},
            {**base, "code": "day15", "expire_time": "2026-07-29 00:00:00"},
            {**base, "code": "expired", "expire_time": "2026-07-13 00:00:00"},
            {**base, "code": "financial", "exchange_code": "CFFEX",
             "expire_time": "2026-07-20 00:00:00"},
        ]

        result = option_scanner.select_near_expiry_options(
            instruments, as_of=pd.Timestamp("2026-07-14", tz="Asia/Shanghai"),
            min_dte=1, max_dte=15,
        )

        self.assertEqual([item["code"] for item in result], ["near1", "near14"])
        self.assertEqual([item["dte"] for item in result], [1, 14])

    def test_keeps_nearest_strikes_around_underlying_price(self):
        options = [
            {"code": "c80", "options_target_code": "cu2608",
             "options_cp_type": 1, "options_exercise_price": 80},
            {"code": "c95", "options_target_code": "cu2608",
             "options_cp_type": 1, "options_exercise_price": 95},
            {"code": "c105", "options_target_code": "cu2608",
             "options_cp_type": 1, "options_exercise_price": 105},
            {"code": "c120", "options_target_code": "cu2608",
             "options_cp_type": 1, "options_exercise_price": 120},
        ]

        result = option_scanner.select_nearest_strikes(
            options, {"cu2608": 100}, strikes_per_side=2,
            max_moneyness=0.15,
        )

        self.assertEqual([item["code"] for item in result], ["c95", "c105"])
        self.assertEqual([item["moneyness"] for item in result], [-0.05, 0.05])

    def test_keeps_only_hour_bars_closed_by_scan_time(self):
        bars = pd.DataFrame({
            "datetime": pd.to_datetime([
                "2026-07-14 20:00", "2026-07-14 21:00", "2026-07-14 22:00"
            ]),
            "close": [100, 101, 999],
        })

        result = option_scanner.closed_hour_bars(
            bars, now=pd.Timestamp("2026-07-14 21:31", tz="Asia/Shanghai")
        )

        self.assertEqual(result["close"].tolist(), [100, 101])


class HourlySignalTests(unittest.TestCase):
    def test_detects_ma_cross_on_latest_closed_bar(self):
        bars = pd.DataFrame({
            "datetime": pd.date_range("2026-07-14 10:00", periods=4, freq="h"),
            "close": [3.0, 2.0, 1.0, 4.0],
            "volume": [10, 10, 10, 10],
            "open_interest": [100, 100, 100, 100],
        })

        result = option_scanner.analyze_hourly_signal(
            bars, ma_fast=2, ma_slow=3, cross_lookback=3
        )

        self.assertTrue(result["ma_bullish"])
        self.assertTrue(result["ma_cross_now"])
        self.assertEqual(result["ma_cross_bars_ago"], 0)
        self.assertAlmostEqual(result["ma_fast"], 2.5)
        self.assertAlmostEqual(result["ma_slow"], 7 / 3)

    def test_reports_recent_macd_cross_and_current_state(self):
        bars = pd.DataFrame({
            "datetime": pd.date_range("2026-07-14 09:00", periods=7, freq="h"),
            "close": [5.0, 4.0, 3.0, 2.0, 1.0, 2.0, 3.0],
        })

        result = option_scanner.analyze_hourly_signal(
            bars, ma_fast=2, ma_slow=3, macd_fast=2, macd_slow=4,
            macd_signal=2, cross_lookback=3,
        )

        self.assertTrue(result["macd_bullish"])
        self.assertFalse(result["macd_cross_now"])
        self.assertEqual(result["macd_cross_bars_ago"], 1)
        self.assertGreater(result["macd_line"], result["macd_signal"])

    def test_liquidity_uses_recent_trading_activity(self):
        bars = pd.DataFrame({
            "datetime": pd.date_range("2026-07-13 09:00", periods=20, freq="h"),
            "close": range(20),
            "volume": [10] * 20,
            "open_interest": [500] * 20,
        })

        result = option_scanner.assess_liquidity(
            bars, now=pd.Timestamp("2026-07-14 05:00", tz="Asia/Shanghai"),
            lookback=20, min_nonzero_bars=10, min_volume=100,
            min_open_interest=100, max_stale_hours=48,
        )

        self.assertTrue(result["liquid"])
        self.assertEqual(result["nonzero_volume_bars"], 20)
        self.assertEqual(result["recent_volume"], 200)
        self.assertEqual(result["open_interest"], 500)
        self.assertEqual(result["stale_hours"], 1.0)

    def test_direction_confirmation_respects_call_and_put_direction(self):
        option_signal = {"ma_bullish": True, "macd_bullish": True}
        underlying_signal = {"ma_bullish": True, "macd_bullish": True}

        call = option_scanner.direction_confirmation(
            "CALL", option_signal, underlying_signal
        )
        put = option_scanner.direction_confirmation(
            "PUT", option_signal, underlying_signal
        )

        self.assertTrue(call["ma_direction_confirmed"])
        self.assertTrue(call["double_confirmed"])
        self.assertFalse(put["ma_direction_confirmed"])
        self.assertFalse(put["double_confirmed"])


class ScannerIntegrationTests(unittest.TestCase):
    def test_scans_candidates_in_one_batch_and_returns_signal_rows(self):
        metadata = {
            "code": "cu2608C100000", "name": "沪铜2608购100000",
            "category_type": 7, "variety_type": 1, "exchange_code": "SHFE",
            "start_date": "2026-01-01", "expire_time": "2026-07-20",
            "options_cp_type": 1, "options_target_code": "cu2608",
            "options_exercise_price": 3,
        }
        bars = pd.DataFrame({
            "datetime": pd.date_range("2026-07-14 09:00", periods=7, freq="h"),
            "close": [5.0, 4.0, 3.0, 2.0, 1.0, 2.0, 3.0],
            "volume": [10] * 7,
            "open_interest": [500] * 7,
        })

        class Client:
            def __init__(self):
                self.fetch_calls = []

            def search(self, **kwargs):
                self.search_args = kwargs
                return [metadata]

            def get_klines_by_count(self, codes, interval, count):
                self.fetch_calls.append((codes, interval, count))
                if codes == ["cu2608"]:
                    return {"cu2608": bars.copy()}
                return {metadata["code"]: bars}

        client = Client()
        result = option_scanner.scan_near_expiry_options(
            client, now=pd.Timestamp("2026-07-14 16:00", tz="Asia/Shanghai"),
            ma_fast=2, ma_slow=3, macd_fast=2, macd_slow=4,
            macd_signal=2, cross_lookback=3, kline_count=10,
            liquidity_lookback=5, min_nonzero_bars=3, min_volume=20,
            min_open_interest=100,
        )

        self.assertEqual(client.search_args, {"category_type": 7})
        self.assertEqual(client.fetch_calls, [
            (["cu2608"], "1h", 10),
            ([metadata["code"]], "1h", 10),
        ])
        self.assertEqual(result.loc[0, "code"], metadata["code"])
        self.assertEqual(result.loc[0, "dte"], 6)
        self.assertTrue(result.loc[0, "liquid"])
        self.assertTrue(result.loc[0, "ma_bullish"])
        self.assertEqual(result.loc[0, "macd_cross_bars_ago"], 1)
        self.assertTrue(result.loc[0, "underlying_ma_bullish"])
        self.assertEqual(result.loc[0, "underlying_macd_cross_bars_ago"], 1)
        self.assertTrue(result.loc[0, "double_confirmed"])
        self.assertEqual(result.loc[0, "confirmation_score"], 8)


if __name__ == "__main__":
    unittest.main()
