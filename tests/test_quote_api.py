import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import quote_api


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise quote_api.requests.HTTPError(str(self.status_code))

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)


class QuoteClientConfigurationTests(unittest.TestCase):
    def test_client_requires_api_key(self):
        old = os.environ.pop("QUOTE_API_KEY", None)
        try:
            with self.assertRaisesRegex(ValueError, "QUOTE_API_KEY"):
                quote_api.QuoteClient()
        finally:
            if old is not None:
                os.environ["QUOTE_API_KEY"] = old

    def test_search_uses_injected_credential_and_filters(self):
        session = FakeSession([FakeResponse({"code": 0, "data": [{"code": "au6666"}]})])
        client = quote_api.QuoteClient(api_key="secret", session=session)

        result = client.search(exchange_code="SHFE", category_type=1, keyword="黄金")

        self.assertEqual(result, [{"code": "au6666"}])
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("POST", f"{quote_api.BASE_URL}/api/v1/varieties/search"))
        self.assertEqual(kwargs["json"], {
            "exchange_code": "SHFE", "category_type": 1, "keyword": "黄金"
        })
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret")

    def test_main_contracts_falls_back_to_main_by_date(self):
        session = FakeSession([
            FakeResponse({"code": 500, "message": "upstream failed"}, status_code=500),
            FakeResponse({"code": 0, "data": [
                {"main_variety_code": "au9999", "variety_code": "au2608"},
                {"main_variety_code": "rb9999", "variety_code": "rb2610"},
            ]}),
        ])
        client = quote_api.QuoteClient(api_key="secret", session=session)

        result = client.main_contracts(as_of="2026-07-14")

        self.assertEqual(result, {"au": "au2608", "rb": "rb2610"})
        self.assertEqual([call[0] for call in session.calls], ["GET", "POST"])
        self.assertEqual(session.calls[1][2]["json"], {
            "start_time": "2026-07-14", "end_time": "2026-07-14"
        })

    def test_get_kline_by_count_returns_sorted_shanghai_dataframe(self):
        session = FakeSession([FakeResponse({"code": 0, "data": [
            {"time_stamp": 3600, "close": 2, "open": 2, "high": 2,
             "low": 2, "volume": 1},
            {"time_stamp": 0, "close": 1, "open": 1, "high": 1,
             "low": 1, "volume": 1},
        ]})])
        client = quote_api.QuoteClient(api_key="secret", session=session)

        frame = client.get_kline_by_count("au6666", interval="day", count=2)

        self.assertEqual(frame["close"].tolist(), [1, 2])
        self.assertEqual(str(frame["datetime"].iloc[0]), "1970-01-01 08:00:00")
        self.assertEqual(session.calls[0][2]["json"], {
            "variety_code": "au6666", "interval_range": 101, "count": 2
        })

    def test_get_klines_by_count_returns_frames_by_code(self):
        session = FakeSession([FakeResponse({"code": 0, "data": [
            {"code": "au6666", "klines": [{"time_stamp": 0, "close": 100}]},
            {"code": "rb6666", "klines": [{"time_stamp": 0, "close": 200}]},
        ]})])
        client = quote_api.QuoteClient(api_key="secret", session=session)

        frames = client.get_klines_by_count(["au6666", "rb6666"], count=6)

        self.assertEqual(frames["au6666"]["close"].tolist(), [100])
        self.assertEqual(frames["rb6666"]["close"].tolist(), [200])
        self.assertEqual(session.calls[0][2]["json"], {
            "variety_codes": ["au6666", "rb6666"],
            "interval_range": 101,
            "count": 6,
        })


if __name__ == "__main__":
    unittest.main()
