import json
import threading
import time
from pathlib import Path

import requests

from notifier import send_telegram, format_smc_alert, format_gbpjpy_alert
from strategies.smc import run_smc_scan, run_range_scan
from strategies.gbpjpy import run_gbpjpy_confluence_check, run_kj_confluence_check
from watcher import SetupWatcher, setup_from_smc, setup_from_gbpjpy, setup_from_kj
from order_executor import OrderExecutor

JARVIS_CONFIG_PATH = Path(__file__).parent / "jarvis_config.json"

WATCHLIST = ["GBPJPY", "EURUSD", "XAUUSD", "NAS100", "NZDJPY", "AUDCAD"]

SYMBOL_ALIASES = {
    "nas100": "NAS100", "nas 100": "NAS100", "nasdaq": "NAS100", "nas": "NAS100",
    "gold": "XAUUSD", "xau": "XAUUSD", "xauusd": "XAUUSD",
    "gbpjpy": "GBPJPY", "gj": "GBPJPY", "guppy": "GBPJPY",
    "eurusd": "EURUSD", "euro": "EURUSD", "eu": "EURUSD",
    "nzdjpy": "NZDJPY", "nj": "NZDJPY",
    "audcad": "AUDCAD", "ac": "AUDCAD",
}

def _parse_symbol(text: str):
    text_lower = text.lower()
    for alias, symbol in SYMBOL_ALIASES.items():
        if alias in text_lower:
            return symbol
    for sym in WATCHLIST:
        if sym.lower() in text_lower:
            return sym
    return None


HELP_TEXT = """
🤖 *Jarvis — Command List*

*Overview*
`summary` — balance, trades & watched levels at a glance

*Account*
`balance` — account balance
`positions` — open trades
`pnl` — floating profit/loss on open trades

*Prices & Bias*
`price GBPJPY` — live price for any symbol
`bias GBPJPY` — quick directional bias (structure + VWAP)

*Analysis*
`scan GBPJPY` — SMC scan any pair + auto-watch levels
`gbpjpy` or `kj` — full KJ top-down confluence (GBPJPY only)
`scalp EURUSD` — scalp levels, quality rating + nearest zones for any pair
`range GBPJPY` — range-bound setups: buy support, sell resistance
`scan all` — scan entire watchlist (SMC)
`rate all` — SMC star rating for all pairs (KJ shown for GBPJPY)
`rescan` — force immediate rescan right now

*Watchlist*
`watching` — see all levels Jarvis is tracking
`clear GBPJPY` — remove a symbol's watched setups
`clear all` — clear all watched setups
`add EURUSD` — add symbol to scan watchlist
`remove EURUSD` — remove symbol from scan watchlist

*Manual Orders*
`buy GBPJPY 213.50` — place pending long at that price
`sell NAS100 29300` — place pending short at that price
`buy GBPJPY 213.50 sl 212.80` — with custom stop loss

*Auto-Trading*
`auto on` — enable auto order placement
`auto off` — disable (alerts only)
`risk 2` — set risk % per trade (0.5–5%)
`mode demo` — trade on demo account
`mode live` — trade on live account

*Entry Timeframe*
`tf 5m` — use 5-minute candles for entries
`tf 15m` — use 15-minute candles for entries
`tf 1h` — use 1-hour candles for entries

*Monitoring*
`start` — start background alerts
`stop` — stop background alerts
`status` — monitoring status

`help` — show this list
""".strip()


class JarvisMonitor:
    def __init__(self, client):
        self.client = client
        self.watcher = SetupWatcher()
        self.executor = OrderExecutor()
        self._scan_thread = None
        self._price_thread = None
        self._chat_thread = None
        self._running = False
        self._alerted_setups = set()
        self._alerted_gbpjpy_score = 0
        self._last_update_id = 0

    def _load_config(self) -> dict:
        with open(JARVIS_CONFIG_PATH) as f:
            return json.load(f)

    def _load_and_set(self, key: str, value) -> dict:
        config = self._load_config()
        config[key] = value
        with open(JARVIS_CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
        return config

    def _notify(self, message: str):
        config = self._load_config()
        token = config.get("telegram_token", "")
        chat_id = config.get("telegram_chat_id", "")
        if not token or token == "YOUR_BOT_TOKEN_HERE":
            print(f"[Jarvis] {message}")
            return
        send_telegram(token, chat_id, message)

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def start(self, interval_minutes: int = None) -> str:
        if self._running:
            return "Jarvis is already running."
        config = self._load_config()
        interval = interval_minutes or config.get("monitor_interval_minutes", 15)
        self._running = True

        self._scan_thread = threading.Thread(target=self._scan_loop, args=(interval,), daemon=True)
        self._price_thread = threading.Thread(target=self._price_check_loop, daemon=True)
        self._chat_thread = threading.Thread(target=self._chat_loop, daemon=True)

        self._scan_thread.start()
        self._price_thread.start()
        self._chat_thread.start()

        self._notify("🟢 *Jarvis is online.*\nMonitoring + price alerts active. Type `help` for commands.")
        return f"Jarvis is live. Scanning every {interval} min, checking price levels every 5 min."

    def stop(self) -> str:
        self._running = False
        self._alerted_setups.clear()
        self._alerted_gbpjpy_score = 0
        self._notify("🔴 *Jarvis monitoring stopped.*")
        return "Jarvis stopped."

    def status(self) -> dict:
        config = self._load_config()
        return {
            "running": self._running,
            "interval_minutes": config.get("monitor_interval_minutes", 15),
            "telegram_configured": config.get("telegram_token", "") not in ("", "YOUR_BOT_TOKEN_HERE"),
            "watched_setups": len(self.watcher.active_setups()),
            "watchlist": config.get("watchlist", []),
        }

    # ── Price Check Loop (every 5 min — lightweight) ──────────────────────────

    def _price_check_loop(self):
        print("[Jarvis] Price check loop started.")
        while self._running:
            try:
                alerts = self.watcher.check_all(self.client, self.executor)
                for setup, msg in alerts:
                    self._notify(msg)
                    print(f"[Jarvis] Level alert fired: {setup.id}")
            except Exception as e:
                print(f"[Jarvis] Price check error: {e}")
            time.sleep(300)  # 5 minutes

    # ── Full Scan Loop (every N min — heavier) ────────────────────────────────

    def _scan_loop(self, interval_minutes: int):
        print(f"[Jarvis] Scan loop started. Every {interval_minutes} min.")
        while self._running:
            try:
                self._scan_smc_all()
            except Exception as e:
                print(f"[Jarvis] SMC scan error: {e}")
            try:
                self._scan_kj_all()
            except Exception as e:
                print(f"[Jarvis] KJ scan error: {e}")
            time.sleep(interval_minutes * 60)

    def _scan_smc_all(self):
        config = self._load_config()
        entry_tf = config.get("entry_timeframe", "15m")
        for symbol in config.get("watchlist", WATCHLIST):
            try:
                result = run_smc_scan(symbol, self.client, entry_tf=entry_tf)
                try:
                    current_price = self.client.get_tick(symbol)["price"]
                except Exception:
                    current_price = None
                for smc_setup in result.get("valid_setups", []):
                    key = f"{symbol}_{smc_setup['direction']}_{smc_setup['entry']}"
                    if key not in self._alerted_setups:
                        self._alerted_setups.add(key)
                        self._notify(format_smc_alert(symbol, result["structure"], smc_setup))
                        watched = setup_from_smc(result, smc_setup)
                        self.watcher.add(watched, current_price)
            except Exception as e:
                print(f"[Jarvis] Error scanning {symbol}: {e}")

    def _scan_kj_all(self):
        """KJ scan — GBPJPY only. Alerts when score meets threshold."""
        config = self._load_config()
        threshold = config.get("gbpjpy_alert_threshold", 5)
        try:
            result = run_gbpjpy_confluence_check(self.client)
            score = int(result.get("confluence_score", "0/10").split("/")[0])
            if score >= threshold and score > self._alerted_gbpjpy_score:
                self._alerted_gbpjpy_score = score
                self._notify(format_gbpjpy_alert(result))
                watched = setup_from_gbpjpy(result)
                if watched:
                    self.watcher.add(watched)
            elif score < threshold:
                self._alerted_gbpjpy_score = 0
        except Exception as e:
            print(f"[Jarvis] KJ scan error: {e}")

    # ── Telegram Chat Loop ────────────────────────────────────────────────────

    def _chat_loop(self):
        config = self._load_config()
        token = config.get("telegram_token", "")
        chat_id = str(config.get("telegram_chat_id", ""))
        print("[Jarvis] Chat listener started.")

        while self._running:
            try:
                resp = requests.get(
                    f"https://api.telegram.org/bot{token}/getUpdates",
                    params={"offset": self._last_update_id + 1, "timeout": 20},
                    timeout=25,
                )
                if not resp.ok:
                    time.sleep(5)
                    continue

                for update in resp.json().get("result", []):
                    self._last_update_id = update["update_id"]
                    message = update.get("message", {})
                    from_id = str(message.get("chat", {}).get("id", ""))
                    text = message.get("text", "").strip().lower()

                    if from_id != chat_id or not text:
                        continue

                    reply = self._handle_command(text)
                    if reply:
                        send_telegram(token, chat_id, reply)

            except Exception as e:
                print(f"[Jarvis] Chat loop error: {e}")
                time.sleep(5)

    def _handle_command(self, text: str) -> str:
        try:
            if text in ("help", "/help", "/start"):
                return HELP_TEXT

            if text in ("summary", "overview", "dashboard"):
                return self._do_summary()

            if any(w in text for w in ("balance", "account", "equity")):
                info = self.client.get_account_info()
                accounts = info.get("accounts", [info])
                if accounts:
                    a = accounts[0]
                    return (
                        f"💰 *Account Balance*\n\n"
                        f"Balance: `${a.get('accountBalance', 'N/A')} {a.get('currency', 'USD')}`\n"
                        f"Account: `{a.get('accNum', 'N/A')}` ({a.get('status', 'N/A')})"
                    )
                return f"`{json.dumps(info)}`"

            if any(w in text for w in ("pnl", "profit", "p&l")):
                return self._do_pnl()

            if any(w in text for w in ("position", "trade", "open")):
                positions = self.client.get_positions()
                if not positions:
                    return "📭 No open positions."
                return f"📋 *Open Positions*\n\n`{json.dumps(positions, indent=2)}`"

            if text.startswith("price"):
                symbol = _parse_symbol(text) or "GBPJPY"
                tick = self.client.get_tick(symbol)
                return f"📈 *{symbol}*: `{tick['price']}`\n_as of {tick['time']}_"

            if text.startswith("bias"):
                symbol = _parse_symbol(text)
                if not symbol:
                    return "❓ Usage: `bias GBPJPY`"
                return self._do_bias(symbol)

            if any(w in text for w in ("watching", "watchlist", "levels", "watch")):
                return self.watcher.watchlist_summary()

            if text in ("clear all", "clear everything", "reset watch", "reset watchlist"):
                count = self.watcher.clear_all()
                return f"🗑 Cleared {count} setup(s) from watchlist."

            if text.startswith("clear"):
                symbol = _parse_symbol(text)
                if symbol:
                    count = self.watcher.remove_by_symbol(symbol)
                    return f"🗑 Removed {count} {symbol} setup(s)." if count else f"No {symbol} setups in watchlist."
                return "❓ Usage: `clear GBPJPY` or `clear all`"

            if text in ("rescan", "scan now", "force scan"):
                threading.Thread(target=self._force_rescan, daemon=True).start()
                return "🔄 Forcing rescan now... You'll get alerts if setups are found."

            if any(w in text for w in ("scan all", "all symbols", "all pairs")):
                threading.Thread(target=self._bg_watchlist_scan, daemon=True).start()
                return "🔍 Scanning all 6 pairs in background. Results incoming per symbol."

            if any(text.startswith(w + " ") for w in ("buy", "sell", "long", "short")):
                return self._do_manual_order(text)

            if any(w in text for w in ("confluence", "kj")) or (
                any(w in text for w in ("gbpjpy", "gj", "guppy")) and "scan" not in text
            ):
                self._notify("🔍 Running GBPJPY confluence check...")
                result = run_gbpjpy_confluence_check(self.client)
                score = result.get("confluence_score", "0/10")
                valid = result.get("setup_valid") in (True, "True")
                entry = result.get("1h_entry", {})
                reversal = result.get("4h_reversal", {})

                # Auto-watch levels if setup valid
                if valid:
                    watched = setup_from_gbpjpy(result)
                    if watched:
                        self.watcher.add(watched)
                        watch_note = "\n\n👁 _Jarvis is now watching these levels._"
                    else:
                        watch_note = ""
                else:
                    watch_note = ""

                status = "✅ SETUP VALID" if valid else "⏳ Not ready yet"
                return (
                    f"🎯 *GBPJPY Confluence Check*\n\n"
                    f"Score: *{score}* — {status}\n"
                    f"Pattern: `{reversal.get('pattern', 'None detected')}`\n"
                    f"Entry Zone: `{entry.get('entry_zone', 'N/A')}`\n"
                    f"Stop Loss: `{entry.get('sl', 'N/A')}`\n"
                    f"Take Profit: `{entry.get('tp', 'N/A')}`\n"
                    f"R:R: `{entry.get('rr', 'N/A')}:1`\n"
                    f"Neckline: `{reversal.get('neckline', 'N/A')}`\n"
                    f"Lot Guidance: `{entry.get('lot_guidance', 'N/A')}`\n\n"
                    f"_{result.get('message', '')}_"
                    f"{watch_note}"
                )

            if text.startswith("tf"):
                parts = text.split()
                valid_tfs = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "1H": "1H"}
                raw = parts[1].lower() if len(parts) > 1 else ""
                tf = valid_tfs.get(raw) or valid_tfs.get(raw.upper())
                if not tf:
                    return "❓ Valid options: `tf 5m`, `tf 15m`, `tf 30m`, `tf 1h`"
                self._load_and_set("entry_timeframe", tf)
                return f"✅ Entry timeframe set to `{tf}`.\n_All scans will now use {tf} candles for FVG + sweep detection._"

            if text.startswith("kj") or text.startswith("confluence "):
                symbol = _parse_symbol(text)
                if not symbol:
                    return "❓ Usage: `kj GBPJPY`"
                if symbol != "GBPJPY":
                    return (
                        f"⚠️ KJ is GBPJPY-specific — won't be accurate on {symbol}.\n"
                        f"Use `scan {symbol}` for SMC or `scalp {symbol}` for scalp levels."
                    )
                self._notify("🔍 Running KJ confluence check: GBPJPY...")
                return self._do_kj_check("GBPJPY")

            if any(text.startswith(w) for w in ("rate", "ratings", "rating")):
                threading.Thread(target=self._bg_ratings_all, daemon=True).start()
                return "📊 Running KJ ratings on all pairs... results incoming."

            if text.startswith("scalp"):
                symbol = _parse_symbol(text)
                if not symbol:
                    return "❓ Usage: `scalp GBPJPY`"
                self._notify(f"⚡ Scalp scan: {symbol}...")
                return self._do_scalp_scan(symbol)

            if text.startswith("range"):
                symbol = _parse_symbol(text)
                if not symbol:
                    return "❓ Usage: `range GBPJPY`"
                self._notify(f"📊 Range scan: {symbol}...")
                return self._do_range_scan(symbol)

            symbol = _parse_symbol(text)
            if "scan" in text or symbol:
                if not symbol:
                    return "❓ Which symbol? e.g. `scan GBPJPY` or just `gold`"
                self._notify(f"🔍 Scanning {symbol}...")
                return self._do_smc_scan(symbol)

            # Auto-trade controls
            if "auto on" in text or text == "auto":
                config = self._load_and_set("auto_trade", True)
                mode = config.get("trade_mode", "demo").upper()
                risk = config.get("risk_percent", 2.0)
                return (
                    f"🤖 *Auto-trading ENABLED*\n\n"
                    f"Mode: `{mode}`\n"
                    f"Risk per trade: `{risk}%`\n\n"
                    f"_Jarvis will place limit orders automatically when entry zones are hit._"
                )

            if "auto off" in text:
                self._load_and_set("auto_trade", False)
                return "🔒 *Auto-trading DISABLED.*\n\n_Jarvis will alert only — no orders placed._"

            if text.startswith("risk"):
                parts = text.replace("%", "").split()
                try:
                    pct = float(parts[1])
                    pct = max(0.5, min(pct, 5.0))  # cap between 0.5% and 5%
                    self._load_and_set("risk_percent", pct)
                    return f"✅ Risk per trade set to `{pct}%`"
                except Exception:
                    return "❓ Usage: `risk 2` or `risk 2.5`"

            if text == "mode demo":
                self._load_and_set("trade_mode", "demo")
                return "🔵 *Switched to DEMO account.*"

            if text == "mode live":
                self._load_and_set("trade_mode", "live")
                return "🔴 *Switched to LIVE account.* Be careful."

            if text.startswith("add "):
                symbol = _parse_symbol(text)
                if symbol:
                    config = self._load_config()
                    wl = config.get("watchlist", [])
                    if symbol not in wl:
                        wl.append(symbol)
                        self._load_and_set("watchlist", wl)
                        return f"✅ Added `{symbol}` to watchlist. ({len(wl)} symbols)"
                    return f"`{symbol}` is already in the watchlist."
                return "❓ Usage: `add EURUSD`"

            if text.startswith("remove "):
                symbol = _parse_symbol(text)
                if symbol:
                    config = self._load_config()
                    wl = config.get("watchlist", [])
                    if symbol in wl:
                        wl.remove(symbol)
                        self._load_and_set("watchlist", wl)
                        return f"✅ Removed `{symbol}` from watchlist. ({len(wl)} symbols)"
                    return f"`{symbol}` is not in the watchlist."
                return "❓ Usage: `remove EURUSD`"

            if any(w in text for w in ("start", "monitor on")):
                return "Jarvis is already running." if self._running else self.start()

            if any(w in text for w in ("stop", "monitor off")):
                return self.stop()

            if "status" in text:
                s = self.status()
                config = self._load_config()
                entry_tf = config.get("entry_timeframe", "15m")
                return (
                    f"⚙️ *Jarvis Status*\n\n"
                    f"Monitoring: `{'ON' if s['running'] else 'OFF'}`\n"
                    f"Interval: `{s['interval_minutes']} min`\n"
                    f"Entry Timeframe: `{entry_tf}`\n"
                    f"Watched setups: `{s['watched_setups']}`\n"
                    f"Watchlist: `{', '.join(s['watchlist'])}`"
                )

            return "❓ Didn't get that. Type `help` for commands."

        except Exception as e:
            return f"⚠️ Error: {e}"

    # ── New command helpers ───────────────────────────────────────────────────

    def _do_summary(self) -> str:
        config = self._load_config()
        mode = config.get("trade_mode", "demo").upper()
        auto = config.get("auto_trade", False)
        risk = config.get("risk_percent", 2.0)
        try:
            info = self.client.get_account_info()
            accounts = info.get("accounts", [info])
            a = accounts[0] if accounts else {}
            balance = f"${a.get('accountBalance', 'N/A')} {a.get('currency', 'USD')}"
        except Exception:
            balance = "unavailable"
        try:
            positions = self.client.get_positions()
            pos_count = len(positions)
        except Exception:
            pos_count = 0
        active = self.watcher.active_setups()
        watch_lines = "\n".join(f"  • {s.symbol} {s.direction} ({s.status})" for s in active) if active else "  None"
        return (
            f"📊 *Jarvis Summary*\n\n"
            f"Mode: `{mode}` | Auto: `{'ON ✅' if auto else 'OFF 🔒'}` | Risk: `{risk}%`\n"
            f"Balance: `{balance}`\n"
            f"Open Trades: `{pos_count}`\n\n"
            f"*Watching ({len(active)}):*\n{watch_lines}"
        )

    def _do_bias(self, symbol: str) -> str:
        result = run_smc_scan(symbol, self.client)
        structure = result.get("structure", "N/A").upper()
        vwap_bias = result.get("vwap", {}).get("bias", "N/A")
        tl = result.get("trendlines", {})
        tl_bias = tl.get("trend", "N/A") if tl else "N/A"
        return (
            f"📐 *{symbol} Bias*\n\n"
            f"4H Structure: `{structure}`\n"
            f"VWAP: `{vwap_bias}`\n"
            f"Trendline: `{tl_bias}`\n\n"
            f"_Type `scan {symbol}` for full setup._"
        )

    def _do_pnl(self) -> str:
        positions = self.client.get_positions()
        if not positions:
            return "📭 No open positions."
        lines = ["💹 *Floating P&L*\n"]
        total = 0.0
        for p in positions:
            sym = p.get("symbol", p.get("tradableInstrumentId", "?"))
            side = p.get("side", "?")
            qty = p.get("qty", p.get("volume", "?"))
            pl = p.get("pl", p.get("unrealizedPL", p.get("profit", None)))
            pl_str = f"`${float(pl):.2f}`" if isinstance(pl, (int, float)) else "`N/A`"
            if isinstance(pl, (int, float)):
                total += float(pl)
            lines.append(f"• `{sym}` {side} {qty} lots — {pl_str}")
        lines.append(f"\n*Total: `${total:.2f}`*")
        return "\n".join(lines)

    def _do_manual_order(self, text: str) -> str:
        parts = text.split()
        direction = "LONG" if parts[0] in ("buy", "long") else "SHORT"
        symbol = _parse_symbol(text)
        if not symbol:
            return "❓ Usage: `buy GBPJPY 213.50` or `sell NAS100 29300`"
        entry = None
        for p in parts[1:]:
            try:
                entry = float(p)
                break
            except ValueError:
                continue
        if entry is None:
            return "❓ Couldn't read entry price. Usage: `buy GBPJPY 213.50`"
        sl = None
        if "sl" in parts:
            try:
                sl = float(parts[parts.index("sl") + 1])
            except Exception:
                pass
        sl_note = ""
        if sl is None:
            sl_pct = 0.005
            sl = round(entry * (1 - sl_pct), 5) if direction == "LONG" else round(entry * (1 + sl_pct), 5)
            sl_note = "\n\n_Default SL at 0.5% — edit in TradeLocker._"
        result = self.executor.place_order(symbol, direction, entry, sl, tp=None)
        return self.executor.format_order_result(result) + sl_note

    def _force_rescan(self):
        try:
            self._scan_smc_all()
            self._scan_kj_all()
            self._notify("✅ Rescan complete.")
        except Exception as e:
            self._notify(f"⚠️ Rescan error: {e}")

    # ── Scan helpers ──────────────────────────────────────────────────────────

    def _do_smc_scan(self, symbol: str) -> str:
        entry_tf = self._load_config().get("entry_timeframe", "15m")
        result = run_smc_scan(symbol, self.client, entry_tf=entry_tf)

        # KJ is GBPJPY-specific only
        kj_line = ""
        if symbol == "GBPJPY":
            try:
                kj = run_gbpjpy_confluence_check(self.client)
                kj_score = int(kj.get("confluence_score", "0/10").split("/")[0])
                kj_dir = kj.get("direction", "?")
                kj_valid = kj_score >= 5
                kj_line = (
                    f"\n\n📐 *KJ Score: `{kj.get('confluence_score', '?/10')}`* — {kj_dir} "
                    f"{'✅' if kj_valid else '⏳'}"
                    f"\n_Type `kj` for full top-down breakdown._"
                )
                if kj_valid:
                    watched_kj = setup_from_gbpjpy(kj)
                    if watched_kj:
                        self.watcher.add(watched_kj)
            except Exception:
                pass

        if not result.get("valid_setups"):
            return (
                f"🔍 *{symbol} Scan*\n\n"
                f"4H Structure: `{result.get('structure', 'N/A').upper()}`\n"
                f"VWAP Bias: `{result.get('vwap', {}).get('bias', 'N/A')}`\n\n"
                f"SMC: No valid setup — checklist incomplete.\n"
                f"_Jarvis will alert when conditions align._"
                + kj_line
            )
        top = result["valid_setups"][0]
        try:
            current_price = self.client.get_tick(symbol)["price"]
        except Exception:
            current_price = None
        watched = setup_from_smc(result, top)
        self.watcher.add(watched, current_price)
        entry_tf = result.get("entry_tf", "15m")
        return (
            format_smc_alert(symbol, result["structure"], top) +
            f"\nEntry TF: `{entry_tf}`" +
            "\n\n👁 _Jarvis is now watching these levels._"
            + kj_line
        )

    def _do_kj_check(self, symbol: str) -> str:
        result = run_kj_confluence_check(symbol, self.client)
        score = result.get("raw_score", 0)
        direction = result.get("direction", "?")
        valid = result.get("setup_valid", False)
        entry = result.get("1h_entry", {})
        reversal = result.get("4h_reversal", {})
        key = result.get("key_level", {})
        checklist = result.get("checklist", {})

        stars = "⭐" * min(5, max(1, round(score / 2)))
        status = "✅ SETUP VALID" if valid else "⏳ Not ready"

        check_lines = "\n".join(
            f"{'✅' if v else '❌'} {k.replace('_', ' ').title()}"
            for k, v in checklist.items()
        )

        reply = (
            f"🎯 *{symbol} KJ Confluence — {direction}*\n\n"
            f"Score: *{result.get('confluence_score')}* {stars} — {status}\n"
            f"Key Level: `{key.get('signal', 'N/A')}`\n"
            f"Pattern: `{reversal.get('pattern', 'None')}`\n\n"
            f"Entry Zone: `{entry.get('entry_zone', 'N/A')}`\n"
            f"Stop Loss: `{entry.get('sl', 'N/A')}`\n"
            f"Take Profit: `{entry.get('tp', 'N/A')}`\n"
            f"R:R: `{entry.get('rr', 'N/A')}:1`\n\n"
            f"*Checklist:*\n{check_lines}\n\n"
            f"_{result.get('message', '')}_"
        )

        if valid:
            watched = setup_from_kj(result)
            if watched:
                self.watcher.add(watched)
                reply += "\n\n👁 _Jarvis is watching these levels._"

        return reply

    def _bg_ratings_all(self):
        """Rate all pairs: GBPJPY uses KJ score, all others use SMC score."""
        config = self._load_config()
        watchlist = config.get("watchlist", WATCHLIST)
        entry_tf = config.get("entry_timeframe", "15m")
        rows = []
        best_score = -1
        best_sym = ""
        best_dir = ""

        def _stars(n: int, out_of: int) -> str:
            pct = n / out_of if out_of else 0
            if pct >= 0.85: return "⭐⭐⭐⭐⭐"
            if pct >= 0.65: return "⭐⭐⭐⭐"
            if pct >= 0.45: return "⭐⭐⭐"
            if pct >= 0.25: return "⭐⭐"
            return "⭐"

        for symbol in watchlist:
            try:
                smc = run_smc_scan(symbol, self.client, entry_tf=entry_tf)
                smc_raw = max(
                    (s.get("confluence_score", "0/5").split("/")[0]
                     for s in smc.get("valid_setups", [{"confluence_score": "0/5"}])),
                    default="0"
                )
                try:
                    smc_int = int(smc_raw)
                except Exception:
                    smc_int = 0
                direction = smc.get("direction_bias", "?").upper()

                if symbol == "GBPJPY":
                    kj = run_gbpjpy_confluence_check(self.client)
                    kj_raw = int(kj.get("confluence_score", "0/10").split("/")[0])
                    kj_valid = kj_raw >= 5
                    direction = kj.get("direction", direction)
                    score = kj_raw
                    valid_tag = "✅" if kj_valid else "·"
                    rows.append(
                        f"{valid_tag} `{symbol:<7}` {_stars(kj_raw, 10)}  KJ `{kj_raw}/10` SMC `{smc_int}/5` — {direction}"
                    )
                else:
                    valid_tag = "✅" if smc.get("valid_setups") else "·"
                    score = smc_int
                    rows.append(
                        f"{valid_tag} `{symbol:<7}` {_stars(smc_int, 5)}  SMC `{smc_int}/5` — {direction}"
                    )

                if score > best_score:
                    best_score = score
                    best_sym = symbol
                    best_dir = direction
            except Exception as e:
                rows.append(f"· `{symbol}` — error: {e}")

        table = "\n".join(rows)
        footer = f"\n\n🥇 *Best:* `{best_sym}` {best_dir}" if best_sym else ""
        self._notify(
            f"📊 *Pair Ratings*\n\n{table}{footer}\n\n"
            f"_`scan SYMBOL` for SMC levels · `kj` for GBPJPY KJ · `scalp SYMBOL` for scalp levels_"
        )

    def _do_scalp_scan(self, symbol: str) -> str:
        entry_tf = self._load_config().get("entry_timeframe", "15m")
        result = run_smc_scan(symbol, self.client, entry_tf=entry_tf)
        structure = result.get("structure", "ranging")
        vwap_bias = result.get("vwap", {}).get("bias", "neutral")
        demand_zones = result.get("demand_zones", [])
        supply_zones = result.get("supply_zones", [])
        current_price = result.get("current_price")
        scalp_setups = [s for s in result.get("valid_setups", []) if s.get("trade_type") == "SCALP"]

        # Find nearest zones to current price
        def _nearest(zones):
            if not zones or not current_price:
                return None
            return min(zones, key=lambda z: abs(((z["top"] + z["bottom"]) / 2) - current_price))

        near_demand = _nearest(demand_zones)
        near_supply = _nearest(supply_zones)

        # Scalp quality rating 1–5
        rating = 1
        scalp_dir = "—"
        if scalp_setups:
            rating = 5
            scalp_dir = scalp_setups[0]["direction"]
        else:
            if structure in ("bullish", "bearish"):
                rating += 1
            if current_price and near_demand:
                demand_mid = (near_demand["top"] + near_demand["bottom"]) / 2
                dist_pct = abs(current_price - demand_mid) / current_price * 100
                if dist_pct < 0.15:
                    rating += 2; scalp_dir = "LONG"
                elif dist_pct < 0.4:
                    rating += 1; scalp_dir = "LONG"
            if current_price and near_supply:
                supply_mid = (near_supply["top"] + near_supply["bottom"]) / 2
                dist_pct = abs(current_price - supply_mid) / current_price * 100
                if dist_pct < 0.15:
                    rating += 2; scalp_dir = "SHORT"
                elif dist_pct < 0.4:
                    rating += 1; scalp_dir = "SHORT"
            rating = min(rating, 4)

        stars = "⭐" * rating
        lines = [f"⚡ *{symbol} Scalp Analysis*\n"]
        lines.append(f"Structure: `{structure.upper()}` | VWAP: `{vwap_bias}`")
        lines.append(f"Scalp Quality: {stars} ({rating}/5) | Direction: *{scalp_dir}*\n")

        if near_demand:
            lines.append(f"📗 Nearest Demand Zone: `{near_demand['bottom']} – {near_demand['top']}`")
        if near_supply:
            lines.append(f"📕 Nearest Supply Zone: `{near_supply['bottom']} – {near_supply['top']}`")
        if current_price:
            lines.append(f"💱 Current Price: `{current_price}`")

        if scalp_setups:
            top = scalp_setups[0]
            lines.append(f"\n✅ *Active Scalp Setup Found:*")
            lines.append(f"Entry: `{top['entry']}` | SL: `{top['sl']}` | TP: `{top['tp']}` | R:R: `{top['rr']}:1`")
            try:
                tick_price = self.client.get_tick(symbol)["price"]
            except Exception:
                tick_price = current_price
            watched = setup_from_smc(result, top)
            self.watcher.add(watched, tick_price)
            lines.append("\n👁 _Jarvis is watching these levels._")
        else:
            lines.append(f"\n⏳ No confirmed scalp setup — wait for price to reach zone, then look for a 5m flip.")

        lines.append(f"\n_Scalp = counter-trend. Smaller size, quick exit._")
        return "\n".join(lines)

    def _do_range_scan(self, symbol: str) -> str:
        entry_tf = self._load_config().get("entry_timeframe", "15m")
        result = run_range_scan(symbol, self.client, entry_tf=entry_tf)
        setups = result.get("valid_setups", [])
        rh = result.get("range_high", "N/A")
        rl = result.get("range_low", "N/A")
        if not setups:
            return (
                f"📊 *{symbol} Range Scan*\n\n"
                f"Range: `{rl} — {rh}`\n"
                f"Structure: `{result.get('structure', 'N/A').upper()}`\n\n"
                f"No valid range setups — zones too tight or R:R insufficient.\n"
            )
        lines = [f"📊 *{symbol} Range Setups*\n\nRange: `{rl} — {rh}`\n"]
        for s in setups:
            sweep_note = "✅ sweep confirmed" if s.get("sweep_confirmed") else "⏳ no sweep yet"
            lines.append(
                f"*{s['direction']}* — Entry `{s['entry']}` | SL `{s['sl']}` | TP `{s['tp']}` | R:R `{s['rr']}:1` | {sweep_note}"
            )
        lines.append("\n_Range trades: buy support, sell resistance. Exit before breakout._")
        return "\n".join(lines)

    def _bg_watchlist_scan(self):
        """Background version of watchlist scan — notifies per symbol so the chat loop stays free."""
        entry_tf = self._load_config().get("entry_timeframe", "15m")
        found_any = False
        for symbol in WATCHLIST:
            try:
                result = run_smc_scan(symbol, self.client, entry_tf=entry_tf)
                if result.get("valid_setups"):
                    top = result["valid_setups"][0]
                    try:
                        current_price = self.client.get_tick(symbol)["price"]
                    except Exception:
                        current_price = None
                    watched = setup_from_smc(result, top)
                    self.watcher.add(watched, current_price)
                    self._notify(
                        format_smc_alert(symbol, result["structure"], top) +
                        f"\nEntry TF: `{entry_tf}`\n👁 _Watching levels._"
                    )
                    found_any = True
            except Exception as e:
                print(f"[Jarvis] Scan error {symbol}: {e}")
        if not found_any:
            self._notify("🔍 *Scan All Complete*\n\nNo valid setups across watchlist right now.")

    def _do_watchlist_scan(self) -> str:
        entry_tf = self._load_config().get("entry_timeframe", "15m")
        found = []
        for symbol in WATCHLIST:
            try:
                result = run_smc_scan(symbol, self.client, entry_tf=entry_tf)
                if result.get("valid_setups"):
                    top = result["valid_setups"][0]
                    try:
                        current_price = self.client.get_tick(symbol)["price"]
                    except Exception:
                        current_price = None
                    watched = setup_from_smc(result, top)
                    self.watcher.add(watched, current_price)
                    found.append(f"• *{symbol}* {top['direction']} — Entry `{top['entry']}` | R:R `{top['rr']}:1`")
            except Exception:
                continue
        if not found:
            return "🔍 *Watchlist Scan Complete*\n\nNo valid setups right now.\n_Jarvis keeps monitoring._"
        return (
            "🎯 *Active Setups Found*\n\n" +
            "\n".join(found) +
            "\n\n👁 _All levels added to Jarvis watchlist._"
        )
