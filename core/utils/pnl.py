# pnl.py
from dataclasses import dataclass


@dataclass
class PnL:
    """PnL-Datenstruktur für realized und unrealized P&L"""
    realized: float = 0.0
    unrealized: float = 0.0
    fees: float = 0.0

class PnLTracker:
    """
    PnL-Tracker mit FIFO-basierter Average-Cost-Berechnung.
    Trackt realized/unrealized P&L und Gebühren pro Symbol.
    """

    def __init__(self, fee_rate=0.001):
        self.fee = fee_rate
        self.avg_cost = {}   # symbol -> (qty, avg_price)
        self.pnl = PnL()

    def on_fill(self, symbol, side, price, qty, fee_quote=0.0):
        """
        Fill-Event verarbeiten und P&L aktualisieren.

        Args:
            symbol: Trading-Symbol
            side: "BUY" oder "SELL"
            price: Ausführungspreis
            qty: Ausgeführte Menge
            fee_quote: Direkter Fee-Betrag in Quote-Currency
        """
        # Use provided fee or calculate from rate
        fee = fee_quote if fee_quote > 0 else (price * qty * self.fee)
        self.pnl.fees += fee

        if side.upper() == "BUY":
            # BUY: Update average cost
            q0, p0 = self.avg_cost.get(symbol, (0.0, 0.0))
            new_q = q0 + qty
            new_p = (q0 * p0 + qty * price) / new_q if new_q > 0 else 0.0
            self.avg_cost[symbol] = (new_q, new_p)

        else:  # SELL
            # SELL: Realize P&L
            q0, p0 = self.avg_cost.get(symbol, (0.0, 0.0))
            if q0 > 0:
                sell_cost = qty * p0
                sell_proceeds = qty * price
                realized_pnl = sell_proceeds - sell_cost - fee
                self.pnl.realized += realized_pnl

                # Update remaining position
                remaining_qty = max(0.0, q0 - qty)
                self.avg_cost[symbol] = (remaining_qty, p0)

    def mark_to_market(self, symbol, last_price):
        """
        Mark-to-Market für unrealized P&L.

        Args:
            symbol: Trading-Symbol
            last_price: Aktueller Marktpreis

        Returns:
            Aktualisierte PnL-Struktur
        """
        q0, p0 = self.avg_cost.get(symbol, (0.0, 0.0))
        if q0 > 0 and p0 > 0:
            self.pnl.unrealized = (last_price - p0) * q0
        else:
            self.pnl.unrealized = 0.0
        return self.pnl

    def get_position_pnl(self, symbol, last_price):
        """
        Hole P&L für eine spezifische Position.

        Args:
            symbol: Trading-Symbol
            last_price: Aktueller Marktpreis

        Returns:
            Dictionary mit Position-Details
        """
        q0, p0 = self.avg_cost.get(symbol, (0.0, 0.0))

        if q0 <= 0:
            return {
                "symbol": symbol,
                "qty": 0.0,
                "avg_price": 0.0,
                "current_price": last_price,
                "unrealized_pnl": 0.0,
                "unrealized_pct": 0.0,
                "market_value": 0.0
            }

        unrealized_pnl = (last_price - p0) * q0
        unrealized_pct = (last_price / p0 - 1.0) * 100.0 if p0 > 0 else 0.0
        market_value = q0 * last_price

        return {
            "symbol": symbol,
            "qty": q0,
            "avg_price": p0,
            "current_price": last_price,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pct": unrealized_pct,
            "market_value": market_value
        }

    def snapshot(self) -> dict:
        """
        Snapshot des aktuellen PnL-Status (vereinfachtes Interface).
        Kompatibel mit dem Feedback-Format.
        """
        return {
            "pnl_realized": self.pnl.realized,
            "pnl_unrealized": self.pnl.unrealized,
            "fees": self.pnl.fees,
            "equity_delta": self.pnl.realized + self.pnl.unrealized - self.pnl.fees
        }

    def get_total_pnl(self):
        """
        Hole gesamte P&L-Statistiken.

        Returns:
            Dictionary mit Gesamt-P&L
        """
        return {
            "realized": self.pnl.realized,
            "unrealized": self.pnl.unrealized,
            "fees": self.pnl.fees,
            "total": self.pnl.realized + self.pnl.unrealized,
            "net_after_fees": self.pnl.realized + self.pnl.unrealized - self.pnl.fees
        }

    def reset(self):
        """Reset PnL-Tracker (für neue Session)"""
        self.pnl = PnL()
        self.avg_cost = {}
