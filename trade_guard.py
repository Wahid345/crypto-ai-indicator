import json
import os
import time

STATE_FILE = "bot_trade_state.json"
COOLDOWN_SECONDS = 900  # 15m ক্যান্ডেল (৯০০ সেকেন্ড) বিরতি


def _normalize_ts(ts):
    if ts is None:
        return None
    if hasattr(ts, "timestamp"):
        return int(ts.timestamp())
    return int(float(ts))


class TradeGuardAndExitManager:
    def __init__(self, target_profit=5.0, breakeven_trigger=1.50, base_tsl_gap=1.25):
        self.target_profit = target_profit
        self.breakeven_trigger = breakeven_trigger
        self.base_tsl_gap = base_tsl_gap
        self.state = self._load_state()

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "last_close_time": 0,
            "last_closed_candle_ts": None,
            "breakeven_locked": False,
            "peak_price": None,
        }

    def _save_state(self):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def is_in_cooldown(self, current_candle_ts=None):
        if time.time() - self.state.get("last_close_time", 0) < COOLDOWN_SECONDS:
            return True
        norm_ts = _normalize_ts(current_candle_ts)
        if (
            norm_ts is not None
            and self.state.get("last_closed_candle_ts") == norm_ts
        ):
            return True
        return False

    def on_trade_closed(self, candle_ts=None, reason=""):
        self.state["last_close_time"] = time.time()
        norm_ts = _normalize_ts(candle_ts)
        if norm_ts is not None:
            self.state["last_closed_candle_ts"] = norm_ts
        self.state["breakeven_locked"] = False
        self.state["peak_price"] = None
        self._save_state()
        print(f"[GUARD] Closed ({reason}). Cooldown {COOLDOWN_SECONDS}s started.", flush=True)

    def can_enter_trade(
        self,
        signal,
        htf_1h_trend,
        current_candle_ts,
        is_candle_closed,
        ai_confidence,
        min_conf=65.0,
    ):
        if not is_candle_closed:
            return False

        if self.is_in_cooldown(current_candle_ts):
            return False

        if float(ai_confidence) < float(min_conf):
            return False

        sig = (signal or "").upper()
        trend = (htf_1h_trend or "").upper()

        if sig in ("LONG", "BUY") and trend != "BULLISH":
            return False

        if sig in ("SHORT", "SELL") and trend != "BEARISH":
            return False

        return True

    def evaluate_open_position(
        self, side, entry_price, current_price, unrealised_pnl, atr_15m=0.0
    ):
        if unrealised_pnl >= self.target_profit:
            return "CLOSE_TP", current_price

        dynamic_gap = max(self.base_tsl_gap, float(atr_15m or 0.0) * 1.2)
        side_norm = (side or "").lower()

        if side_norm in ("buy", "long"):
            if self.state["peak_price"] is None or current_price > self.state["peak_price"]:
                self.state["peak_price"] = current_price

            stop_price = self.state["peak_price"] - dynamic_gap

            # PnL >= $1.50 হলে এন্ট্রি প্রাইসের সামান্য ওপরে লক
            if unrealised_pnl >= self.breakeven_trigger:
                self.state["breakeven_locked"] = True
                stop_price = max(stop_price, entry_price + 0.05)

            # PnL >= $3.00 হলে আরও ওপরে প্রফিট লক (প্রাইস স্কেলে +$0.35 অফসেট)
            if unrealised_pnl >= 3.00:
                stop_price = max(stop_price, entry_price + 0.35)

            self._save_state()
            if current_price <= stop_price:
                return "CLOSE_SL", stop_price

        elif side_norm in ("sell", "short"):
            if self.state["peak_price"] is None or current_price < self.state["peak_price"]:
                self.state["peak_price"] = current_price

            stop_price = self.state["peak_price"] + dynamic_gap

            # PnL >= $1.50 হলে এন্ট্রি প্রাইসের সামান্য নিচে লক
            if unrealised_pnl >= self.breakeven_trigger:
                self.state["breakeven_locked"] = True
                stop_price = min(stop_price, entry_price - 0.05)

            # PnL >= $3.00 হলে আরও নিচে প্রফিট লক (প্রাইস স্কেলে -$0.35 অফসেট)
            if unrealised_pnl >= 3.00:
                stop_price = min(stop_price, entry_price - 0.35)

            self._save_state()
            if current_price >= stop_price:
                return "CLOSE_SL", stop_price

        return "HOLD", None