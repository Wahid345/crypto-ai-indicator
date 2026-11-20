import json
import os
import time

STATE_FILE = "bot_trade_state.json"
COOLDOWN_SECONDS = 900  # 15m ক্যান্ডেল (৯০০ সেকেন্ড) কুলডাউন


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

    def on_trade_closed(self, candle_ts, reason=""):
        self.state["last_close_time"] = time.time()
        self.state["last_closed_candle_ts"] = candle_ts
        self.state["breakeven_locked"] = False
        self.state["peak_price"] = None
        self._save_state()
        print(f"[GUARD] Closed ({reason}). Cooldown {COOLDOWN_SECONDS}s started.")

    def can_enter_trade(
        self,
        signal,
        htf_1h_trend,
        current_candle_ts,
        is_candle_closed,
        ai_confidence,
        min_conf=0.65,
    ):
        if not is_candle_closed:
            return False

        if time.time() - self.state["last_close_time"] < COOLDOWN_SECONDS:
            return False

        if self.state["last_closed_candle_ts"] == current_candle_ts:
            return False

        if ai_confidence < min_conf:
            return False

        if signal == "LONG" and htf_1h_trend != "BULLISH":
            return False

        if signal == "SHORT" and htf_1h_trend != "BEARISH":
            return False

        return True

    def is_in_cooldown(self, current_candle_ts=None):
        if time.time() - self.state.get("last_close_time", 0) < COOLDOWN_SECONDS:
            return True
        if (
            current_candle_ts is not None
            and self.state.get("last_closed_candle_ts") == current_candle_ts
        ):
            return True
        return False
    
    def evaluate_open_position(
        self, side, entry_price, current_price, unrealized_pnl, atr_15m=0.0
    ):
        if unrealized_pnl >= self.target_profit:
            return "CLOSE_TP", current_price

        dynamic_gap = max(self.base_tsl_gap, float(atr_15m or 0.0) * 1.2)

        if side == "LONG":
            if self.state["peak_price"] is None or current_price > self.state["peak_price"]:
                self.state["peak_price"] = current_price
                self._save_state()

            stop_price = self.state["peak_price"] - dynamic_gap

            if unrealized_pnl >= self.breakeven_trigger:
                self.state["breakeven_locked"] = True
                self._save_state()
                stop_price = max(stop_price, entry_price + 0.05)

            if current_price <= stop_price:
                return "CLOSE_SL", stop_price

        elif side == "SHORT":
            if self.state["peak_price"] is None or current_price < self.state["peak_price"]:
                self.state["peak_price"] = current_price
                self._save_state()

            stop_price = self.state["peak_price"] + dynamic_gap

            if unrealized_pnl >= self.breakeven_trigger:
                self.state["breakeven_locked"] = True
                self._save_state()
                stop_price = min(stop_price, entry_price - 0.05)

            if current_price >= stop_price:
                return "CLOSE_SL", stop_price

        return "HOLD", None