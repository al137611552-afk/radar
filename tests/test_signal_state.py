import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import signal_state


FIELDS = (
    "ma_cross_time", "macd_cross_time", "double_confirmed",
    "ma_direction_confirmed", "macd_direction_confirmed",
)


class SignalStateTests(unittest.TestCase):
    def test_emits_first_seen_once_and_suppresses_identical_signal(self):
        current = pd.DataFrame([{
            "code": "A", "ma_cross_time": pd.Timestamp("2026-07-14 10:00"),
            "macd_cross_time": None, "double_confirmed": True,
            "ma_direction_confirmed": True, "macd_direction_confirmed": False,
        }])

        first, state = signal_state.diff_signals(
            current, {}, fingerprint_fields=FIELDS, scope="option:double"
        )
        repeated, repeated_state = signal_state.diff_signals(
            current, state, fingerprint_fields=FIELDS, scope="option:double"
        )

        self.assertEqual(first["alert_type"].tolist(), ["首次命中"])
        self.assertTrue(repeated.empty)
        self.assertEqual(repeated_state["signals"], state["signals"])

    def test_classifies_new_cross_and_confirmation_change(self):
        before = pd.DataFrame([
            {"code": "A", "ma_cross_time": "2026-07-14T10:00:00",
             "macd_cross_time": None, "double_confirmed": True,
             "ma_direction_confirmed": True, "macd_direction_confirmed": False},
            {"code": "B", "ma_cross_time": None, "macd_cross_time": None,
             "double_confirmed": False, "ma_direction_confirmed": False,
             "macd_direction_confirmed": False},
        ])
        _, state = signal_state.diff_signals(
            before, {}, fingerprint_fields=FIELDS, scope="option:double"
        )
        after = before.copy()
        after.loc[0, "ma_cross_time"] = "2026-07-14T12:00:00"
        after.loc[1, "double_confirmed"] = True
        after.loc[1, "macd_direction_confirmed"] = True

        alerts, _ = signal_state.diff_signals(
            after, state, fingerprint_fields=FIELDS, scope="option:double"
        )

        self.assertEqual(alerts.set_index("code")["alert_type"].to_dict(), {
            "A": "新金叉", "B": "确认变化",
        })

    def test_emits_expired_when_a_previous_signal_disappears(self):
        current = pd.DataFrame([{
            "code": "A", "ma_cross_time": "2026-07-14T10:00:00",
            "macd_cross_time": None, "double_confirmed": True,
            "ma_direction_confirmed": True, "macd_direction_confirmed": False,
        }])
        _, state = signal_state.diff_signals(
            current, {}, fingerprint_fields=FIELDS, scope="option:double"
        )

        alerts, new_state = signal_state.diff_signals(
            current.iloc[0:0], state, fingerprint_fields=FIELDS,
            scope="option:double",
        )

        self.assertEqual(alerts[["code", "alert_type"]].to_dict("records"), [
            {"code": "A", "alert_type": "信号失效"}
        ])
        self.assertEqual(new_state["signals"], {})

    def test_saves_and_loads_state_json(self):
        state = {
            "version": 1, "scope": "option:double",
            "signals": {"A": {"fingerprint": {"double_confirmed": True}}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "state.json"

            signal_state.save_state(path, state)
            loaded = signal_state.load_state(path)

            self.assertEqual(loaded, state)
            self.assertFalse((path.parent / (path.name + ".tmp")).exists())

    def test_normalizes_pandas_nat_before_json_persistence(self):
        current = pd.DataFrame([{
            "code": "A", "ma_cross_time": pd.NaT,
            "macd_cross_time": pd.NaT, "double_confirmed": True,
            "ma_direction_confirmed": True, "macd_direction_confirmed": False,
        }])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            _, state = signal_state.diff_signals(
                current, {}, fingerprint_fields=FIELDS, scope="option:double"
            )

            signal_state.save_state(path, state)

            loaded = signal_state.load_state(path)
            self.assertIsNone(
                loaded["signals"]["A"]["fingerprint"]["ma_cross_time"]
            )


if __name__ == "__main__":
    unittest.main()
