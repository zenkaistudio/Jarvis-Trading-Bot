"""
Order execution for Jarvis.
Operates on demo or live based on jarvis_config.json trade_mode.
Auto-trading is OFF by default — must be explicitly enabled.
"""

import json
import time
from datetime import datetime
from pathlib import Path

import requests

JARVIS_CONFIG_PATH = Path(__file__).parent / "jarvis_config.json"

# Demo instrument IDs (fetched 2026-05-21)
DEMO_INSTRUMENTS = {
    "EURUSD": {"tradableInstrumentId": 4665, "route_trade": 898485, "route_info": 898479},
    "AUDCAD": {"tradableInstrumentId": 4683, "route_trade": 898485, "route_info": 898479},
    "NZDJPY": {"tradableInstrumentId": 4684, "route_trade": 898485, "route_info": 898479},
    "GBPJPY": {"tradableInstrumentId": 4686, "route_trade": 898485, "route_info": 898479},
    "NAS100": {"tradableInstrumentId": 4691, "route_trade": 898485, "route_info": 898479},
    "XAUUSD": {"tradableInstrumentId": 4709, "route_trade": 898485, "route_info": 898479},
}

# Live instrument IDs (from tradelocker_config.json)
LIVE_INSTRUMENTS = {
    "EURUSD": {"tradableInstrumentId": 3470, "route_trade": 509994, "route_info": 509043},
    "GBPJPY": {"tradableInstrumentId": 3474, "route_trade": 509994, "route_info": 509043},
    "XAUUSD": {"tradableInstrumentId": 3366, "route_trade": 509994, "route_info": 509043},
    "NAS100": {"tradableInstrumentId": 3373, "route_trade": 509994, "route_info": 509043},
    "NZDJPY": {"tradableInstrumentId": 3483, "route_trade": 509994, "route_info": 509043},
    "AUDCAD": {"tradableInstrumentId": 3510, "route_trade": 509994, "route_info": 509043},
}

# Approximate pip sizes per symbol
PIP_SIZE = {
    "GBPJPY": 0.01, "NZDJPY": 0.01,
    "EURUSD": 0.0001, "AUDCAD": 0.0001,
    "XAUUSD": 0.1, "NAS100": 1.0,
}

# Approximate USD pip value per 1.0 lot (for position sizing)
PIP_VALUE_USD = {
    "GBPJPY": 8.5,
    "NZDJPY": 7.5,
    "EURUSD": 10.0,
    "AUDCAD": 10.0,
    "XAUUSD": 10.0,
    "NAS100": 1.0,
}


class OrderExecutor:
    def __init__(self):
        self._access_token = None
        self._token_expiry = 0

    def _load_config(self) -> dict:
        with open(JARVIS_CONFIG_PATH) as f:
            return json.load(f)

    def is_auto_trade_enabled(self) -> bool:
        return self._load_config().get("auto_trade", False)

    def get_trade_mode(self) -> str:
        return self._load_config().get("trade_mode", "demo")

    def _mode_config(self) -> dict:
        config = self._load_config()
        mode = config.get("trade_mode", "demo")
        return config.get(mode, config.get("demo"))

    def _instruments(self) -> dict:
        return DEMO_INSTRUMENTS if self.get_trade_mode() == "demo" else LIVE_INSTRUMENTS

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _authenticate(self) -> str:
        mc = self._mode_config()
        tl_config_path = Path(__file__).parent.parent / "tradelocker_config.json"
        with open(tl_config_path) as f:
            tl = json.load(f)

        resp = requests.post(
            f"{mc['base_url']}/auth/jwt/token",
            json={"email": tl["email"], "password": tl["password"], "server": mc["server"]},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["accessToken"]
        expire_raw = data.get("expireDate", "")
        try:
            d = datetime.fromisoformat(expire_raw.replace("Z", "+00:00"))
            self._token_expiry = d.timestamp()
        except Exception:
            self._token_expiry = time.time() + 3600
        return self._access_token

    def _headers(self) -> dict:
        if not self._access_token or time.time() > self._token_expiry - 60:
            self._authenticate()
        mc = self._mode_config()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "accNum": str(mc["acc_num"]),
        }

    def _base_url(self) -> str:
        return self._mode_config()["base_url"]

    def _account_id(self) -> str:
        return self._mode_config()["account_id"]

    # ── Position Sizing ───────────────────────────────────────────────────────

    def calculate_lot_size(self, symbol: str, entry: float, sl: float, balance: float, risk_pct: float = 2.0) -> float:
        """
        Calculates lot size using: risk_amount / (sl_pips * pip_value_per_lot)
        Capped at 10 lots max, minimum 0.01.
        """
        pip = PIP_SIZE.get(symbol, 0.0001)
        pip_val = PIP_VALUE_USD.get(symbol, 10.0)
        sl_pips = abs(entry - sl) / pip
        if sl_pips == 0:
            return 0.01
        risk_amount = balance * (risk_pct / 100)
        lots = risk_amount / (sl_pips * pip_val)
        lots = max(0.01, min(round(lots, 2), 10.0))
        return lots

    def get_account_balance(self) -> float:
        resp = requests.get(
            f"{self._base_url()}/auth/jwt/all-accounts",
            headers=self._headers(),
            timeout=15,
        )
        resp.raise_for_status()
        accounts = resp.json().get("accounts", [])
        acc_id = self._account_id()
        for acc in accounts:
            if str(acc.get("id")) == str(acc_id):
                return float(acc.get("accountBalance", 0))
        return float(accounts[0].get("accountBalance", 0)) if accounts else 0.0

    # ── Order Placement ───────────────────────────────────────────────────────

    def place_order(self, symbol: str, direction: str, entry: float, sl: float, tp: float = None) -> dict:
        """
        Places a limit order at entry price with SL/TP.
        direction: 'LONG' or 'SHORT'
        Returns result dict with order details or error.
        """
        if not self.is_auto_trade_enabled():
            return {"success": False, "error": "Auto-trading is OFF. Say 'auto on' to enable."}

        instrument = self._instruments().get(symbol)
        if not instrument:
            return {"success": False, "error": f"Symbol {symbol} not in instrument list."}

        config = self._load_config()
        risk_pct = config.get("risk_percent", 2.0)
        balance = self.get_account_balance()
        lots = self.calculate_lot_size(symbol, entry, sl, balance, risk_pct)
        side = "Buy" if direction == "LONG" else "Sell"

        body = {
            "tradableInstrumentId": instrument["tradableInstrumentId"],
            "routeId": instrument["route_trade"],
            "type": "Limit",
            "validity": "GTC",
            "side": side,
            "qty": lots,
            "price": entry,
            "stopLoss": sl,
        }
        if tp:
            body["takeProfit"] = tp

        resp = requests.post(
            f"{self._base_url()}/trade/accounts/{self._account_id()}/orders",
            headers=self._headers(),
            json=body,
            timeout=15,
        )

        mode = self.get_trade_mode().upper()
        if resp.status_code in (200, 201):
            data = resp.json()
            if data.get("s") == "error":
                return {"success": False, "error": data.get("errmsg", "Unknown error")}
            return {
                "success": True,
                "mode": mode,
                "symbol": symbol,
                "direction": direction,
                "lots": lots,
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "balance_used": round(balance * risk_pct / 100, 2),
                "risk_pct": risk_pct,
                "response": data,
            }
        return {"success": False, "error": f"{resp.status_code}: {resp.text[:200]}"}

    def get_open_positions(self) -> list:
        endpoints = [
            f"/trade/accounts/{self._account_id()}/positions",
            f"/trade/accounts/{self._account_id()}/orders",
        ]
        for ep in endpoints:
            try:
                resp = requests.get(f"{self._base_url()}{ep}", headers=self._headers(), timeout=15)
                if resp.status_code == 200:
                    d = resp.json().get("d", {})
                    return d.get("positions", d.get("orders", []))
            except Exception:
                continue
        return []

    def format_order_result(self, result: dict) -> str:
        if not result["success"]:
            return f"❌ *Order Failed*\n\n{result['error']}"
        tp_line = f"Take Profit: `{result['tp']}`\n" if result.get("tp") else ""
        return (
            f"✅ *Order Placed — {result['mode']} ACCOUNT*\n\n"
            f"Symbol: `{result['symbol']}`\n"
            f"Direction: `{result['direction']}`\n"
            f"Lots: `{result['lots']}`\n"
            f"Entry: `{result['entry']}`\n"
            f"Stop Loss: `{result['sl']}`\n"
            f"{tp_line}"
            f"Risk: `{result['risk_pct']}%` = `${result['balance_used']}`\n\n"
            f"_Limit order set. Fills when price reaches entry._"
        )
