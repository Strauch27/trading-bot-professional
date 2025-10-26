#!/usr/bin/env python3
"""
Portfolio Reset Operations Module

Contains functions for:
- Full portfolio reset (sell all assets)
- Stale order cleanup
- Order reattachment (crash recovery)

NOTE: Vollständige Implementierungen können aus trading_legacy.py (Zeilen 747-1173) kopiert werden.
"""

import time
import logging
from typing import Dict

logger = logging.getLogger(__name__)


def cleanup_stale_orders(exchange, held_assets, open_buy_orders, max_age_minutes=60):
    """Bereinigt alte offene Orders die Kapital blockieren - mit Crash-Recovery"""
    from core.logging.loggingx import log_mexc_update, log_mexc_cancel
    try:
        from config import take_profit_threshold, stop_loss_threshold
        cleaned_count = 0
        reattached_count = 0

        # MEXC benötigt ein Symbol für fetchOpenOrders - iteriere über alle gehaltenen Assets und offenen Orders
        symbols_to_check = set()

        # Füge Symbole von gehaltenen Assets hinzu
        for symbol in held_assets.keys():
            symbols_to_check.add(symbol)

        # Füge Symbole von offenen Buy-Orders hinzu
        for symbol in open_buy_orders.keys():
            symbols_to_check.add(symbol)

        # Prüfe Orders für jedes Symbol
        for symbol in symbols_to_check:
            try:
                open_orders = exchange.fetch_open_orders(symbol)
                for order in open_orders:
                    if order['timestamp']:
                        age_minutes = (time.time() - order['timestamp']/1000) / 60

                        # Überprüfe ob diese Order in unseren aktiven Orders ist
                        is_active = False
                        for asset_symbol, asset_data in held_assets.items():
                            if asset_data.get('active_order_id') == order['id']:
                                is_active = True
                                break
                        for buy_symbol, buy_data in open_buy_orders.items():
                            if buy_data.get('order_id') == order['id']:
                                is_active = True
                                break

                        # Crash-Recovery: Reattach verloren gegangene TP/SL Orders
                        if not is_active and symbol in held_assets:
                            px = float(order.get('price') or 0.0)
                            bp = float(held_assets[symbol].get('buying_price') or 0.0)
                            if px and bp:
                                # Check ob es eine TP oder SL Order ist (mit 2% Toleranz)
                                tp_ok = px >= bp * take_profit_threshold * 0.98
                                sl_ok = px <= bp * stop_loss_threshold * 1.02

                                if tp_ok or sl_ok:
                                    # Reattach die Order zum State
                                    held_assets[symbol]['active_order_id'] = order['id']
                                    held_assets[symbol]['active_order_type'] = 'TAKE_PROFIT' if tp_ok else 'STOP_LOSS'
                                    if sl_ok:
                                        held_assets[symbol]['stop_loss_price'] = px
                                    is_active = True
                                    reattached_count += 1
                                    logger.info(f"Reattached active order {order['id']} to {symbol} (crash-recovery)",
                                              extra={'event_type':'ORDER_REATTACHED','symbol':symbol,
                                                    'order_id':order['id'],'type':'TP' if tp_ok else 'SL'})

                        # Nur canceln, wenn weiterhin nicht aktiv UND alt
                        if not is_active and age_minutes > max_age_minutes and order['status'] == 'open':
                            try:
                                exchange.cancel_order(order['id'], symbol)
                                cleaned_count += 1
                                # Log MEXC cancel
                                try:
                                    log_mexc_cancel(order['id'], symbol, reason="stale_cleanup")
                                except Exception:
                                    # Logging is optional, don't fail cleanup
                                    pass
                                logger.info(f"Alte Order storniert: {symbol} - {order['id']}",
                                          extra={'event_type': 'STALE_ORDER_CLEANED', 'symbol': symbol,
                                                'order_id': order['id'], 'age_minutes': age_minutes})
                            except Exception as ce:
                                logger.debug(f"Cancel stale order failed {symbol}: {ce}",
                                           extra={'event_type':'STALE_ORDER_CANCEL_FAIL','symbol':symbol})

            except Exception as e:
                logger.debug(f"Konnte offene Orders für {symbol} nicht abrufen: {e}",
                           extra={'event_type': 'FETCH_OPEN_ORDERS_ERROR', 'symbol': symbol, 'error': str(e)})

        if cleaned_count > 0 or reattached_count > 0:
            logger.info(f"{cleaned_count} alte Orders bereinigt, {reattached_count} Orders reattached",
                       extra={'event_type': 'STALE_ORDERS_RESULT', 'cleaned': cleaned_count, 'reattached': reattached_count})
    except Exception as e:
        logger.error(f"Fehler bei der Bereinigung alter Orders: {e}",
                    extra={'event_type': 'CLEANUP_ERROR', 'error': str(e)})


def full_portfolio_reset(exchange, settlement_manager) -> bool:
    """
    Verkauft beim Start alle Assets außer USDT zum Marktpreis und wartet auf das Settlement.
    Diese Funktion dient dazu, mit einem sauberen Zustand zu starten.
    Returns: True bei Erfolg (auch wenn keine Assets zu verkaufen waren), False bei Fehler
    """
    from core.logging.loggingx import log_mexc_cancel
    from .helpers import amount_to_precision, price_to_precision
    from .settlement import refresh_budget_from_exchange, refresh_budget_from_exchange_safe
    from core.utils.utils import get_symbol_limits, next_client_order_id
    # Note: PORTFOLIO_RESET_START already logged in portfolio.py - no duplicate needed

    try:
        markets = exchange.load_markets()
        balance = exchange.fetch_balance()
    except Exception as e:
        logger.error(f"Konnte Markets oder Balance nicht abrufen: {e}", extra={'event_type': 'PORTFOLIO_RESET_FETCH_FAIL'})
        return False

    totals = balance.get('total', {}) or {}
    free = balance.get('free', {}) or {}
    used = balance.get('used', {}) or {}

    # Mapping "BTCUSDT" -> "BTC/USDT" vorbereiten
    market_symbols = set(markets.keys())
    normalized = {s.replace("/", ""): s for s in market_symbols}

    # Stablecoins die wir nicht verkaufen wollen
    stablecoins = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "UST"}

    assets_processed = 0
    assets_sold = 0

    for asset, qty_total in totals.items():
        if not asset or asset.upper() in stablecoins:
            continue

        # bevorzugt normalisiert (z.B. 'BTCUSDT' -> 'BTC/USDT'), sonst Fallback
        symbol_key = f"{asset}USDT"
        symbol = normalized.get(symbol_key, f"{asset}/USDT")

        if symbol not in markets:
            logger.info(f"Reset: {symbol} nicht in Markets – übersprungen.",
                        extra={'event_type': 'RESET_SKIP_NOT_IN_MARKETS', 'symbol': symbol, 'asset': asset})
            continue

        m = markets.get(symbol, {})
        m_type = (m.get('type') or ('spot' if m.get('spot') else None))
        if m_type != 'spot':
            logger.info(f"Reset: {symbol} ist kein Spot-Markt – überspringe.",
                        extra={'event_type': 'RESET_SKIP_NOT_SPOT', 'symbol': symbol,
                               'm_type': m_type, 'active': m.get('active')})
            continue
        if m.get('active') is False:
            logger.info(f"Reset: {symbol} laut CCXT inactive – versuche Verkauf trotzdem.",
                        extra={'event_type': 'RESET_TRY_INACTIVE_MARKET', 'symbol': symbol})

        assets_processed += 1

        # Storniere offene Orders für dieses Symbol
        try:
            open_orders = exchange.fetch_open_orders(symbol)
            if open_orders:
                for o in open_orders:
                    try:
                        exchange.cancel_order(o['id'], symbol)
                        # Log MEXC cancel
                        try:
                            log_mexc_cancel(o['id'], symbol, reason="portfolio_reset")
                        except Exception:
                            # Logging is optional, don't fail reset
                            pass
                    except Exception as ce:
                        logger.warning(f"Cancel fehlgeschlagen {symbol}: {ce}",
                                     extra={'event_type': 'RESET_CANCEL_FAILED', 'symbol': symbol})
                logger.info(f"{len(open_orders)} offene Order(s) für {symbol} storniert",
                          extra={'event_type': 'RESET_ORDERS_CANCELED', 'symbol': symbol, 'count': len(open_orders)})
                # Nach Cancel Balance refreshen
                time.sleep(1)
                balance = exchange.fetch_balance()
                free = balance.get('free', {}) or {}
                used = balance.get('used', {}) or {}
        except Exception as e:
            logger.debug(f"Open orders check Fehler {symbol}: {e}",
                       extra={'event_type': 'RESET_OPEN_ORDERS_CHECK_ERROR', 'symbol': symbol})

        # Bestimme verfügbare Menge (free mit Fallback auf total)
        qty_free = float(free.get(asset, 0.0) or 0.0)
        qty_used = float(used.get(asset, 0.0) or 0.0)
        qty_total = float(totals.get(asset, 0.0) or 0.0)
        qty = qty_free if qty_free > 0 else qty_total

        if qty <= 0:
            logger.debug(f"Reset: {symbol} hat keine Menge (free={qty_free}, used={qty_used}, total={qty_total})",
                       extra={'event_type': 'RESET_SKIP_NO_BALANCE', 'symbol': symbol,
                             'free': qty_free, 'used': qty_used, 'total': qty_total})
            continue

        # Preis on-demand holen (nicht auf vorgeholte tickers verlassen)
        try:
            ticker = exchange.fetch_ticker(symbol)
            price = float(ticker.get('last') or ticker.get('close') or ticker.get('ask') or 0.0)
        except Exception as pe:
            logger.debug(f"Reset: Konnte Preis für {symbol} nicht abrufen: {pe}",
                       extra={'event_type': 'RESET_SKIP_NO_PRICE_FETCH', 'symbol': symbol})
            price = 0.0
            # Fallback: Best Bid/Ask aus Orderbuch
            try:
                ob = exchange.fetch_order_book(symbol)
                if ob and ob.get('bids'):
                    price = float(ob['bids'][0][0])
                elif ob and ob.get('asks'):
                    price = float(ob['asks'][0][0])
            except Exception:
                pass

        if price <= 0:
            logger.info(f"Reset: {symbol} hat keinen Preis – versuche dennoch Market-Sell ohne Wertprüfung.",
                        extra={'event_type': 'RESET_NO_PRICE_ATTEMPT_SELL', 'symbol': symbol,
                               'free': qty_free, 'used': qty_used, 'qty': qty})
            # Wir setzen später order_value nur, wenn price > 0 ist.

        # Limits und Min-Order-Value prüfen (nur wenn wir einen Preis haben)
        limits = markets[symbol].get('limits', {})

        # Amount-Präzision anwenden (V9_3-style: richtige Tick/Step-Interpretation)
        market_info = markets[symbol]
        if market_info:
            try:
                from helpers_filters import create_filters_from_market, _floor_to_step
                f = create_filters_from_market(market_info)
                # Floor für SELL-Orders (Inventar-begrenzt)
                qty = _floor_to_step(qty, f.stepSize)
            except ImportError:
                # Fallback if helpers_filters not available
                pass

        # Dust-Check mit robuster Limits-Prüfung
        if price > 0:
            # Nutze get_symbol_limits für robuste Limits-Extraktion
            limits_data = get_symbol_limits(exchange, symbol)
            min_cost = float(limits_data.get("min_cost", 5.1))
            amount_step = float(limits_data.get("amount_step", 0.001))
            min_amount = float(limits_data.get("min_amount", 0.0))

            # Kleinste verkaufbare Menge bei aktuellem Preis (Bid)
            min_sell_qty_by_cost = (min_cost / price) if price else 0.0

            # Quantize up - sicherstellen dass wir über Minimum sind
            def quantize_up(value, step):
                """Rundet nach oben auf nächste gültige Stufe"""
                if step <= 0:
                    return value
                import math
                return math.ceil(value / step) * step

            min_sell_qty = max(min_amount, quantize_up(min_sell_qty_by_cost, amount_step))
            order_value = qty * price

            # Dust-Prüfung: Wenn qty < min_sell_qty → Dust überspringen
            if qty < min_sell_qty:
                logger.info(f"DUST_SKIPPED - {symbol} qty={qty} < min_sell_qty={min_sell_qty}",
                           extra={'event_type': 'DUST_SKIPPED', 'symbol': symbol,
                                  'qty': qty, 'min_sell_qty': min_sell_qty, 'price': price,
                                  'min_cost': min_cost, 'order_value': order_value})

                # Dust zum Sweeper hinzufügen (wenn Settlement Manager verfügbar)
                if settlement_manager and hasattr(settlement_manager, 'dust_sweeper'):
                    dust_sweeper = settlement_manager.dust_sweeper
                    if dust_sweeper:
                        dust_sweeper.add_dust(symbol, qty, price)

                continue  # KEIN Verkaufsversuch für Dust

            # Zusätzliche Mindest-Order-Value Prüfung
            if order_value < min_cost:
                logger.info(f"DUST_SKIPPED - {symbol} order_value={order_value:.2f} < min_cost={min_cost:.2f}",
                           extra={'event_type': 'DUST_SKIPPED', 'symbol': symbol,
                                  'value': order_value, 'min_required': min_cost,
                                  'qty': qty, 'price': price})

                # Dust zum Sweeper hinzufügen
                if settlement_manager and hasattr(settlement_manager, 'dust_sweeper'):
                    dust_sweeper = settlement_manager.dust_sweeper
                    if dust_sweeper:
                        dust_sweeper.add_dust(symbol, qty, price)

                continue  # KEIN Verkaufsversuch für Dust

        else:
            order_value = 0  # Kein Preis verfügbar

        # Market-Sell ausführen
        try:
            if price > 0:
                logger.info(f"Verkaufe {qty} {asset} ({symbol}) für ~{order_value:.2f} USDT",
                          extra={'event_type': 'RESET_SELL_ATTEMPT', 'symbol': symbol,
                                'amount': qty, 'est_value': order_value})
            else:
                logger.info(f"Verkaufe {qty} {asset} ({symbol}) ohne Preisinformation",
                          extra={'event_type': 'RESET_SELL_ATTEMPT', 'symbol': symbol,
                                'amount': qty})

            # Nutze amount_to_precision für finale Präzision
            precise_qty = amount_to_precision(exchange, symbol, qty)
            try:
                order = exchange.create_market_sell_order(symbol, precise_qty)
            except Exception as e:
                err = str(e).lower()

                # 1) Preis-Pflicht (MEXC 700004) -> Market mit Preis (oder Limit IOC) aus Orderbuch
                if ("mandatory parameter 'price'" in err) or ("700004" in err):
                    # Bestes Gebot (Bid) als Ausführungspreis holen
                    ob = exchange.fetch_order_book(symbol)
                    exec_price = None
                    if ob and ob.get('bids'):
                        exec_price = float(ob['bids'][0][0])
                    elif ob and ob.get('asks'):
                        exec_price = float(ob['asks'][0][0])

                    if exec_price:
                        exec_price = price_to_precision(exchange, symbol, exec_price)
                        logger.warning(
                            f"RESET_SELL_PRICE_FALLBACK {symbol}: nutze Preis {exec_price} wegen 700004",
                            extra={'event_type': 'RESET_SELL_PRICE_FALLBACK', 'symbol': symbol, 'price': exec_price}
                        )
                        # a) Market + price (manche MEXC-Setups akzeptieren das)
                        try:
                            client_order_id = next_client_order_id(symbol, side="SELL")
                            order = exchange.create_order(symbol, 'market', 'sell', precise_qty, exec_price, {'clientOrderId': client_order_id})
                        except Exception:
                            # b) Limit IOC als letzter Rettungsanker
                            order = exchange.create_limit_sell_order(symbol, precise_qty, exec_price, {'timeInForce': 'IOC'})
                    else:
                        raise e  # kein Preis verfügbar -> weiterwerfen

                # 2) Symbol-Route/Mapping-Problem (10007) -> Märkte neu laden & Limit-IOC versuchen
                elif ("symbol not support" in err) or ("10007" in err):
                    # a) Märkte neu laden
                    try:
                        exchange.load_markets(True)
                    except Exception:
                        pass
                    logger.warning(
                        f"RESET_SELL_RETRY {symbol}: first attempt failed: {e}, retrying with LIMIT-IOC",
                        extra={'event_type': 'RESET_SELL_RETRY', 'symbol': symbol}
                    )

                    # b) Besten Bid als Preis holen
                    px = None
                    try:
                        ob = exchange.fetch_order_book(symbol)
                        if ob and ob.get('bids'):
                            px = float(ob['bids'][0][0])
                        elif ob and ob.get('asks'):
                            px = float(ob['asks'][0][0])
                    except Exception:
                        px = None

                    if px:
                        px = price_to_precision(exchange, symbol, px * 0.9995)  # 0.05% unter Bid für aggressiveres Kreuzen
                        # c) Limit-IOC platzieren (kreuzt Orderbuch und füllt i.d.R. sofort)
                        try:
                            logger.warning(
                                f"RESET_SELL_LIMIT_IOC {symbol}: Limit-IOC bei {px}",
                                extra={'event_type':'RESET_SELL_LIMIT_IOC','symbol':symbol,'price':px}
                            )
                            order = exchange.create_limit_sell_order(symbol, precise_qty, px, {'timeInForce': 'IOC'})
                        except Exception as e2:
                            # Fallback ohne IOC (GTC) – sollte ebenfalls sofort gefüllt werden
                            logger.warning(
                                f"RESET_SELL_LIMIT_GTC {symbol}: IOC fehlgeschlagen ({e2}), versuche GTC",
                                extra={'event_type':'RESET_SELL_LIMIT_GTC','symbol':symbol,'price':px}
                            )
                            order = exchange.create_limit_sell_order(symbol, precise_qty, px)
                    else:
                        # Kein Preis ermittelt → als letzter Versuch noch einmal Market
                        order = exchange.create_market_sell_order(symbol, precise_qty)

                else:
                    # Unbekannter Fehler → bubbled up
                    raise e

            if order:
                filled = float(order.get('filled', order.get('amount', 0)) or 0.0)
                avg = float(order.get('average', order.get('price', price)) or price)
                revenue = filled * avg

                if revenue <= 0:
                    # Letzter Versuch: Fills nachziehen
                    try:
                        # 1) Order-Status aktualisieren
                        oid = order.get('id')
                        if oid:
                            refreshed = exchange.fetch_order(oid, symbol)
                            if refreshed:
                                filled = float(refreshed.get('filled') or filled or 0)
                                avg = float(refreshed.get('average') or refreshed.get('price') or avg or price or 0)
                                revenue = (filled * avg) if (filled and avg) else revenue
                        # 2) Trades der letzten ~90s summieren
                        if revenue <= 0 and hasattr(exchange, 'fetch_my_trades'):
                            import time as _t
                            since = int((_t.time() - 90) * 1000)
                            trades = exchange.fetch_my_trades(symbol, since=since)
                            sold = [t for t in trades or [] if str(t.get('side')).lower() == 'sell']
                            if sold:
                                sum_cost = sum(float(t.get('cost') or 0) for t in sold)
                                sum_amt  = sum(float(t.get('amount') or 0) for t in sold)
                                if sum_cost > 0 and sum_amt > 0:
                                    revenue = sum_cost
                                    filled = sum_amt
                                    avg = revenue / filled
                    except Exception:
                        pass

                if revenue > 0:
                    settlement_manager.add_pending(f"{symbol}_{time.time()}", revenue, symbol)
                    assets_sold += 1
                    logger.info(f"Verkauf von {symbol} erfolgreich: {filled} @ {avg:.8f} = {revenue:.2f} USDT",
                              extra={'event_type': 'RESET_SELL_SUCCESS', 'symbol': symbol,
                                    'amount': filled, 'price': avg, 'revenue': revenue})
                else:
                    logger.warning(f"Reset: Verkauf von {symbol} ohne Revenue",
                                 extra={'event_type': 'RESET_SELL_NO_REVENUE', 'symbol': symbol})
            else:
                logger.warning(f"Reset: Verkauf von {symbol} gab None zurück",
                             extra={'event_type': 'RESET_SELL_NONE', 'symbol': symbol, 'qty': qty})

        except Exception as e:
            logger.error(f"Reset: Verkauf von {symbol} fehlgeschlagen: {e}",
                       extra={'event_type': 'RESET_SELL_FAILED', 'symbol': symbol,
                             'qty': qty, 'price': price, 'error': str(e)})

    logger.info(f"Portfolio-Reset: {assets_processed} Assets geprüft, {assets_sold} verkauft",
              extra={'event_type': 'PORTFOLIO_RESET_SUMMARY',
                    'assets_processed': assets_processed, 'assets_sold': assets_sold})

    # Auf Abschluss aller Settlements warten
    if settlement_manager.get_total_pending() > 0:
        logger.info("Alle Verkaufs-Orders platziert. Warte auf Abschluss der Settlements...")
        refresh_func = lambda: refresh_budget_from_exchange_safe(exchange, 0, timeout=5.0)
        settlement_manager.wait_for_all_settlements(refresh_func, timeout=300)

    logger.info("Portfolio-Reset abgeschlossen.", extra={'event_type': 'PORTFOLIO_RESET_COMPLETE'})
    return True  # Erfolg
