import json
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

CONFIG_PATH = Path(__file__).parent.parent / "tradelocker_config.json"


class TradeLockerClient:
    def __init__(self):
        with open(CONFIG_PATH) as f:
            self.config = json.load(f)
        self.base_url = self.config["base_url"]
        self.watchlist = self.config["watchlist"]
        self.account_id = self.config["account_id"]
        self._access_token = None
        self._token_expiry = 0

    # ── Auth ──────────────────────────────────────────────────────────────────

    def authenticate(self) -> str:
        resp = requests.post(
            f"{self.base_url}{self.config['auth_endpoint']}",
            json={
                "email": self.config["email"],
                "password": self.config["password"],
                "server": self.config["server"],
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["accessToken"]
        expire_raw = data.get("expireDate", "")
        try:
            # expireDate is ISO 8601: '2026-05-21T03:39:04.000Z'
            dt = datetime.fromisoformat(expire_raw.replace("Z", "+00:00"))
            self._token_expiry = dt.timestamp()
        except (ValueError, AttributeError, TypeError):
            self._token_expiry = time.time() + 3600
        return self._access_token

    def _headers(self) -> dict:
        if not self._access_token or time.time() > self._token_expiry - 60:
            self.authenticate()
        return {"Authorization": f"Bearer {self._access_token}"}

    # ── Market Data ───────────────────────────────────────────────────────────

    _RESOLUTION_MINUTES = {
        "1m": 1, "5m": 5, "15m": 15, "30m": 30,
        "1H": 60, "4H": 240, "1D": 1440, "1W": 10080, "1M": 43200,
    }

    def get_candles(self, symbol: str, resolution: str = "1H", bars: int = 200) -> pd.DataFrame:
        """Return OHLCV DataFrame for symbol/resolution. Applies 700ms rate-limit delay."""
        if symbol not in self.watchlist:
            raise ValueError(f"{symbol} not in watchlist. Options: {list(self.watchlist)}")

        instrument = self.watchlist[symbol]
        mins = self._RESOLUTION_MINUTES.get(resolution, 60)
        to_ts = int(time.time() * 1000)
        from_ts = to_ts - (bars * mins * 60 * 1000 * 2)  # 2x buffer for weekends/gaps

        time.sleep(0.7)

        resp = requests.get(
            f"{self.base_url}{self.config['history_endpoint']}",
            headers={
                **self._headers(),
                "accNum": str(self.config.get("acc_num", "")),
            },
            params={
                "tradableInstrumentId": instrument["tradableInstrumentId"],
                "routeId": self.config["route_id_info"],
                "resolution": resolution,
                "from": from_ts,
                "to": to_ts,
            },
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        d = body.get("d") if isinstance(body, dict) else None
        if not d:
            raise ValueError(f"No data in response for {symbol} {resolution}: {body}")

        # API returns barDetails as list of objects: [{t, o, h, l, c, v}, ...]
        bar_list = d.get("barDetails") or d.get("bars")
        if bar_list:
            df = pd.DataFrame(bar_list)
            df.rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}, inplace=True)
        elif d.get("t"):
            # Fallback: parallel arrays format
            df = pd.DataFrame({"time": d["t"], "open": d["o"], "high": d["h"], "low": d["l"], "close": d["c"], "volume": d["v"]})
        else:
            raise ValueError(f"Unrecognised candle format for {symbol} {resolution}: {list(d.keys())}")

        df["time"] = pd.to_datetime(df["time"], unit="ms")
        return df.tail(bars).reset_index(drop=True)

    def get_tick(self, symbol: str) -> dict:
        """Return latest candle close as current price approximation."""
        df = self.get_candles(symbol, "1H", bars=2)
        latest = df.iloc[-1]
        return {
            "symbol": symbol,
            "price": round(latest["close"], 5),
            "time": str(latest["time"]),
        }

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account_info(self) -> dict:
        resp = requests.get(
            f"{self.base_url}{self.config['accounts_endpoint']}",
            headers=self._headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        # all-accounts returns a list — find the one matching our account
        accounts = data.get("d", {}).get("accounts", data) if isinstance(data, dict) else data
        if isinstance(accounts, list):
            for acc in accounts:
                if str(acc.get("id", "")) == str(self.account_id) or str(acc.get("accNum", "")) == str(self.config.get("acc_num", "")):
                    return acc
            return accounts[0] if accounts else data
        return data

    def get_positions(self) -> list:
        # Try the most likely endpoints for open positions
        endpoints = [
            f"/trade/accounts/{self.account_id}/positions",
            f"/trade/accounts/{self.account_id}/orders",
        ]
        for endpoint in endpoints:
            try:
                resp = requests.get(
                    f"{self.base_url}{endpoint}",
                    headers=self._headers(),
                    timeout=15,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        d = data.get("d", {})
                        return d.get("positions", d.get("orders", list(d.values())[0] if d else []))
                    return data if isinstance(data, list) else []
            except Exception:
                continue
        return []
