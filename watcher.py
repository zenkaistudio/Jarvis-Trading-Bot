"""
Dynamic setup watcher.
After any scan that finds a valid setup, key price levels are stored here.
A lightweight price-check loop fires Telegram alerts when levels are hit.
"""

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WatchedSetup:
    id: str                        # e.g. "GBPJPY_LONG_212.616"
    symbol: str
    direction: str                 # LONG | SHORT
    setup_type: str                # smc | gbpjpy
    entry_top: float
    entry_bottom: float
    sl: float
    tp: Optional[float]
    neckline: Optional[float]      # IH&S neckline level (GBPJPY)
    added_at: float = field(default_factory=time.time)
    status: str = "waiting"        # waiting | entered | missed | invalidated | tp_hit
    alerts_fired: set = field(default_factory=set)

    # ── Price checks ─────────────────────────────────────────────────────────

    def in_entry_zone(self, price: float) -> bool:
        return self.entry_bottom <= price <= self.entry_top

    def sl_breached(self, price: float) -> bool:
        return price < self.sl if self.direction == "LONG" else price > self.sl

    def tp_approaching(self, price: float) -> bool:
        if not self.tp:
            return False
        proximity = 0.003  # within 0.3%
        return price >= self.tp * (1 - proximity) if self.direction == "LONG" else price <= self.tp * (1 + proximity)

    def price_ran_through(self, price: float) -> bool:
        """Price blew past the zone without the user entering."""
        if self.status != "waiting":
            return False
        return price > self.entry_top * 1.002 if self.direction == "LONG" else price < self.entry_bottom * 0.998

    def neckline_broken(self, price: float) -> bool:
        if not self.neckline:
            return False
        return price > self.neckline if self.direction == "LONG" else price < self.neckline

    def is_active(self) -> bool:
        return self.status not in ("invalidated", "tp_hit")

    def summary(self) -> str:
        zone = f"{self.entry_bottom}–{self.entry_top}"
        neck = f" | Neckline: `{self.neckline}`" if self.neckline else ""
        return (
            f"*{self.symbol}* {self.direction} | Zone: `{zone}` | "
            f"SL: `{self.sl}`{f' | TP: `{self.tp}`' if self.tp else ''}{neck} | "
            f"Status: `{self.status}`"
        )


# ── Alert formatters ──────────────────────────────────────────────────────────

def fmt_entry_zone(setup: WatchedSetup, price: float) -> str:
    action = "BUY" if setup.direction == "LONG" else "SELL"
    lot_note = "Largest lots at zone bottom." if setup.direction == "LONG" else "Largest lots at zone top."
    return (
        f"🟡 *JARVIS — {setup.symbol} ENTRY ZONE REACHED*\n\n"
        f"Price is inside your {setup.direction} zone.\n"
        f"Current: `{price}`\n"
        f"Zone: `{setup.entry_bottom} — {setup.entry_top}`\n"
        f"Stop Loss: `{setup.sl}`\n"
        f"{f'Take Profit: `{setup.tp}`' + chr(10) if setup.tp else ''}"
        f"\n_{action} signal active. {lot_note}_"
    )


def fmt_invalidation(setup: WatchedSetup, price: float) -> str:
    return (
        f"🔴 *JARVIS — {setup.symbol} SETUP INVALIDATED*\n\n"
        f"Price breached the stop loss level.\n"
        f"Current: `{price}`\n"
        f"SL Level: `{setup.sl}`\n\n"
        f"_Setup removed from watchlist._"
    )


def fmt_tp_approaching(setup: WatchedSetup, price: float) -> str:
    return (
        f"🟢 *JARVIS — {setup.symbol} TP APPROACHING*\n\n"
        f"Price nearing your target.\n"
        f"Current: `{price}`\n"
        f"Take Profit: `{setup.tp}`\n\n"
        f"_Consider scaling out or moving stop to breakeven._"
    )


def fmt_missed_entry(setup: WatchedSetup, price: float) -> str:
    reentry = ""
    if setup.neckline:
        reentry = (
            f"\n*Re-entry Protocol (KJ Strategy):*\n"
            f"Wait for neckline break → retest `{setup.neckline}` as support\n"
            f"Enter smaller lot on the retest."
        )
    else:
        reentry = (
            f"\n*Re-entry Protocol (SMC):*\n"
            f"Wait for price to return to zone or find new FVG.\n"
            f"Do not chase."
        )
    return (
        f"🟠 *JARVIS — {setup.symbol} ENTRY MISSED*\n\n"
        f"Price ran through the entry zone.\n"
        f"Current: `{price}`\n"
        f"Zone was: `{setup.entry_bottom} — {setup.entry_top}`\n"
        f"{reentry}"
    )


def fmt_neckline_break(setup: WatchedSetup, price: float) -> str:
    return (
        f"🚀 *JARVIS — {setup.symbol} NECKLINE BROKEN*\n\n"
        f"IH&S neckline broken — bullish confirmation.\n"
        f"Current: `{price}`\n"
        f"Neckline: `{setup.neckline}`\n\n"
        f"_Trail stop to breakeven. Watch for pullback to `{setup.neckline}` as new support._"
    )


# ── Watcher ───────────────────────────────────────────────────────────────────

class SetupWatcher:
    def __init__(self):
        self._setups: dict[str, WatchedSetup] = {}

    def add(self, setup: WatchedSetup):
        self._setups[setup.id] = setup
        print(f"[Jarvis] Watching: {setup.id}")

    def remove(self, setup_id: str):
        self._setups.pop(setup_id, None)

    def active_setups(self) -> list[WatchedSetup]:
        return [s for s in self._setups.values() if s.is_active()]

    def all_setups(self) -> list[WatchedSetup]:
        return list(self._setups.values())

    def check_all(self, client, executor=None) -> list[tuple[WatchedSetup, str]]:
        """
        Fetch current price for each watched symbol, check all level conditions.
        If executor is provided and auto_trade is ON, places order when entry zone hit.
        Returns list of (setup, alert_message) pairs to send.
        """
        alerts = []
        symbols_checked = {}

        for setup in self.active_setups():
            if setup.symbol not in symbols_checked:
                try:
                    tick = client.get_tick(setup.symbol)
                    symbols_checked[setup.symbol] = tick["price"]
                except Exception as e:
                    print(f"[Jarvis] Price fetch failed for {setup.symbol}: {e}")
                    continue

            price = symbols_checked[setup.symbol]
            fired = self._check_setup(setup, price)

            for msg in fired:
                alerts.append((setup, msg))
                # Auto-execute when entry zone is hit
                if executor and "ENTRY ZONE REACHED" in msg and executor.is_auto_trade_enabled():
                    try:
                        result = executor.place_order(
                            symbol=setup.symbol,
                            direction=setup.direction,
                            entry=setup.entry_bottom if setup.direction == "LONG" else setup.entry_top,
                            sl=setup.sl,
                            tp=setup.tp,
                        )
                        order_msg = executor.format_order_result(result)
                        alerts.append((setup, order_msg))
                    except Exception as e:
                        alerts.append((setup, f"⚠️ Auto-trade failed: {e}"))

        dead = [sid for sid, s in self._setups.items() if not s.is_active()]
        for sid in dead:
            del self._setups[sid]
            print(f"[Jarvis] Removed inactive setup: {sid}")

        return alerts

    def _check_setup(self, setup: WatchedSetup, price: float) -> list[str]:
        msgs = []

        # SL breach — highest priority, mark invalidated
        if setup.sl_breached(price) and "sl" not in setup.alerts_fired:
            setup.status = "invalidated"
            setup.alerts_fired.add("sl")
            msgs.append(fmt_invalidation(setup, price))

        # Entry zone reached
        elif setup.in_entry_zone(price) and "entry" not in setup.alerts_fired:
            setup.status = "entered"
            setup.alerts_fired.add("entry")
            msgs.append(fmt_entry_zone(setup, price))

        # Price ran through zone (missed entry)
        elif setup.price_ran_through(price) and "missed" not in setup.alerts_fired:
            setup.status = "missed"
            setup.alerts_fired.add("missed")
            msgs.append(fmt_missed_entry(setup, price))

        # TP approaching (can fire alongside other alerts)
        if setup.tp_approaching(price) and "tp" not in setup.alerts_fired:
            setup.status = "tp_hit"
            setup.alerts_fired.add("tp")
            msgs.append(fmt_tp_approaching(setup, price))

        # Neckline broken (GBPJPY IH&S)
        if setup.neckline_broken(price) and "neckline" not in setup.alerts_fired:
            setup.alerts_fired.add("neckline")
            msgs.append(fmt_neckline_break(setup, price))

        return msgs

    def watchlist_summary(self) -> str:
        active = self.active_setups()
        if not active:
            return "📭 No setups currently being watched."
        lines = [f"👁 *Jarvis Watchlist* ({len(active)} active)\n"]
        for s in active:
            lines.append(s.summary())
        return "\n".join(lines)


# ── Helpers to build WatchedSetup from scan results ──────────────────────────

def setup_from_smc(scan_result: dict, smc_setup: dict) -> WatchedSetup:
    symbol = scan_result["symbol"]
    direction = smc_setup["direction"]
    entry = smc_setup["entry"]
    sl = smc_setup["sl"]
    tp = smc_setup.get("tp")

    # Entry zone = ±0.2% around entry price
    spread = entry * 0.002
    entry_bottom = round(entry - spread, 5) if direction == "LONG" else round(entry, 5)
    entry_top = round(entry, 5) if direction == "LONG" else round(entry + spread, 5)

    return WatchedSetup(
        id=f"{symbol}_{direction}_{entry}",
        symbol=symbol,
        direction=direction,
        setup_type="smc",
        entry_top=entry_top,
        entry_bottom=entry_bottom,
        sl=sl,
        tp=tp,
        neckline=None,
    )


def setup_from_gbpjpy(confluence_result: dict) -> Optional[WatchedSetup]:
    if not confluence_result.get("setup_valid") in (True, "True"):
        return None

    entry_info = confluence_result.get("1h_entry", {})
    reversal = confluence_result.get("4h_reversal", {})

    entry_zone_str = entry_info.get("entry_zone", "")
    sl = entry_info.get("sl")

    try:
        parts = entry_zone_str.replace(" ", "").split("—")
        entry_bottom = float(parts[0])
        entry_top = float(parts[1])
    except Exception:
        return None

    neckline = reversal.get("neckline") if reversal.get("pattern") == "Inverse Head & Shoulders" else None

    return WatchedSetup(
        id=f"GBPJPY_LONG_{entry_bottom}",
        symbol="GBPJPY",
        direction="LONG",
        setup_type="gbpjpy",
        entry_top=float(entry_top),
        entry_bottom=float(entry_bottom),
        sl=float(sl) if sl else entry_bottom * 0.999,
        tp=None,
        neckline=float(neckline) if neckline else None,
    )
