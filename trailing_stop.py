# trailing_stop.py

class TrailingStopLossManager:
    def __init__(self, entry_price, position_type, initial_sl_distance=1.25, trailing_distance=1.25):
        self.entry_price = float(entry_price)
        self.position_type = position_type.lower()  # 'buy' অথবা 'sell'
        self.trailing_distance = float(trailing_distance)
        
        if self.position_type in ['buy', 'long']:
            self.best_price = self.entry_price
            self.current_sl = self.entry_price - initial_sl_distance
        else:
            self.best_price = self.entry_price
            self.current_sl = self.entry_price + initial_sl_distance

    def update_trailing_stop(self, current_market_price):
        current_market_price = float(current_market_price)
        updated = False

        if self.position_type in ['buy', 'long']:
            if current_market_price > self.best_price:
                self.best_price = current_market_price
                potential_sl = self.best_price - self.trailing_distance
                if potential_sl > self.current_sl:
                    self.current_sl = potential_sl
                    updated = True
        else:
            if current_market_price < self.best_price:
                self.best_price = current_market_price
                potential_sl = self.best_price + self.trailing_distance
                if potential_sl < self.current_sl:
                    self.current_sl = potential_sl
                    updated = True

        return self.current_sl, updated

    def check_sl_hit(self, current_market_price):
        current_market_price = float(current_market_price)
        if self.position_type in ['buy', 'long']:
            return current_market_price <= self.current_sl
        else:
            return current_market_price >= self.current_sl