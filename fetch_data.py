#!/usr/bin/env python3
"""
Fetches prices for every holding in positions.json, plus the OMX Stockholm 30
index (intraday + multi-range history), SEK exchange rates, and Placera.se
news headlines — and writes it all to data.json for index.html to read.

Run by .github/workflows/update.yml on a schedule. Safe to run manually too:
    python3 fetch_data.py
"""
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
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

OMX_HISTORY_RANGES = {
    'w1': ('5d', '1h'),
    'm1': ('1mo', '1d'),
    'm3': ('3mo', '1d'),
    'ytd': ('ytd', '1d'),
    'y1': ('1y', '1d'),
    'y3': ('3y', '1wk'),
    'y5': ('5y', '1wk'),
    'y10': ('10y', '1mo'),
    'max': ('max', '1mo'),
}


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


def fetch_omx():
    if not stockholm_open():
        return None, None
    try:
        meta_data = get_json(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EOMX?interval=15m&range=1d"
        )
        result = meta_data["chart"]["result"][0]
        meta = result["meta"]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        series, series_times = [], []
        for t, cl in zip(timestamps, closes):
            if cl is not None:
                series.append(round(cl, 2))
                series_times.append(t)
        prev_close = meta.get("chartPreviousClose")
        change_abs = meta.get("fulldayChange")
        if change_abs is None and prev_close is not None:
            change_abs = meta["regularMarketPrice"] - prev_close
        omx = {
            "value": meta["regularMarketPrice"],
            "changeAbs": change_abs,
            "changePercent": meta.get("regularMarketChangePercent", 0),
            "previousClose": prev_close,
            "series": series,
            "seriesTimes": series_times,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        print(f"  OMX intraday fetch failed: {e}")
        return None, None

    history = {}
    for key, (rng, interval) in OMX_HISTORY_RANGES.items():
        try:
            data = get_json(
                f"https://query1.finance.yahoo.com/v8/finance/chart/%5EOMX?interval={interval}&range={rng}"
            )
            result = data["chart"]["result"][0]
            timestamps = result["timestamp"]
            closes = result["indicators"]["quote"][0]["close"]
            series, series_times = [], []
            for t, cl in zip(timestamps, closes):
                if cl is not None:
                    series.append(round(cl, 2))
                    series_times.append(t)
            history[key] = {"series": series, "seriesTimes": series_times}
        except Exception as e:
            print(f"  OMX history[{key}] fetch failed: {e}")
    history["updatedAt"] = datetime.now(timezone.utc).isoformat()
    return omx, history


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

    print("Fetching OMX Stockholm 30...")
    omx, history = fetch_omx()
    if omx is not None:
        data["omx"] = omx
    if history is not None:
        data["omxHistory"] = {**data.get("omxHistory", {}), **history}

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
