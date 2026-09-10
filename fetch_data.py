#!/usr/bin/env python3
"""
Fetches prices for every holding in positions.json, plus three index/commodity
panels (OMX Stockholm 30, Nasdaq-100, gold — each intraday + multi-range
history), SEK exchange rates, and Placera.se news headlines — and writes it
all to data.json for index.html to read.

Run by .github/workflows/update.yml on a schedule. Safe to run manually too:
    python3 fetch_data.py
"""
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UA = "Mozilla/5.0"
ISIN_RE = re.compile(r'^[A-Z]{2}[A-Z0-9]{9}[0-9]$')

MARKETS = {
    'ST': ('Europe/Stockholm', 9 * 60, 17 * 60 + 30),
    'OL': ('Europe/Oslo', 9 * 60, 16 * 60 + 30),
    'CO': ('Europe/Copenhagen', 9 * 60, 17 * 60),
    'HE': ('Europe/Helsinki', 10 * 60, 18 * 60 + 25),
    'DE': ('Europe/Berlin', 9 * 60, 17 * 60 + 30),
    'L': ('Europe/London', 8 * 60, 16 * 60 + 30),
}
DEFAULT_MARKET = ('America/New_York', 9 * 60 + 30, 16 * 60)  # no suffix -> US

# Only the ranges the user actually wants on these three panels (no 3/5/10y or
# Max) — the "1 d." tab is covered separately by each index's own intraday fetch.
INDEX_HISTORY_RANGES = {
    'w1': ('5d', '1h'),
    'm1': ('1mo', '1d'),
    'm3': ('3mo', '1d'),
    'ytd': ('ytd', '1d'),
    'y1': ('1y', '1d'),
}

# Gold's real spot price comes from goldprice.dev (free, no key needed for
# this volume), but its free tier has no intraday and only 30 days of daily
# history — nowhere near enough for the range tabs above. So the CHART uses
# Yahoo's GC=F (COMEX gold futures) as a close proxy (normally ~0.5-1.5% off
# spot — same tradeoff documented in the sibling marknadssignaler project),
# while the headline price+change stays genuine spot. Throttled to once per
# hour (see fetch_gold) to stay comfortably inside goldprice.dev's anonymous
# rate limit and its free tier's 1,000 calls/month if a key gets added later.
GOLD_CHART_SYMBOL = "GC=F"
GOLD_SPOT_URL = "https://api.goldprice.dev/v1/prices?symbol=XAU-USD-SPOT"
GOLD_MIN_INTERVAL = timedelta(minutes=55)


def gold_bars_url():
    # from/to are required by the API (no default range) — ask for the last
    # 10 days so a long weekend/holiday gap still leaves a closed daily bar.
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=10)
    return (
        "https://api.goldprice.dev/v1/bars?symbol=XAU-USD-SPOT&interval=1d"
        f"&from={start.isoformat()}&to={today.isoformat()}&limit=10"
    )


def market_suffix(ticker):
    return ticker.rsplit('.', 1)[1] if '.' in ticker else None


def market_open(ticker):
    tz, open_min, close_min = MARKETS.get(market_suffix(ticker), DEFAULT_MARKET)
    return _window_open(tz, open_min, close_min)


def _window_open(tz, open_min, close_min):
    now = datetime.now(ZoneInfo(tz))
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return open_min <= minutes <= close_min


def stockholm_open():
    tz, open_min, close_min = MARKETS['ST']
    return _window_open(tz, open_min, close_min)


def us_market_open():
    tz, open_min, close_min = DEFAULT_MARKET
    return _window_open(tz, open_min, close_min)


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def resolve_isin(isin):
    """Resolve an ISIN to a real Yahoo trading symbol via Yahoo's search endpoint."""
    url = ("https://query1.finance.yahoo.com/v1/finance/search?q="
           + urllib.parse.quote(isin) + "&quotesCount=1&newsCount=0")
    try:
        data = get_json(url)
        quotes = data.get("quotes") or []
        if quotes and quotes[0].get("symbol"):
            return quotes[0]["symbol"]
    except Exception:
        pass
    return None


def instrument_type(yahoo_type):
    if yahoo_type == "ETF":
        return "etf"
    if yahoo_type == "MUTUALFUND":
        return "fond"
    return "aktie"


def fetch_quote(symbol):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(symbol) + "?interval=1d&range=1d")
    data = get_json(url)
    result = data["chart"]["result"][0]
    meta = result["meta"]
    if "regularMarketPrice" not in meta:
        return None
    return meta


def extended_hours_fields(meta):
    """US tickers only: pre/post-market price, when currently in that session."""
    if not meta.get("hasPrePostMarketData"):
        return {}
    period = meta.get("currentTradingPeriod") or {}
    now_ts = int(time.time())
    for session in ("pre", "post"):
        window = period.get(session)
        if not window:
            continue
        if window["start"] <= now_ts <= window["end"]:
            return {
                "extendedPrice": meta.get("fulldayPrice"),
                "extendedChangePercent": meta.get("fulldayChangePercent"),
                "extendedSession": session,
            }
    return {}


def fetch_prices(positions):
    prices = {}
    for ticker in positions:
        fetch_symbol = ticker
        resolved_ticker = None
        if ISIN_RE.match(ticker):
            fetch_symbol = resolve_isin(ticker)
            if not fetch_symbol:
                print(f"  {ticker}: ISIN not found on Yahoo, skipping")
                continue
            resolved_ticker = fetch_symbol

        if not market_open(fetch_symbol):
            print(f"  {ticker} ({fetch_symbol}): market closed, skipping")
            continue

        try:
            meta = fetch_quote(fetch_symbol)
        except Exception as e:
            print(f"  {ticker} ({fetch_symbol}): fetch failed — {e}")
            continue
        if not meta:
            print(f"  {ticker} ({fetch_symbol}): no regularMarketPrice")
            continue

        entry = {
            "price": meta["regularMarketPrice"],
            "changePercent": meta.get("regularMarketChangePercent", 0),
            "currency": meta.get("currency", "SEK"),
            "name": meta.get("longName") or meta.get("shortName") or fetch_symbol,
            "type": instrument_type(meta.get("instrumentType")),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        if resolved_ticker:
            entry["resolvedTicker"] = resolved_ticker
        if market_suffix(fetch_symbol) is None:
            entry.update(extended_hours_fields(meta))

        prices[ticker] = entry
        print(f"  {ticker}: {entry['price']} {entry['currency']} ({entry['type']})")
    return prices


def _series_from_chart_result(result):
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    series, series_times = [], []
    for t, cl in zip(timestamps, closes):
        if cl is not None:
            series.append(round(cl, 2))
            series_times.append(t)
    return series, series_times


def fetch_yahoo_index(symbol, intraday_interval="15m"):
    """Intraday snapshot + range-tab history for a Yahoo index/future symbol.
    Returns (index_dict, history_dict) or (None, None) on failure."""
    encoded = urllib.parse.quote(symbol)
    try:
        meta_data = get_json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?interval={intraday_interval}&range=1d"
        )
        result = meta_data["chart"]["result"][0]
        meta = result["meta"]
        series, series_times = _series_from_chart_result(result)
        prev_close = meta.get("chartPreviousClose")
        change_abs = meta.get("fulldayChange")
        if change_abs is None and prev_close is not None:
            change_abs = meta["regularMarketPrice"] - prev_close
        index = {
            "value": meta["regularMarketPrice"],
            "changeAbs": change_abs,
            "changePercent": meta.get("regularMarketChangePercent", 0),
            "previousClose": prev_close,
            "series": series,
            "seriesTimes": series_times,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        print(f"  {symbol} intraday fetch failed: {e}")
        return None, None

    history = {}
    for key, (rng, interval) in INDEX_HISTORY_RANGES.items():
        try:
            data = get_json(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?interval={interval}&range={rng}"
            )
            series, series_times = _series_from_chart_result(data["chart"]["result"][0])
            history[key] = {"series": series, "seriesTimes": series_times}
        except Exception as e:
            print(f"  {symbol} history[{key}] fetch failed: {e}")
    history["updatedAt"] = datetime.now(timezone.utc).isoformat()
    return index, history


def _gold_last_updated(data):
    ts = (data.get("indices", {}).get("gold", {}) or {}).get("updatedAt")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_gold(data):
    """Real XAU/USD spot (goldprice.dev) for the headline number, GC=F (Yahoo
    futures) for the chart/range-tabs — see the module docstring comment near
    GOLD_CHART_SYMBOL for why. Throttled to once/hour to respect the free
    goldprice.dev quota regardless of how often this whole script runs."""
    last = _gold_last_updated(data)
    if last and datetime.now(timezone.utc) - last < GOLD_MIN_INTERVAL:
        print("  Gold: skipped, fetched recently")
        return None, None

    try:
        spot_data = get_json(GOLD_SPOT_URL)
        spot = float(spot_data["symbols"][0]["price"])
    except Exception as e:
        print(f"  Gold spot fetch failed: {e}")
        return None, None

    change_abs, change_pct = None, None
    try:
        bars_data = get_json(gold_bars_url())
        closed_bars = [b for b in bars_data.get("bars", []) if b.get("is_closed")]
        if closed_bars:
            prev_close = float(closed_bars[0]["close"])
            change_abs = spot - prev_close
            change_pct = (change_abs / prev_close) * 100 if prev_close else None
    except Exception as e:
        print(f"  Gold bars fetch failed: {e}")

    index = {
        "value": round(spot, 2),
        "changeAbs": round(change_abs, 2) if change_abs is not None else None,
        "changePercent": round(change_pct, 3) if change_pct is not None else 0,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }

    chart_index, chart_history = fetch_yahoo_index(GOLD_CHART_SYMBOL, intraday_interval="15m")
    if chart_index:
        index["series"] = chart_index["series"]
        index["seriesTimes"] = chart_index["seriesTimes"]
    return index, chart_history


def fetch_fx():
    fx = {}
    for code in ("USD", "EUR", "GBP", "NOK", "DKK"):
        try:
            data = get_json(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{code}SEK=X?interval=1d&range=1d"
            )
            price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
            fx[code] = round(price, 4)
        except Exception as e:
            print(f"  FX {code} fetch failed: {e}")
    fx["updatedAt"] = datetime.now(timezone.utc).isoformat()
    return fx


def fetch_news():
    try:
        html = get_text("https://www.placera.se/kategorier/nyheter")
    except Exception as e:
        print(f"  News fetch failed: {e}")
        return None
    pattern = re.compile(r'href="(/nyheter/[^"]+)"[^>]*><h2[^>]*>(.*?)</h2>', re.S)
    items, seen = [], set()
    for href, title in pattern.findall(html):
        if href in seen:
            continue
        seen.add(href)
        clean_title = re.sub(r"<[^>]+>", "", title).strip()
        items.append({"title": clean_title, "url": "https://www.placera.se" + href})
        if len(items) >= 8:
            break
    if not items:
        return None
    return {"items": items, "updatedAt": datetime.now(timezone.utc).isoformat()}


def main():
    with open("positions.json", encoding="utf-8") as f:
        positions = json.load(f)

    try:
        with open("data.json", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

    print(f"Fetching prices for {len(positions)} positions...")
    new_prices = fetch_prices(positions)
    data["prices"] = {**data.get("prices", {}), **new_prices}

    indices = data.get("indices", {})
    indices_history = data.get("indicesHistory", {})

    print("Fetching OMX Stockholm 30...")
    if stockholm_open():
        omx, omx_history = fetch_yahoo_index("^OMX")
        if omx is not None:
            indices["omx"] = omx
        if omx_history is not None:
            indices_history["omx"] = {**indices_history.get("omx", {}), **omx_history}
    else:
        print("  Stockholm closed, skipping")

    print("Fetching Nasdaq-100...")
    if us_market_open():
        ndx, ndx_history = fetch_yahoo_index("^NDX")
        if ndx is not None:
            indices["ndx"] = ndx
        if ndx_history is not None:
            indices_history["ndx"] = {**indices_history.get("ndx", {}), **ndx_history}
    else:
        print("  US market closed, skipping")

    print("Fetching gold (XAU/USD)...")
    gold, gold_history = fetch_gold(data)
    if gold is not None:
        indices["gold"] = gold
    if gold_history is not None:
        indices_history["gold"] = {**indices_history.get("gold", {}), **gold_history}

    data["indices"] = indices
    data["indicesHistory"] = indices_history

    print("Fetching FX rates...")
    fx = fetch_fx()
    if len(fx) > 1:  # more than just updatedAt
        data["fx"] = {**data.get("fx", {}), **fx}

    print("Fetching Placera news...")
    news = fetch_news()
    if news is not None:
        data["news"] = news

    data["updatedAt"] = datetime.now(timezone.utc).isoformat()

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("Wrote data.json")


if __name__ == "__main__":
    main()
