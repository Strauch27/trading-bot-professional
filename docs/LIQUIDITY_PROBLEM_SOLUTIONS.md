# Liquidity Problem - Lösungsstrategien
**Date:** 2025-10-31
**Problem:** "Oversold" Errors bei Low-Liquidity Coins
**Beispiel:** COAI/USDT - 216 failed exit attempts

---

## Problem-Analyse

### Was passiert:

**Symptom:**
```
ERROR: Limit order failed: COAI/USDT sell 18.67@1.347:
       mexc {"msg":"Oversold","code":30005}
```

**Ursache:**
- MEXC Exchange hat **keine Käufer** für COAI/USDT zu diesem Preis
- Order kann nicht ausgeführt werden
- "Oversold" = Es gibt niemanden der kaufen will

**Warum passiert das:**
1. Low-liquidity Coin (kleines Trading-Volumen)
2. Preis gefallen → Noch weniger Käufer
3. Sell-Side des Orderbooks ist leer
4. Bot versucht zu verkaufen → Keiner kauft

---

## Ursachen-Kategorie

### Es liegt NICHT am Bid/Ask Preis alleine!

**Das Problem ist:**
- **Volumen zu gering** (nicht genug Käufer)
- **Orderbook zu dünn** (keine Liquidität)
- **Coin zu unpopulär** auf MEXC

**Selbst mit perfektem Bid-Preis:** Wenn keiner kauft, geht's nicht!

---

## 5 Lösungsansätze

### Lösung 1: **Top-Coins Filter** ⭐⭐⭐ EMPFOHLEN

**Idee:** Nur Coins mit hohem Volumen handeln

**Implementation:**
```python
# config.py
MIN_24H_VOLUME_USDT = 500_000  # Minimum $500k daily volume
# oder
MIN_24H_VOLUME_USDT = 1_000_000  # $1M for safer liquidity

EXCLUDE_LOW_LIQUIDITY_COINS = True  # Enable liquidity filter
```

**Code:**
```python
# In market guards or buy decision:
def check_volume_liquidity(symbol: str, ticker: dict) -> bool:
    """Check if coin has enough volume"""
    volume_24h_usdt = ticker.get('quoteVolume', 0)  # 24h volume in USDT

    if volume_24h_usdt < config.MIN_24H_VOLUME_USDT:
        logger.info(
            f"LIQUIDITY BLOCK: {symbol} has insufficient volume: "
            f"{volume_24h_usdt:,.0f} USDT < {config.MIN_24H_VOLUME_USDT:,.0f}",
            extra={'event_type': 'LIQUIDITY_VOLUME_TOO_LOW', 'symbol': symbol}
        )
        return False

    return True
```

**Vorteil:**
- ✅ Vermeidet Low-Liquidity Coins komplett
- ✅ Einfach zu implementieren
- ✅ Prüfung vor Buy

**Nachteil:**
- ⚠️ Weniger Trading-Opportunities (kleinere Coin-Auswahl)

**Aufwand:** 30 Minuten

---

### Lösung 2: **Spread-Based Pre-Entry Filter** ⭐⭐

**Idee:** Prüfe Spread VOR dem Buy

**Implementation:**
```python
# config.py
MAX_SPREAD_BPS_ENTRY = 50  # Max 0.5% spread für Entry
CHECK_SPREAD_BEFORE_BUY = True

# In buy guards:
def check_entry_spread(symbol: str, ticker: dict) -> bool:
    """Check spread before allowing buy"""
    bid = ticker.get('bid', 0)
    ask = ticker.get('ask', 0)

    if bid > 0 and ask > 0:
        spread_bps = ((ask - bid) / bid) * 10000

        if spread_bps > config.MAX_SPREAD_BPS_ENTRY:
            logger.warning(
                f"ENTRY BLOCKED: {symbol} spread too wide: {spread_bps:.1f}bp",
                extra={'event_type': 'ENTRY_BLOCKED_WIDE_SPREAD', 'symbol': symbol, 'spread_bps': spread_bps}
            )
            return False

    return True
```

**Vorteil:**
- ✅ Blockt problematische Coins vor Buy
- ✅ Verhindert "stuck positions"
- ✅ Konfigurierbar

**Nachteil:**
- ⚠️ Könnte auch good opportunities blocken

**Aufwand:** 20 Minuten

---

### Lösung 3: **Market Orders für Problem-Coins** ⭐

**Idee:** Wenn Limit fails, nutze Market Order

**Bereits teilweise implementiert!**
```python
# config.py
EXIT_LOW_LIQUIDITY_ACTION = "market"  # Statt "skip"
```

**Aber:** Kann teuer sein (schlechter Preis)

**Verbesserung:**
```python
# Nur für bekannt problematische Coins:
LOW_LIQUIDITY_COINS_USE_MARKET = ["COAI/USDT", "SOON/USDT", ...]  # Blacklist

def should_use_market_exit(symbol: str, spread_pct: float) -> bool:
    """Decide if market order should be used"""

    # Known problematic coin
    if symbol in config.LOW_LIQUIDITY_COINS_USE_MARKET:
        return True

    # Spread extremely wide
    if spread_pct > 15.0:
        return True

    return False
```

**Vorteil:**
- ✅ Kann Position schließen (besser als stuck)
- ✅ Selektiv (nur bei Problem-Coins)

**Nachteil:**
- ❌ Schlechterer Preis (Slippage)

**Aufwand:** 30 Minuten

---

### Lösung 4: **Dynamische Coin-Selektion** ⭐⭐⭐ BEST LONG-TERM

**Idee:** Wähle Coins basierend auf aktueller Liquidität

**Implementation:**
```python
# Hourly coin selection based on liquidity metrics
def select_tradeable_coins(all_coins: List[str], exchange) -> List[str]:
    """Select coins with good liquidity"""

    selected = []

    for symbol in all_coins:
        try:
            ticker = exchange.fetch_ticker(symbol)

            # Check volume
            volume_24h = ticker.get('quoteVolume', 0)
            if volume_24h < config.MIN_24H_VOLUME_USDT:
                continue

            # Check spread
            bid = ticker.get('bid', 0)
            ask = ticker.get('ask', 0)
            if bid > 0 and ask > 0:
                spread_pct = ((ask - bid) / bid) * 100
                if spread_pct > 1.0:  # >1% spread = illiquid
                    continue

            # Check order book depth
            orderbook = exchange.fetch_order_book(symbol, limit=20)
            bid_volume = sum(level[1] for level in orderbook['bids'][:5])  # Top 5 levels
            bid_notional = sum(level[0] * level[1] for level in orderbook['bids'][:5])

            if bid_notional < 1000:  # Need at least $1000 of bids
                continue

            selected.append(symbol)

        except Exception as e:
            logger.debug(f"Liquidity check failed for {symbol}: {e}")
            continue

    logger.info(f"Selected {len(selected)}/{len(all_coins)} coins with good liquidity")
    return selected

# Update TOPCOINS_SYMBOLS every hour
```

**Vorteil:**
- ✅✅ Beste Lösung - adaptiv
- ✅ Nur liquide Coins
- ✅ Automatisch updated

**Nachteil:**
- ⚠️ Komplex zu implementieren
- ⚠️ API calls für Filterung

**Aufwand:** 2-3 Stunden

---

### Lösung 5: **Manual Blacklist** ⭐ QUICK FIX

**Idee:** Coins die Probleme machen einfach excluden

**Implementation:**
```python
# config.py
LIQUIDITY_BLACKLIST = [
    "COAI/USDT",   # Known illiquid
    "SOON/USDT",   # Had 100+ oversold errors
    "MBG/USDT",    # Had issues
    # Add more as discovered
]

# In coin selection:
if symbol in config.LIQUIDITY_BLACKLIST:
    logger.debug(f"Skipping blacklisted coin: {symbol}")
    continue
```

**Vorteil:**
- ✅ Sofort wirksam
- ✅ Einfach
- ✅ Konfigurierbar

**Nachteil:**
- ❌ Manuell zu pflegen
- ❌ Nicht adaptiv

**Aufwand:** 10 Minuten

---

## Empfohlene Kombinations-Strategie

### Phase 1: Sofort (Quick Wins)

**1. Manual Blacklist** (10 min)
- COAI/USDT, SOON/USDT excluden
- Sofortige Verbesserung

**2. Increase MIN_24HUSD_VOLUME** (bereits vorhanden!)
```python
# config.py:347
MIN_24HUSD_VOLUME = 150000  # Aktuell

# Erhöhen auf:
MIN_24HUSD_VOLUME = 500_000  # $500k minimum
```

**3. Enable Spread Guard für Entry**
```python
# config.py:418
ENABLE_SPREAD_GUARD_ENTRY = True  # Statt False

# Dann wird MAX_SPREAD_BPS_ENTRY (20bp) genutzt
```

**Aufwand:** 15 Minuten total
**Effect:** -70-90% Oversold errors

---

### Phase 2: Diese Woche

**4. Pre-Entry Liquidity Check** (30 min)
- Spread check vor Buy
- Orderbook depth check
- Block illiquide Coins

**5. Market Order Fallback** (30 min)
- Für bekannte Problem-Coins
- Nur wenn Limit komplett failed

**Aufwand:** 1 Stunde
**Effect:** -95% Oversold errors

---

### Phase 3: Langfristig

**6. Dynamic Coin Selection** (2-3h)
- Hourly liquidity assessment
- Automatic coin list updates
- Best long-term solution

**Aufwand:** 2-3 Stunden
**Effect:** -99% Oversold errors

---

## Konkrete Empfehlung

### JETZT SOFORT (während Bot läuft):

**Option A: Manuelle Blacklist** (10 Minuten)
```python
# config.py - add:
LIQUIDITY_BLACKLIST = ["COAI/USDT", "SOON/USDT", "MBG/USDT"]
EXCLUDE_SYMBOL_PREFIXES = EXCLUDE_SYMBOL_PREFIXES + LIQUIDITY_BLACKLIST
```

**Option B: Volume Threshold erhöhen** (5 Minuten)
```python
# config.py:347
MIN_24HUSD_VOLUME = 500_000  # Statt 150_000
```

**Option C: Spread Guard aktivieren** (2 Minuten)
```python
# config.py:418
ENABLE_SPREAD_GUARD_ENTRY = True
```

**Meine Empfehlung: ALLE 3!** (Total: 17 Minuten)

---

## Root Cause: Warum diese Coins?

**COAI/USDT Analyse:**
- Kleiner Coin (niedrige Market Cap)
- Geringes Trading-Volumen
- Dünnes Orderbook
- Wenig Trader interessiert

**Preis ist nicht das Problem:**
- Selbst mit perfektem Bid-Preis
- Wenn niemand kaufen will
- Kann Order nicht filled werden

**Das ist ein Liquiditäts-Problem, kein Preis-Problem!**

---

## Implementierungs-Priorität

**SOFORT (Option B + C):** 7 Minuten
```python
# 1. Volume erhöhen
MIN_24HUSD_VOLUME = 500_000

# 2. Spread guard aktivieren
ENABLE_SPREAD_GUARD_ENTRY = True
```

**SPÄTER (wenn Bot läuft):** Option A + detaillierte Checks

---

## Erwartete Verbesserung

**Aktuell:**
- Viele Low-Liquidity Coins
- Hohe Oversold-Rate
- Stuck Positions

**Nach Quick Fixes:**
- Nur High-Liquidity Coins
- -70-90% Oversold errors
- Fast keine stuck positions

**Nach Full Implementation:**
- Adaptive Selektion
- -95-99% Oversold errors
- Nur beste Coins

---

**Soll ich die Quick Fixes (Option B + C) JETZT implementieren?**
- 7 Minuten
- Bot kann weiterlaufen
- Sofortige Verbesserung für nächste Runs
