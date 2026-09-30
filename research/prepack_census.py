"""c-26 Step 0: are Kalshi's NFL parlay pre-packs on disk at all?

    python -m research.prepack_census --db D:/calibrated-sports/data/market_log.db \
        --board D:/calibrated-sports/data/board_019.db \
        --raw D:/calibrated-sports/data/raw/kalshi \
        --out research/prepack_census.json [--probe D:/temp/c26_probe]

Pre-registered at docs/C26-parlay-prepacks-preregistration.md. Read-only:
market_log.db and board_019.db are opened mode=ro, raw shards are only read.
`--probe` makes a handful of public, unauthenticated GETs against Kalshi (no
credits, no money) and keeps every response verbatim under the probe directory.

Four questions, in the order that separates the causes:
  1. store      - do pre-pack rows exist in markets / quotes / depth / trades?
  2. board      - does /series list the pre-pack series, and did a series_ticker
                  fetch return any markets (brief 019's snapshot)?
  3. raw        - does ANY archived response mention a pre-pack ticker? The raw
                  records carry no request params, so an empty markets response
                  for a pre-pack series cannot be told apart from any other empty
                  response; a pre-pack TICKER in a markets/orderbooks payload can.
  4. probe      - where does Kalshi list pre-pack markets, if not by series?
The hard exit (>= 8 games and >= 50 packs quoted two-sided) is evaluated and
printed; exit code 0 means the census ran, not that it passed.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
import time
import zlib

PREFIX_LO, PREFIX_HI = "KXNFLPREPACK", "KXNFLPREPACL"      # index range scan
# Every series brief 019's /series snapshot titled "MVE NFL ..." or "NFL COMBO".
PACK_RE = re.compile(r"KXNFLPREPACK[A-Z0-9]*|KXMVENFL[A-Z0-9]*|KXNFLCOMBO[A-Z0-9]*")
MIN_GAMES, MIN_PACKS = 8, 50                                # pre-registered exit


def ro(path: str) -> sqlite3.Connection:
    uri = "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30)


def store_census(db: str) -> dict:
    c = ro(db)
    q = lambda s, *a: c.execute(s, a).fetchall()
    out = {}
    out["markets_prefix"] = q(
        "select count(*) from markets where market_id>=? and market_id<?",
        PREFIX_LO, PREFIX_HI)[0][0]
    out["markets_any_spelling"] = q(
        "select count(*) from markets where upper(market_id) like '%PREPACK%'"
        " or upper(market_id) like '%SGP%' or upper(market_id) like '%MVE%'"
        " or upper(market_id) like '%COMBO%' or lower(title) like '%parlay%'"
        " or lower(market_type)='parlay'")[0][0]
    out["markets_by_type"] = q(
        "select venue, market_type, count(*) from markets"
        " where venue='kalshi' group by 1,2 order by 3 desc")
    out["kalshi_series_in_markets"] = q(
        "select substr(market_id,1,instr(market_id,'-')-1) s, count(*) from markets"
        " where venue='kalshi' group by s order by 2 desc")
    for tbl in ("quotes", "market_depth", "market_trades"):
        out[f"{tbl}_prefix"] = q(
            f"select count(*) from {tbl} where venue='kalshi'"
            " and market_id>=? and market_id<?", PREFIX_LO, PREFIX_HI)[0][0]
    out["two_sided_packs"] = q(
        "select count(distinct market_id), count(distinct event_id) from quotes"
        " where venue='kalshi' and market_id>=? and market_id<?"
        " and best_bid>0 and best_ask<1", PREFIX_LO, PREFIX_HI)[0]
    out["unmapped_reasons_parlay"] = q(
        "select unmapped_reason, count(*) from market_outcome"
        " where unmapped_reason like 'parlay%' group by 1")
    out["kalshi_quote_ts_span"] = q(
        "select min(ts), max(ts) from quotes where venue='kalshi'")[0]
    out["kalshi_markets_first_seen_span"] = q(
        "select min(first_seen), max(last_seen) from markets where venue='kalshi'")[0]
    return out


def board_census(board: str) -> dict:
    c = ro(board)
    q = lambda s, *a: c.execute(s, a).fetchall()
    rows = q("select ticker, title, frequency, fee_type, fee_multiplier, tracked,"
             " snapshot_ts from series")
    series = [r for r in rows if PACK_RE.fullmatch(r[0] or "")]
    fetched = {r[0]: r for r in q("select series, status, pages, n_events, n_markets,"
                                  " note, fetched_ts from fetch_log")}
    return {
        "series": [dict(zip(("ticker", "title", "frequency", "fee_type",
                             "fee_multiplier", "tracked", "snapshot_ts"), r))
                   for r in series],
        "fetch_log": {s[0]: dict(zip(("series", "status", "pages", "n_events",
                                      "n_markets", "note", "fetched_ts"),
                                     fetched[s[0]]))
                      for s in series if s[0] in fetched},
        "markets_rows": q("select count(*) from markets where series in (%s)"
                          % ",".join("?" * len(series)), *[s[0] for s in series])[0][0]
                        if series else 0,
    }


def members(path: str):
    """Yield decoded lines member by member; a torn member is counted, not fatal."""
    data = open(path, "rb").read()
    bad = 0
    while data:
        d = zlib.decompressobj(31)
        try:
            chunk = d.decompress(data)
        except zlib.error:
            bad += 1
            nxt = data.find(b"\x1f\x8b\x08", 1)       # skip to the next member header
            if nxt < 0:
                break
            data = data[nxt:]
            continue
        for line in chunk.decode("utf-8", "replace").splitlines():
            yield line, 0
        data = d.unused_data
    if bad:
        yield None, bad


EP_RE = re.compile(r'^\{"ts":([0-9.]+),"endpoint":"([^"]+)"')


def raw_census(raw: str) -> dict:
    shards = sorted(glob.glob(os.path.join(raw, "*", "*.jsonl.gz")))
    by_ep, hits_ep, tickers, torn = {}, {}, set(), 0
    latest_series, latest_series_ts = None, -1.0
    for p in shards:
        for line, bad in members(p):
            if line is None:
                torn += bad
                continue
            m = EP_RE.match(line)
            ep = m.group(2) if m else "?"
            by_ep[ep] = by_ep.get(ep, 0) + 1
            found = PACK_RE.findall(line)
            if not found:
                continue
            hits_ep[ep] = hits_ep.get(ep, 0) + 1
            if ep == "series":
                ts = float(m.group(1))
                if ts > latest_series_ts:
                    latest_series_ts, latest_series = ts, line
            else:
                # a pre-pack MARKET ticker has an event suffix: SERIES-...
                tickers.update(t for t in re.findall(
                    r'"ticker":"((?:KXNFLPREPACK|KXMVENFL|KXNFLCOMBO)[^"]*-[^"]*)"', line))
    fees = []
    if latest_series:
        rec = json.loads(latest_series)
        for s in rec.get("payload", {}).get("series", []):
            if PACK_RE.fullmatch(s.get("ticker", "")):
                fees.append({k: s.get(k) for k in
                             ("ticker", "title", "frequency", "fee_type", "fee_multiplier")})
    return {
        "shards": len(shards),
        "days": sorted({os.path.basename(os.path.dirname(p)) for p in shards}),
        "lines_by_endpoint": by_ep,
        "lines_mentioning_pack_by_endpoint": hits_ep,
        "pack_market_tickers_seen": sorted(tickers),
        "torn_members": torn,
        "latest_series_ts": latest_series_ts if latest_series else None,
        "latest_series_pack_fees": fees,
    }


def probe(base: str, outdir: str, series: list[str]) -> dict:
    import httpx
    os.makedirs(outdir, exist_ok=True)
    calls = []
    for s in series:
        calls += [(f"series_{s}", f"{base}/series/{s}", {}),
                  (f"markets_{s}", f"{base}/markets",
                   {"series_ticker": s, "limit": 200}),
                  (f"events_{s}", f"{base}/events",
                   {"series_ticker": s, "limit": 200})]
        calls.append((f"open_markets_{s}", f"{base}/markets",
                      {"series_ticker": s, "status": "open", "limit": 200}))
    calls.append(("mve_collections_nfl", f"{base}/multivariate_event_collections",
                  {"limit": 200}))
    res = {}
    with httpx.Client(timeout=30) as cl:
        # Every pack market last season, paged, for a volume census: whether the
        # packs ever TRADED decides whether a historical candle study is worth
        # scoping. Pages are kept verbatim like every other probe response.
        for s in series:
            rows, cursor, pages = [], None, 0
            while pages < 40:
                p = {"series_ticker": s, "limit": 1000}
                if cursor:
                    p["cursor"] = cursor
                r = cl.get(f"{base}/markets", params=p)
                pages += 1
                with open(os.path.join(outdir, f"all_markets_{s}_p{pages}.json"), "w",
                          encoding="utf-8") as f:
                    f.write(r.text)
                j = r.json() if r.status_code == 200 else {}
                rows += j.get("markets", [])
                cursor = j.get("cursor")
                time.sleep(0.5)
                if not cursor or not j.get("markets"):
                    break
            vol = sorted(float(m.get("volume_fp") or 0) for m in rows)
            res[f"all_markets_{s}"] = {
                "pages": pages, "hit_page_cap": pages >= 40, "n_markets": len(rows),
                "n_events": len({m.get("event_ticker") for m in rows}),
                "status": dict(__import__("collections").Counter(
                    m.get("status") for m in rows)),
                "close_min": min((m.get("close_time") or "" for m in rows), default=None),
                "close_max": max((m.get("close_time") or "" for m in rows), default=None),
                "n_volume_gt0": sum(v > 0 for v in vol),
                "volume_total": sum(vol),
                "volume_median": vol[len(vol) // 2] if vol else None,
                "volume_p90": vol[int(len(vol) * 0.9)] if vol else None,
                "volume_max": vol[-1] if vol else None,
            }
        for name, url, params in calls:
            r = cl.get(url, params=params)
            body = r.text
            with open(os.path.join(outdir, name + ".json"), "w", encoding="utf-8") as f:
                f.write(body)
            info = {"status": r.status_code, "bytes": len(body)}
            try:
                j = r.json()
                for key in ("markets", "events", "multivariate_contracts"):
                    if isinstance(j.get(key), list):
                        info[f"n_{key}"] = len(j[key])
                if "series" in j and isinstance(j["series"], dict):
                    info["fee_type"] = j["series"].get("fee_type")
                    info["fee_multiplier"] = j["series"].get("fee_multiplier")
                if "multivariate_contracts" in j:
                    nfl = [c for c in j["multivariate_contracts"]
                           if "NFL" in json.dumps(c).upper()]
                    info["n_nfl_collections"] = len(nfl)
                    info["nfl_collection_tickers"] = [
                        c.get("collection_ticker") for c in nfl][:40]
            except Exception as e:                       # noqa: BLE001
                info["parse_error"] = repr(e)
            res[name] = info
            time.sleep(0.5)                              # stay far below the 429 line
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--board", required=True)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--probe")
    ap.add_argument("--base", default="https://api.elections.kalshi.com/trade-api/v2")
    a = ap.parse_args(argv)

    t0 = time.time()
    res = {"run_ts": t0, "store": store_census(a.db), "board": board_census(a.board)}
    print("store  ", json.dumps({k: v for k, v in res["store"].items()
                                 if not k.startswith("kalshi_series")}, default=str))
    print("board  ", len(res["board"]["series"]), "pre-pack/MVE series;",
          {k: (v["n_events"], v["n_markets"]) for k, v in res["board"]["fetch_log"].items()})
    res["raw"] = raw_census(a.raw)
    r = res["raw"]
    if r["shards"] == 0 or not r["lines_by_endpoint"]:
        print("REFUSE: raw scan read nothing - a zero here would be a proxy, not a count")
        return 2
    print("raw    ", r["shards"], "shards", r["days"][0], "->", r["days"][-1],
          "lines", r["lines_by_endpoint"], "pack-mentions", r["lines_mentioning_pack_by_endpoint"],
          "pack tickers", len(r["pack_market_tickers_seen"]), "torn", r["torn_members"])
    print("fees   ", r["latest_series_pack_fees"])
    if a.probe:
        series = [s["ticker"] for s in res["board"]["series"]]
        res["probe"] = probe(a.base, a.probe, series)
        for k, v in res["probe"].items():
            print("probe  ", k, v)

    n_packs, n_games = res["store"]["two_sided_packs"]
    res["hard_exit"] = {"two_sided_packs": n_packs, "two_sided_events": n_games,
                        "min_packs": MIN_PACKS, "min_games": MIN_GAMES,
                        "fires": n_packs < MIN_PACKS or n_games < MIN_GAMES}
    print("exit   ", res["hard_exit"])
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1, default=str)
    print(f"wrote {a.out} in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
