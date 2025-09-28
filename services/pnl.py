"""
PnL Service - Single Source of Truth für alle PnL-Berechnungen
Konsolidiert realized/unrealized PnL, Fees, Win-Rate und Session-Metriken.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import threading


@dataclass
class TradeRecord:
    """Einzelner Trade-Record für PnL-Berechnung"""
    timestamp: datetime
    symbol: str
    side: str  # BUY, SELL
    quantity: float
    avg_price: float
    entry_price: Optional[float] = None  # Nur bei SELL relevant
    fee_quote: float = 0.0
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    reason: Optional[str] = None  # timeout, take_profit, stop_loss, etc.


@dataclass
class PnLSummary:
    """Zusammenfassung der Session-PnL"""
    realized_pnl_net: float = 0.0
    unrealized_pnl: float = 0.0
    total_fees: float = 0.0
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    total_volume: float = 0.0
    realized_trades: int = 0
    open_positions: int = 0
    session_start: Optional[datetime] = None

    @property
    def total_pnl(self) -> float:
        """Total PnL (realized + unrealized)"""
        return self.realized_pnl_net + self.unrealized_pnl


@dataclass
class PositionState:
    """Unrealized Position für PnL-Berechnung"""
    symbol: str
    quantity: float
    avg_entry_price: float
    current_price: float
    entry_fee_per_unit: float = 0.0
    unrealized_pnl: float = 0.0


class PnLService:
    """
    Single Source of Truth für PnL-Berechnungen.
    Thread-safe, konsistente Gebührenbehandlung, einheitliche Formatierung.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._trades: List[TradeRecord] = []
        self._realized_pnl_net = 0.0
        self._total_fees = 0.0
        self._win_count = 0
        self._loss_count = 0
        self._total_volume = 0.0
        self._session_start = datetime.now(timezone.utc)
        self._unrealized_positions: Dict[str, PositionState] = {}

    def record_fill(
        self,
        symbol: str,
        side: str,
        quantity: float,
        avg_price: float,
        fee_quote: float = 0.0,
        entry_price: Optional[float] = None,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        reason: Optional[str] = None
    ) -> Optional[float]:
        """
        Erfasst einen Fill und berechnet bei SELL sofort realized PnL.

        Args:
            symbol: Trading pair (z.B. "BTC/USDT")
            side: "BUY" oder "SELL"
            quantity: Gehandelte Menge
            avg_price: Durchschnittlicher Fill-Preis
            fee_quote: Gebühren in Quote-Currency
            entry_price: Bei SELL: Entry-Preis für PnL-Berechnung
            order_id: Exchange Order ID
            client_order_id: Client Order ID
            reason: Grund für Trade (timeout, take_profit, etc.)

        Returns:
            Bei SELL: Net realized PnL, sonst None
        """
        with self._lock:
            trade = TradeRecord(
                timestamp=datetime.now(timezone.utc),
                symbol=symbol,
                side=side.upper(),
                quantity=float(quantity),
                avg_price=float(avg_price),
                entry_price=float(entry_price) if entry_price else None,
                fee_quote=float(fee_quote),
                order_id=order_id,
                client_order_id=client_order_id,
                reason=reason
            )

            self._trades.append(trade)
            self._total_fees += fee_quote
            self._total_volume += quantity * avg_price

            # Bei SELL: Realized PnL berechnen
            if side.upper() == "SELL" and entry_price is not None:
                realized_pnl_gross = quantity * (avg_price - entry_price)
                realized_pnl_net = realized_pnl_gross - fee_quote

                self._realized_pnl_net += realized_pnl_net

                # Win/Loss tracking
                if realized_pnl_net > 0:
                    self._win_count += 1
                elif realized_pnl_net < 0:
                    self._loss_count += 1

                return realized_pnl_net

            return None

    def set_unrealized_position(
        self,
        symbol: str,
        quantity: float,
        avg_entry_price: float,
        current_price: float,
        entry_fee_per_unit: float = 0.0
    ) -> float:
        """
        Setzt/aktualisiert unrealized Position.

        Returns:
            Unrealized PnL für diese Position
        """
        with self._lock:
            if quantity <= 0:
                # Position geschlossen
                if symbol in self._unrealized_positions:
                    del self._unrealized_positions[symbol]
                return 0.0

            # Unrealized PnL = (current_price - entry_price) * quantity - allocated_entry_fees
            entry_fees_total = entry_fee_per_unit * quantity
            unrealized_pnl = (current_price - avg_entry_price) * quantity - entry_fees_total

            position = PositionState(
                symbol=symbol,
                quantity=quantity,
                avg_entry_price=avg_entry_price,
                current_price=current_price,
                entry_fee_per_unit=entry_fee_per_unit,
                unrealized_pnl=unrealized_pnl
            )

            self._unrealized_positions[symbol] = position
            return unrealized_pnl

    def remove_unrealized_position(self, symbol: str) -> None:
        """Entfernt unrealized Position (bei Exit)."""
        with self._lock:
            self._unrealized_positions.pop(symbol, None)

    def get_total_unrealized_pnl(self) -> float:
        """Summiert alle unrealized PnL."""
        with self._lock:
            return sum(pos.unrealized_pnl for pos in self._unrealized_positions.values())

    def get_summary(self) -> PnLSummary:
        """Erstellt aktuelle PnL-Zusammenfassung."""
        with self._lock:
            total_trades = self._win_count + self._loss_count
            win_rate = (self._win_count / total_trades) if total_trades > 0 else 0.0

            return PnLSummary(
                realized_pnl_net=self._realized_pnl_net,
                unrealized_pnl=self.get_total_unrealized_pnl(),
                total_fees=self._total_fees,
                trade_count=len(self._trades),
                win_count=self._win_count,
                loss_count=self._loss_count,
                win_rate=win_rate,
                total_volume=self._total_volume,
                realized_trades=total_trades,
                open_positions=len(self._unrealized_positions),
                session_start=self._session_start
            )

    def format_summary_text(self, include_positions: bool = True) -> str:
        """
        Formatiert PnL-Summary als Text (für Terminal/Telegram).
        Einheitliche Formatierung für beide Kanäle.
        """
        summary = self.get_summary()

        lines = []

        # Header
        session_duration = datetime.now(timezone.utc) - self._session_start
        hours = int(session_duration.total_seconds() / 3600)
        minutes = int((session_duration.total_seconds() % 3600) / 60)

        lines.append(f"📊 PnL Session Summary ({hours:02d}:{minutes:02d}h)")
        lines.append("=" * 40)

        # Realized PnL
        realized_sign = "+" if summary.realized_pnl_net >= 0 else ""
        realized_emoji = "✅" if summary.realized_pnl_net >= 0 else "❌"
        lines.append(f"{realized_emoji} Realized: {realized_sign}{summary.realized_pnl_net:.4f} USDT")

        # Unrealized PnL
        if abs(summary.unrealized_pnl) > 0.0001:
            unrealized_sign = "+" if summary.unrealized_pnl >= 0 else ""
            unrealized_emoji = "📈" if summary.unrealized_pnl >= 0 else "📉"
            lines.append(f"{unrealized_emoji} Unrealized: {unrealized_sign}{summary.unrealized_pnl:.4f} USDT")

        # Total
        total_pnl = summary.realized_pnl_net + summary.unrealized_pnl
        total_sign = "+" if total_pnl >= 0 else ""
        total_emoji = "🚀" if total_pnl >= 0 else "🔥"
        lines.append(f"{total_emoji} Total: {total_sign}{total_pnl:.4f} USDT")

        # Stats
        lines.append("")
        lines.append(f"📈 Trades: {summary.trade_count} | Win Rate: {summary.win_rate:.1%}")
        lines.append(f"💰 Volume: {summary.total_volume:.2f} USDT")
        lines.append(f"💸 Fees: {summary.total_fees:.4f} USDT")

        # Positions
        if include_positions and self._unrealized_positions:
            lines.append("")
            lines.append("📍 Open Positions:")
            for symbol, pos in self._unrealized_positions.items():
                pnl_sign = "+" if pos.unrealized_pnl >= 0 else ""
                pnl_emoji = "📈" if pos.unrealized_pnl >= 0 else "📉"
                lines.append(f"  {pnl_emoji} {symbol}: {pos.quantity:.6f} @ {pos.avg_entry_price:.4f} ({pnl_sign}{pos.unrealized_pnl:.4f})")

        return "\n".join(lines)

    def format_telegram_text(self) -> str:
        """Telegram-spezifische Formatierung (falls unterschiedlich)."""
        return self.format_summary_text(include_positions=True)

    def get_trades_since(self, since: datetime) -> List[TradeRecord]:
        """Holt Trades seit einem bestimmten Zeitpunkt."""
        with self._lock:
            return [trade for trade in self._trades if trade.timestamp >= since]

    def get_last_trades(self, count: int = 10) -> List[TradeRecord]:
        """Holt die letzten N Trades."""
        with self._lock:
            return self._trades[-count:] if self._trades else []

    def reset_session(self) -> None:
        """Startet neue Session (für Tests oder manuellen Reset)."""
        with self._lock:
            self._trades.clear()
            self._realized_pnl_net = 0.0
            self._total_fees = 0.0
            self._win_count = 0
            self._loss_count = 0
            self._total_volume = 0.0
            self._session_start = datetime.now(timezone.utc)
            self._unrealized_positions.clear()


# Helper Functions für Kompatibilität mit bestehenden Utils

def fmt_pnl_usdt(amount: float, precision: int = 4) -> str:
    """Formatiert PnL-Betrag als USDT-String."""
    sign = "+" if amount >= 0 else ""
    return f"{sign}{amount:.{precision}f} USDT"


def compute_realized_pnl_net_with_service(
    pnl_service: PnLService,
    symbol: str,
    order: dict,
    trades: list,
    entry_price: float,
    buy_fee_per_unit: float = 0.0
) -> dict:
    """
    Berechnet realized PnL und erfasst im PnLService.
    Kompatibilitäts-Wrapper für bestehende compute_realized_pnl_net_sell Aufrufe.
    """
    from utils import compute_avg_fill_and_fees

    # Existing calculation (to maintain compatibility)
    avg_exit_price, filled_qty, proceeds_quote, sell_fees_quote = compute_avg_fill_and_fees(order, trades)

    if filled_qty <= 0:
        return {
            "symbol": symbol,
            "qty": 0.0,
            "avg_exit_price": 0.0,
            "entry_avg_price": entry_price,
            "proceeds_quote": 0.0,
            "sell_fees_quote": 0.0,
            "buy_fees_alloc_quote": 0.0,
            "cost_basis_quote": 0.0,
            "pnl_net_quote": 0.0
        }

    # Calculate components
    buy_fees_alloc_quote = buy_fee_per_unit * filled_qty
    cost_basis_quote = entry_price * filled_qty + buy_fees_alloc_quote
    pnl_gross = proceeds_quote - (entry_price * filled_qty)
    pnl_net_quote = pnl_gross - sell_fees_quote - buy_fees_alloc_quote

    # Record in PnL Service
    pnl_service.record_fill(
        symbol=symbol,
        side="SELL",
        quantity=filled_qty,
        avg_price=avg_exit_price,
        fee_quote=sell_fees_quote + buy_fees_alloc_quote,  # Total fees
        entry_price=entry_price,
        order_id=order.get("id"),
        client_order_id=order.get("clientOrderId") or order.get("client_order_id"),
        reason="exit"
    )

    return {
        "symbol": symbol,
        "qty": filled_qty,
        "avg_exit_price": avg_exit_price,
        "entry_avg_price": entry_price,
        "proceeds_quote": proceeds_quote,
        "sell_fees_quote": sell_fees_quote,
        "buy_fees_alloc_quote": buy_fees_alloc_quote,
        "cost_basis_quote": cost_basis_quote,
        "pnl_net_quote": pnl_net_quote
    }