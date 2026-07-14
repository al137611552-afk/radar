"""
行情接口客户端 (quote_api)
-------------------------------------------------
对接 173uu 量化数据服务，负责"从数据源拿K线"这一件事。
上层的指标计算、形态识别都基于这里返回的数据。

接口要点（实测确认）：
  - 自签名证书 -> 必须 verify=False
  - Bearer Token 认证
  - K线接口用 POST，周期用数字代码，秒级时间戳
  - 返回 {"code":0, "data":[...]}；单品种时 data 直接是K线数组
"""

import os
import urllib3
import requests
import pandas as pd

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://bj.173uu.com:8680"


class QuoteClient:
    """Quote API client whose credential is supplied at runtime."""

    def __init__(self, api_key=None, base_url=BASE_URL, session=None):
        self.api_key = api_key or os.getenv("QUOTE_API_KEY")
        if not self.api_key:
            raise ValueError("QUOTE_API_KEY is required")
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, path, payload):
        response = self.session.post(
            f"{self.base_url}{path}", json=payload, headers=self._headers(),
            verify=False, timeout=60,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0:
            raise RuntimeError(f"接口返回错误: {body}")
        return body["data"]

    def search(self, keyword=None, exchange_code=None, category_type=None):
        payload = {}
        if exchange_code is not None:
            payload["exchange_code"] = exchange_code
        if category_type is not None:
            payload["category_type"] = category_type
        if keyword is not None:
            payload["keyword"] = keyword
        return self._post("/api/v1/varieties/search", payload)

    def main_contracts(self, as_of=None):
        """Return current main contracts, falling back to historical mapping."""
        try:
            response = self.session.get(
                f"{self.base_url}/api/v1/varieties/main",
                headers=self._headers(), verify=False, timeout=30,
            )
            response.raise_for_status()
            body = response.json()
            if body.get("code") != 0:
                raise RuntimeError(f"接口返回错误: {body}")
            return body["data"]
        except (requests.RequestException, RuntimeError, ValueError, KeyError):
            day = as_of or pd.Timestamp.now(tz="Asia/Shanghai").date().isoformat()
            rows = self._post("/api/v1/varieties/main-by-date", {
                "start_time": str(day), "end_time": str(day),
            })
            return {
                row["main_variety_code"].removesuffix("9999"): row["variety_code"]
                for row in rows
            }

    def get_kline_by_count(self, variety_code, interval="day", count=100):
        data = self._post("/api/v1/kline/by-count", {
            "variety_code": variety_code,
            "interval_range": _to_interval(interval),
            "count": count,
        })
        return _to_df(data)

    def get_klines_by_count(self, variety_codes, interval="day", count=100):
        codes = list(variety_codes)
        data = self._post("/api/v1/kline/by-count", {
            "variety_codes": codes,
            "interval_range": _to_interval(interval),
            "count": count,
        })
        return {row["code"]: _to_df(row.get("klines", [])) for row in data}

    def get_kline_by_timerange(self, variety_code, interval, start_time,
                               end_time, adjust_type=0):
        data = self._post("/api/v1/kline/by-timerange", {
            "variety_code": variety_code,
            "interval_range": _to_interval(interval),
            "start_time": int(start_time),
            "end_time": int(end_time),
            "adjust_type": adjust_type,
        })
        return _to_df(data)

# 周期名 -> 接口的数字代码 (interval_range)
INTERVAL_CODE = {
    "1m": 1, "5m": 5, "10m": 10, "15m": 15, "30m": 30,
    "1h": 60, "day": 101, "week": 102, "month": 103,
}


def _to_interval(interval):
    if interval in INTERVAL_CODE:
        return INTERVAL_CODE[interval]
    if interval in INTERVAL_CODE.values():
        return interval
    raise ValueError(f"未知周期: {interval}，可选 {list(INTERVAL_CODE)}")


def search(keyword=None, exchange_code=None, category_type=None):
    """搜索品种。"""
    return QuoteClient().search(keyword, exchange_code, category_type)


def main_contracts():
    """当前各品类主力合约映射，如 {'rb':'rb2509'}。"""
    return QuoteClient().main_contracts()


def get_kline_by_count(variety_code, interval="day", count=100):
    """按根数获取最近 N 根K线（最多2000，不支持复权）。返回 DataFrame。"""
    return QuoteClient().get_kline_by_count(variety_code, interval, count)


def get_kline_by_timerange(variety_code, interval, start_time, end_time,
                           adjust_type=0):
    """按时间范围获取K线（无数量限制，支持复权）。返回 DataFrame。"""
    return QuoteClient().get_kline_by_timerange(
        variety_code, interval, start_time, end_time, adjust_type
    )


def _to_df(klines):
    """把K线数组转成带时间索引的 pandas DataFrame，方便后续算指标。"""
    if not klines:
        return pd.DataFrame()
    df = pd.DataFrame(klines)
    df["datetime"] = pd.to_datetime(df["time_stamp"], unit="s", utc=True) \
        .dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    cols = ["datetime", "open", "high", "low", "close",
            "volume", "money", "open_interest"]
    df = df[[c for c in cols if c in df.columns]]
    return df.sort_values("datetime").reset_index(drop=True)


if __name__ == "__main__":
    print(">>> 1) 当前白银主力合约:")
    mains = main_contracts()
    print("    ag 主力 =", mains.get("ag"))

    print("\n>>> 2) ag8888 最近 10 根日线:")
    df = get_kline_by_count("ag8888", interval="day", count=10)
    print(df.to_string(index=False))

    print(f"\n>>> 3) 拉到 {len(df)} 根；最新一根收盘价 = {df['close'].iloc[-1]}")
