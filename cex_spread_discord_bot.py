#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CEX-CEX Spread Alert + Indicator Alerts (Discord)
-------------------------------------------------
- CEX間スプレッド監視（閾値bps超えで通知）
- ブラックリストや閾値は config.json で動的変更（自動リロード）
- ETHのBB(ボリンジャー)＋RSIクロスで別チャンネル通知
- 追加: BYBIT_AUTO_ALL（Bybit上場銘柄“全て”自動監視：スポット）
- 追加: 起動時に「デプロイ完了」をDiscordへ通知
- 追加: 大量銘柄向けに batch_size / max_concurrency を追加

依存: aiohttp, python-dotenv
"""

import os
import asyncio
import time
import json
import math
from typing import Dict, Tuple, List, Optional
import aiohttp
from aiohttp import ClientSession
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv()

DEFAULT_CONFIG_PATH = os.getenv("CONFIG_PATH", "config.json")
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=8.0, connect=5.0)

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
INDICATOR_WEBHOOK_URL_ENV = os.getenv("INDICATOR_WEBHOOK_URL", "").strip()

# ------------- 取引所フェッチャ -------------
async def fetch_binance(session: ClientSession, pair: str):
    base, quote = pair.split("-")
    symbol = f"{base}{quote}".upper()
    url = f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={symbol}"
    try:
        async with session.get(url) as r:
            j = await r.json()
        bid = float(j["bidPrice"]); ask = float(j["askPrice"])
        mid = (bid + ask) / 2.0
        return mid, bid, ask, time.time()
    except Exception:
        return None

async def fetch_okx(session: ClientSession, pair: str):
    base, quote = pair.split("-")
    inst = f"{base.upper()}-{quote.upper()}"
    url = f"https://www.okx.com/api/v5/market/ticker?instId={inst}"
    try:
        async with session.get(url) as r:
            j = await r.json()
        data = j["data"][0]
        bid = float(data["bidPx"]); ask = float(data["askPx"])
        mid = (bid + ask) / 2.0
        return mid, bid, ask, time.time()
    except Exception:
        return None

async def fetch_bybit(session: ClientSession, pair: str):
    base, quote = pair.split("-")
    symbol = f"{base}{quote}".upper()
    url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}"
    try:
        async with session.get(url) as r:
            j = await r.json()
        lst = j.get("result", {}).get("list", [])
        if not lst:
            return None
        data = lst[0]
        bid = float(data["bid1Price"]); ask = float(data["ask1Price"])
        mid = (bid + ask) / 2.0
        return mid, bid, ask, time.time()
    except Exception:
        return None

async def fetch_kucoin(session: ClientSession, pair: str):
    base, quote = pair.split("-")
    symbol = f"{base.upper()}-{quote.upper()}"
    url = f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={symbol}"
    try:
        async with session.get(url) as r:
            j = await r.json()
        data = j["data"]
        bid = float(data["bestBid"]); ask = float(data["bestAsk"])
        mid = (bid + ask) / 2.0
        return mid, bid, ask, time.time()
    except Exception:
        return None

async def fetch_gate(session: ClientSession, pair: str):
    base, quote = pair.split("-")
    symbol = f"{base.upper()}_{quote.upper()}"
    url = f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={symbol}"
    try:
        async with session.get(url) as r:
            j = await r.json()
        if not isinstance(j, list) or not j:
            return None
        data = j[0]
        bid = float(data["highest_bid"]); ask = float(data["lowest_ask"])
        mid = (bid + ask) / 2.0
        return mid, bid, ask, time.time()
    except Exception:
        return None

async def fetch_mexc(session: ClientSession, pair: str):
    base, quote = pair.split("-")
    symbol = f"{base}{quote}".upper()
    url = f"https://api.mexc.com/api/v3/ticker/bookTicker?symbol={symbol}"
    try:
        async with session.get(url) as r:
            j = await r.json()
        bid = float(j["bidPrice"]); ask = float(j["askPrice"])
        mid = (bid + ask) / 2.0
        return mid, bid, ask, time.time()
    except Exception:
        return None

async def fetch_htx(session: ClientSession, pair: str):
    base, quote = pair.split("-")
    symbol = f"{base}{quote}".lower()
    url = f"https://api.huobi.pro/market/detail/merged?symbol={symbol}"
    try:
        async with session.get(url) as r:
            j = await r.json()
        tick = j["tick"]
        bid = float(tick["bid"][0]); ask = float(tick["ask"][0])
        mid = (bid + ask) / 2.0
        return mid, bid, ask, time.time()
    except Exception:
        return None

async def fetch_coinbase(session: ClientSession, pair: str):
    base, quote = pair.split("-")
    q = "USD" if quote.upper() in ("USDT", "USDC") else quote.upper()
    product = f"{base.upper()}-{q}"
    url = f"https://api.exchange.coinbase.com/products/{product}/ticker"
    try:
        async with session.get(url) as r:
            j = await r.json()
        bid = float(j["bid"]); ask = float(j["ask"])
        mid = (bid + ask) / 2.0
        return mid, bid, ask, time.time()
    except Exception:
        return None

FETCHERS = {
    "binance": fetch_binance,
    "okx": fetch_okx,
    "bybit": fetch_bybit,
    "kucoin": fetch_kucoin,
    "gate": fetch_gate,
    "mexc": fetch_mexc,
    "htx": fetch_htx,
    "coinbase": fetch_coinbase,
}

def fmt_price(p: float) -> str:
    if p >= 1000: return f"{p:,.2f}"
    if p >= 1:    return f"{p:.2f}"
    return f"{p:.6f}"

async def discord_notify(session: ClientSession, url: str, content: str):
    if not url:
        return
    try:
        async with session.post(url, json={"content": content}) as r:
            if r.status >= 300:
                txt = await r.text()
                print(f"[ERR] Discord {r.status}: {txt}")
    except Exception as e:
        print(f"[ERR] Discord error: {e}")

def jst_now_str():
    jst = timezone(timedelta(hours=9))
    return datetime.now(tz=jst).strftime("%Y-%m-%d %H:%M:%S %Z")

class Config:
    def __init__(self, path: str):
        self.path = path
        self.mtime = 0.0
        self.data = {}

    def _default(self):
        return {
            "pairs": ["BTC-USDT", "ETH-USDT", "SOL-USDT"],
            "exchanges": list(FETCHERS.keys()),
            "threshold_bps": 50.0,
            "pair_threshold_bps": {},
            "blacklist_pairs": [],
            "cooldown_sec": 300,
            "interval_sec": 3.0,
            "renotify_delta_bps": 10.0,
            "batch_size": 40,
            "max_concurrency": 20,
            "indicator": {
                "enabled": True,
                "symbol": "ETH-USDT",
                "source_exchange": "binance",
                "timeframe": "5m",
                "bb_period": 20,
                "bb_std": 2.0,
                "rsi_period": 14,
                "rsi_buy_cross": 30,
                "rsi_sell_cross": 70,
                "cooldown_sec": 1200,
                "interval_sec": 10.0,
                "webhook_url": ""
            },
            "on_deploy_message": True
        }

    def load(self):
        try:
            st = os.stat(self.path)
            if st.st_mtime <= self.mtime and self.data:
                return False
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            self.mtime = st.st_mtime
            print(f"[INFO] config reloaded from {self.path}")
            return True
        except FileNotFoundError:
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                self.data = self._default()
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                self.mtime = os.stat(self.path).st_mtime
                print(f"[INFO] default config created at {self.path}")
                return True
            except Exception as e:
                print("[ERR] failed to create default config:", e)
                self.data = self._default()
                return True
        except Exception as e:
            print("[ERR] failed to load config:", e)
            return False

    def get(self, key, default=None):
        return self.data.get(key, default)

_last_alert: Dict[Tuple[str, str, str], Tuple[float, float]] = {}
_pair_offset = 0
_auto_cache = {"mode": "", "pairs": [], "ts": 0.0}

def compute_spread_bps(mids: Dict[str, float]) -> Optional[Tuple[float, str, str, float, float]]:
    if len(mids) < 2: return None
    min_ex = min(mids, key=lambda k: mids[k])
    max_ex = max(mids, key=lambda k: mids[k])
    min_p = mids[min_ex]; max_p = mids[max_ex]
    if min_p <= 0: return None
    bps = (max_p / min_p - 1.0) * 10000.0
    return bps, min_ex, max_ex, min_p, max_p

async def fetch_all_for_pair(session: ClientSession, pair: str, exchanges: List[str]):
    tasks = []
    for ex in exchanges:
        f = FETCHERS.get(ex)
        if f: tasks.append(asyncio.create_task(f(session, pair)))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: Dict[str, Tuple[float, float, float, float]] = {}
    for ex, res in zip(exchanges, results):
        if isinstance(res, Exception) or res is None:
            continue
        out[ex] = res
    return out

async def bybit_all_pairs(session: ClientSession) -> List[str]:
    url = "https://api.bybit.com/v5/market/tickers?category=spot"
    async with session.get(url) as r:
        j = await r.json()
    items = j.get("result", {}).get("list", [])
    pairs = []
    known_quotes = {"USDT","USDC","USD","BTC","ETH","DAI","EUR","TRY","BRL"}
    for x in items:
        sym = x.get("symbol","").upper()
        matched = False
        for q in sorted(known_quotes, key=len, reverse=True):
            if sym.endswith(q) and len(sym) > len(q):
                base = sym[:-len(q)]
                pairs.append(f"{base}-{q}")
                matched = True
                break
        if not matched:
            pass
    pairs = sorted(list(dict.fromkeys(pairs)))
    return pairs

async def resolve_pairs(session: ClientSession, cfg: Config) -> List[str]:
    global _auto_cache
    raw = cfg.get("pairs", [])
    if isinstance(raw, str) and raw.upper() == "BYBIT_AUTO_ALL":
        ttl = 600.0
        if _auto_cache["mode"] != "BYBIT_AUTO_ALL" or (time.time() - _auto_cache["ts"]) > ttl:
            pairs = await bybit_all_pairs(session)
            _auto_cache = {"mode":"BYBIT_AUTO_ALL","pairs":pairs,"ts":time.time()}
            print(f"[INFO] BYBIT_AUTO_ALL resolved {len(pairs)} pairs")
        return _auto_cache["pairs"]
    if isinstance(raw, list):
        return [p.upper() for p in raw]
    return []

async def spread_loop(cfg: Config):
    global _pair_offset
    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
        if cfg.get("on_deploy_message", True):
            ts = jst_now_str()
            msg = f"🚀 デプロイ完了 / サービス起動: {ts}"
            await discord_notify(session, DISCORD_WEBHOOK_URL, msg)
            if INDICATOR_WEBHOOK_URL_ENV and INDICATOR_WEBHOOK_URL_ENV != DISCORD_WEBHOOK_URL:
                await discord_notify(session, INDICATOR_WEBHOOK_URL_ENV, msg)
        while True:
            try:
                cfg.load()
                exchanges = [e for e in cfg.get("exchanges", []) if e in FETCHERS]
                if len(exchanges) < 2:
                    print("[WARN] exchanges 2以上に設定してください。現在:", exchanges)
                default_thr = float(cfg.get("threshold_bps", 50.0))
                thr_map: Dict[str, float] = {k.upper(): float(v) for k, v in cfg.get("pair_threshold_bps", {}).items()}
                blacklist = set([p.upper() for p in cfg.get("blacklist_pairs", [])])
                interval = float(cfg.get("interval_sec", 3.0))
                batch_size = int(cfg.get("batch_size", 40))
                max_conc = int(cfg.get("max_concurrency", 20))

                pairs_all = await resolve_pairs(session, cfg)
                if not pairs_all:
                    await asyncio.sleep(interval); continue

                start = _pair_offset % len(pairs_all)
                end = start + batch_size
                if end <= len(pairs_all):
                    pairs_batch = pairs_all[start:end]
                else:
                    pairs_batch = pairs_all[start:] + pairs_all[:(end % len(pairs_all))]
                _pair_offset = end

                sem = asyncio.Semaphore(max_conc)
                results = []

                async def worker(pair):
                    nonlocal results
                    if pair in blacklist:
                        return
                    async with sem:
                        now = time.time()
                        data = await fetch_all_for_pair(session, pair, exchanges)
                        if not data or len(data) < 2:
                            return
                        mids = {ex: v[0] for ex, v in data.items()}
                        r = compute_spread_bps(mids)
                        if r is None:
                            return
                        bps, min_ex, max_ex, min_p, max_p = r
                        thr = thr_map.get(pair, default_thr)
                        if bps < thr:
                            return
                        results.append((pair, bps, min_ex, max_ex, min_p, max_p, data, thr, now))

                tasks = [asyncio.create_task(worker(p)) for p in pairs_batch]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

                for (pair, bps, min_ex, max_ex, min_p, max_p, data, thr, now) in results:
                    key = (pair, min_ex, max_ex)
                    prev = _last_alert.get(key)
                    cd = float(cfg.get("cooldown_sec", 300))
                    delta = float(cfg.get("renotify_delta_bps", 10.0))
                    should = False
                    if prev is None:
                        should = True
                    else:
                        last_bps, last_ts = prev
                        if (now - last_ts) >= cd or (bps - last_bps) >= delta:
                            should = True
                    if not should:
                        continue
                    line = " | ".join(f"{ex}:{fmt_price(v[0])}" for ex, v in sorted(data.items(), key=lambda kv: kv[1][0]))
                    msg = (
                        f"**[SPREAD] {pair} 乖離検知**\n"
                        f"{min_ex} → {max_ex} で **{bps:.1f} bps**（約 {(bps/100):.2f}% ）閾値 {thr:.1f} bps\n"
                        f"Buy @{min_ex}: {fmt_price(min_p)} / Sell @{max_ex}: {fmt_price(max_p)}\n"
                        f"全MID: {line}\n"
                        f"※手数料/出入金/サイズ影響は未控除。"
                    )
                    await discord_notify(session, DISCORD_WEBHOOK_URL, msg)
                    _last_alert[key] = (bps, now)

                await asyncio.sleep(interval)
            except Exception as e:
                print(f"[ERR] spread_loop: {e}")
                await asyncio.sleep(2.0)

# ---- Indicator (ETH BB+RSI) ----
def rsi(values: List[float], period: int) -> List[float]:
    if len(values) < period + 1:
        return []
    gains = []
    losses = []
    for i in range(1, period + 1):
        delta = values[i] - values[i-1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    rsis = [0.0] * (period)
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i-1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            r = 100.0
        else:
            rs = avg_gain / avg_loss
            r = 100.0 - (100.0 / (1.0 + rs))
        rsis.append(r)
    return rsis

def sma(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return []
    out = []
    s = sum(values[:period])
    out.append(s / period)
    for i in range(period, len(values)):
        s += values[i] - values[i-period]
        out.append(s / period)
    return out

def stddev(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return []
    out = []
    for i in range(period, len(values)+1):
        window = values[i-period:i]
        m = sum(window) / period
        var = sum((x - m) ** 2 for x in window) / period
        out.append(math.sqrt(var))
    return out

async def fetch_binance_klines(session: ClientSession, symbol: str, interval: str, limit: int = 200):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    async with session.get(url) as r:
        j = await r.json()
    closes = [float(x[4]) for x in j]
    highs = [float(x[2]) for x in j]
    lows  = [float(x[3]) for x in j]
    return closes, highs, lows

_last_touch = {"side": None, "ts": 0.0}
_prev_rsi = None
_last_signal_ts = 0.0

async def indicator_loop(cfg: Config):
    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
        global _prev_rsi, _last_touch, _last_signal_ts
        while True:
            try:
                cfg.load()
                ind = cfg.get("indicator", {})
                if not ind or not ind.get("enabled", True):
                    await asyncio.sleep(5.0)
                    continue

                symbol = ind.get("symbol", "ETH-USDT").upper()
                tf = ind.get("timeframe", "5m")
                bb_p = int(ind.get("bb_period", 20))
                bb_k = float(ind.get("bb_std", 2.0))
                rsi_p = int(ind.get("rsi_period", 14))
                rsi_buy = float(ind.get("rsi_buy_cross", 30))
                rsi_sell = float(ind.get("rsi_sell_cross", 70))
                cooldown = float(ind.get("cooldown_sec", 1200))
                interval = float(ind.get("interval_sec", 10.0))
                webhook = ind.get("webhook_url", INDICATOR_WEBHOOK_URL_ENV)

                sym = symbol.replace("-", "")
                closes, highs, lows = await fetch_binance_klines(session, sym, tf, limit=max(200, bb_p+rsi_p+10))

                if len(closes) < max(bb_p, rsi_p) + 5:
                    await asyncio.sleep(interval)
                    continue

                m = sma(closes, bb_p)
                s = stddev(closes, bb_p)
                if not m or not s:
                    await asyncio.sleep(interval); continue
                upper = [mu + bb_k * sd for mu, sd in zip(m, s)]
                lower = [mu - bb_k * sd for mu, sd in zip(m, s)]
                offset = bb_p - 1
                c_al = closes[offset:]
                h_al = highs[offset:]
                l_al = lows[offset:]
                rs = rsi(closes, rsi_p)
                if not rs:
                    await asyncio.sleep(interval); continue
                r_off = len(closes) - len(rs)
                rsi_al = [None]*r_off + rs

                i = len(c_al) - 1
                if i < 1 or len(rsi_al) < offset + i + 1:
                    await asyncio.sleep(interval); continue

                c_now = c_al[i]
                u_now = upper[i]
                l_now = lower[i]
                h_now = h_al[i]
                l0_now = l_al[i]

                touched_lower = (l0_now <= l_now)
                touched_upper = (h_now >= u_now)

                if touched_lower:
                    _last_touch = {"side": "lower", "ts": time.time()}
                elif touched_upper:
                    _last_touch = {"side": "upper", "ts": time.time()}

                r_now = rsi_al[offset + i]
                r_prev = _prev_rsi

                crossed_up = (r_prev is not None) and (r_prev < rsi_buy) and (r_now >= rsi_buy)
                crossed_down = (r_prev is not None) and (r_prev > rsi_sell) and (r_now <= rsi_sell)

                now = time.time()
                if _last_touch["side"] == "lower" and crossed_up and (now - _last_signal_ts) > cooldown:
                    msg = (
                        f"**[TECH] {symbol} 買い時サイン**\n"
                        f"BB下限タッチ後、RSIが {rsi_buy} を上抜け\n"
                        f"RSI: {r_now:.1f} / Close: {fmt_price(c_now)} / TF: {tf} / BB期間: {bb_p}"
                    )
                    await discord_notify(session, webhook, msg)
                    _last_signal_ts = now

                if _last_touch["side"] == "upper" and crossed_down and (now - _last_signal_ts) > cooldown:
                    msg = (
                        f"**[TECH] {symbol} 売り時サイン**\n"
                        f"BB上限タッチ後、RSIが {rsi_sell} を下抜け\n"
                        f"RSI: {r_now:.1f} / Close: {fmt_price(c_now)} / TF: {tf} / BB期間: {bb_p}"
                    )
                    await discord_notify(session, webhook, msg)
                    _last_signal_ts = now

                _prev_rsi = r_now
                await asyncio.sleep(interval)
            except Exception as e:
                print(f"[ERR] indicator_loop: {e}")
                await asyncio.sleep(3.0)

async def main():
    cfg = Config(DEFAULT_CONFIG_PATH)
    cfg.load()
    tasks = [
        asyncio.create_task(spread_loop(cfg)),
        asyncio.create_task(indicator_loop(cfg)),
    ]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("bye")
