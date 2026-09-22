"""Unit f-05 - NFL stadium coordinates, with provenance and a measured offset per venue.

    python -m research.nfl_stadium_coords              # measure, print, write the points file
    python -m research.nfl_stadium_coords --check      # coverage and roof checks only, no fetch

Writes `feeds/nfl_stadium_points.csv`, which the NFL weather ingest reads. Every response
it reads is archived verbatim in the feeds raw tree first (invariant 2). Free, keyless:
the nflverse schedules release, two Wikidata SPARQL queries, and per venue one OSM API
or Nominatim read - at most one request a second, which is both services' policy.

Prints:
  1. coverage - every nflverse (stadium_id, stadium) pair from FIRST_SEASON, and which
     are not in `feeds/nfl_stadiums.csv`
  2. id reuse - stadium ids carrying more than one Wikidata venue (the JAX00 case)
  3. roofs - the crosswalk's structure against nflverse's per-game label on PLAYED games,
     and every disagreement
  4. coordinates - per venue: Wikidata point, its precision, the OSM footprint tagged with
     the same Wikidata id (both ways), inside or not, offset to the footprint and centroid
  5. the proxy it replaces - distance from each team's current stadium to the airport in
     nfldata's airports.csv
"""
import argparse
import csv
import io
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import polars as pl  # noqa: E402

from feeds import fetch, nfl_venues, sources  # noqa: E402
from jobs import ingest_feeds as J  # noqa: E402

FIRST_SEASON = 2016
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
OSM_API = "https://api.openstreetmap.org/api/0.6"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
AIRPORTS = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/airports.csv"
POINT_COLS = ["qid", "label", "latitude", "longitude", "precision_deg", "osm_ref",
              "inside_footprint", "offset_km", "centroid_km", "retrieved_utc"]


def games(con, client):
    rel = sources.release("nfl_schedule")
    body = client.release_asset(rel.feed, rel.repo, rel.tag, rel.asset_name(None))
    if body is None:
        raise SystemExit("nflverse schedules/games.parquet not listed - refusing")
    fetch.archive(con, rel.feed, None, rel.tag, body, suffix=".parquet.gz")
    return pl.read_parquet(io.BytesIO(body))


def coverage(g, xwalk):
    seen = g.filter(pl.col("season") >= FIRST_SEASON).select(
        "stadium_id", "stadium").unique().rows()
    missing = sorted(p for p in seen if p not in xwalk)
    unused = sorted(p for p in xwalk if p not in set(seen))
    print(f"\n1. coverage: {len(seen)} (stadium_id, name) pairs since {FIRST_SEASON}, "
          f"{len(xwalk)} in the crosswalk, {len(missing)} missing, {len(unused)} unused")
    for p in missing:
        print(f"   MISSING {p}")
    for p in unused:
        print(f"   unused  {p}")
    by_id = defaultdict(set)
    for (sid, _n), m in xwalk.items():
        by_id[sid].add(m["qid"])
    multi = {s: sorted(q) for s, q in by_id.items() if len(q) > 1}
    print(f"\n2. stadium ids carrying more than one venue: {len(multi)}")
    for s, q in sorted(multi.items()):
        names = [n for (sid, n), m in xwalk.items() if sid == s]
        print(f"   {s}: {q}  names {names}")
    return missing


def roofs(g, xwalk):
    """The structure against nflverse's per-game label, played games only."""
    played = g.filter(pl.col("home_score").is_not_null(), pl.col("season") >= FIRST_SEASON)
    labels = defaultdict(lambda: defaultdict(int))
    for sid, name, roof in played.select("stadium_id", "stadium", "roof").iter_rows():
        labels[(sid, name)][roof or ""] += 1
    future = g.filter(pl.col("home_score").is_null())
    flabels = defaultdict(lambda: defaultdict(int))
    for sid, name, roof in future.select("stadium_id", "stadium", "roof").iter_rows():
        flabels[(sid, name)][roof or ""] += 1
    agree = {"open_air": {"outdoors"}, "fixed": {"dome", "closed"},
             "retractable": {"open", "closed"}}
    print("\n3. roofs: crosswalk structure vs nflverse per-game label")
    bad = []
    for key, m in sorted(xwalk.items()):
        for which, lab in (("played", labels.get(key, {})), ("scheduled", flabels.get(key, {}))):
            off = {k: v for k, v in lab.items() if k not in agree[m["roof_type"]]}
            if off:
                bad.append((key, which, m["roof_type"], dict(off)))
                print(f"   {key[0]} {key[1]:34} {m['roof_type']:11} ({m['roof_source']}) "
                      f"{which}: nflverse says {dict(off)}")
    # "nflverse_history" is a claim about the VENUE, so it is proven by any played game
    # at that Wikidata item, whatever name nflverse used that season.
    by_qid = defaultdict(set)
    for key, lab in labels.items():
        if key in xwalk:
            by_qid[xwalk[key]["qid"]].update(k for k, v in lab.items() if v)
    derived = [k for k, m in xwalk.items() if m["roof_source"] == "nflverse_history"]
    unproven = [k for k in derived
                if not by_qid.get(xwalk[k]["qid"])
                or not by_qid[xwalk[k]["qid"]] <= agree[xwalk[k]["roof_type"]]]
    print(f"   {len(bad)} (pair, played|scheduled) cells disagree; "
          f"{len(derived)} rows claim nflverse_history, {len(unproven)} "
          f"that venue's played games do not prove: {unproven}")
    return bad


def wikidata(con, client, qids):
    q = ("SELECT ?item ?itemLabel ?lat ?lon ?prec WHERE { VALUES ?item { %s } "
         "?item p:P625 ?st . ?st psv:P625 ?v . ?v wikibase:geoLatitude ?lat ; "
         "wikibase:geoLongitude ?lon ; wikibase:geoPrecision ?prec . "
         "SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\". } }"
         % " ".join(f"wd:{x}" for x in sorted(qids)))
    body = client.get("nfl_venue_wikidata", WIKIDATA_SPARQL, {"query": q, "format": "json"})
    fetch.archive(con, "nfl_venue_wikidata", None, WIKIDATA_SPARQL, body, kind="json")
    out = defaultdict(list)
    for b in json.loads(body)["results"]["bindings"]:
        qid = b["item"]["value"].rsplit("/", 1)[1]
        out[qid].append({"label": b["itemLabel"]["value"], "lat": float(b["lat"]["value"]),
                         "lon": float(b["lon"]["value"]), "prec": float(b["prec"]["value"])})
    return out


def osm_ids(con, client, qids):
    """{qid: [(type, id)]} from Wikidata P402 (OSM relation) and P10689 (OSM way)."""
    q = ("SELECT ?item ?rel ?way WHERE { VALUES ?item { %s } "
         "OPTIONAL { ?item wdt:P402 ?rel } OPTIONAL { ?item wdt:P10689 ?way } }"
         % " ".join(f"wd:{x}" for x in sorted(qids)))
    body = client.get("nfl_venue_wikidata", WIKIDATA_SPARQL, {"query": q, "format": "json"})
    fetch.archive(con, "nfl_venue_wikidata", "osm_ids", WIKIDATA_SPARQL, body, kind="json")
    out = defaultdict(set)
    for b in json.loads(body)["results"]["bindings"]:
        qid = b["item"]["value"].rsplit("/", 1)[1]
        if b.get("rel"):
            out[qid].add(("relation", int(b["rel"]["value"])))
        if b.get("way"):
            out[qid].add(("way", int(b["way"]["value"])))
    return {k: sorted(v) for k, v in out.items()}


def stitch(segments):
    """Join way segments end to end into closed rings; an unclosable chain is dropped."""
    segs = [list(s) for s in segments if len(s) >= 2]
    rings = []
    while segs:
        ring = segs.pop(0)
        grown = True
        while ring[0] != ring[-1] and grown:
            grown = False
            for i, s in enumerate(segs):
                if s[0] == ring[-1]:
                    ring += s[1:]
                elif s[-1] == ring[-1]:
                    ring += s[::-1][1:]
                elif s[-1] == ring[0]:
                    ring = s[:-1] + ring
                elif s[0] == ring[0]:
                    ring = s[::-1][:-1] + ring
                else:
                    continue
                segs.pop(i)
                grown = True
                break
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def osm_api_footprint(con, client, qid, etype, eid):
    """(tags, rings) for one OSM element from the main API, archived first."""
    url = f"{OSM_API}/{etype}/{eid}/full.json"
    try:
        body = client.get("nfl_venue_osm", url)
    except fetch.FetchError as e:
        print(f"   {qid}: {etype}/{eid} not fetched ({e})")
        return None, []
    fetch.archive(con, "nfl_venue_osm", qid, url, body, kind="json")
    els = json.loads(body)["elements"]
    nodes = {e["id"]: (e["lat"], e["lon"]) for e in els if e["type"] == "node"}
    ways = {e["id"]: e for e in els if e["type"] == "way"}
    me = next((e for e in els if e["type"] == etype and e["id"] == eid), None)
    if me is None:
        return None, []
    if etype == "way":
        return me.get("tags", {}), stitch([[nodes[n] for n in me["nodes"] if n in nodes]])
    outer = [ways[m["ref"]] for m in me.get("members", [])
             if m["type"] == "way" and m.get("role") in ("outer", "") and m["ref"] in ways]
    return me.get("tags", {}), stitch([[nodes[n] for n in w["nodes"] if n in nodes]
                                       for w in outer])


def nominatim_footprint(con, client, qid, label, lat, lon):
    """[(ref, tags, rings)] for search hits near the point - only those whose own
    `wikidata` tag names this item, so a same-named stadium elsewhere cannot answer."""
    d = 0.05
    params = {"q": label, "format": "jsonv2", "polygon_geojson": 1, "extratags": 1,
              "limit": 10, "bounded": 1,
              "viewbox": f"{lon - d},{lat + d},{lon + d},{lat - d}"}
    try:
        body = client.get("nfl_venue_nominatim", NOMINATIM, params)
    except fetch.FetchError as e:
        print(f"   {qid}: nominatim failed ({e})")
        return []
    fetch.archive(con, "nfl_venue_nominatim", qid, NOMINATIM, body, kind="json")
    out = []
    for h in json.loads(body):
        tags = h.get("extratags") or {}
        g = h.get("geojson") or {}
        polys = ([g["coordinates"]] if g.get("type") == "Polygon"
                 else g.get("coordinates", []) if g.get("type") == "MultiPolygon" else [])
        rings = [[(y, x) for x, y in poly[0]] for poly in polys if poly]
        if rings:
            out.append((f"{h.get('osm_type')}/{h.get('osm_id')}", tags, rings))
    return out


def measure_points(con, client, xwalk, keep=None):
    """`keep`: {qid: row} already measured, carried over untouched - but only while
    Wikidata still gives the SAME point, because an offset measured for a coordinate that
    has since moved describes a point nobody is using."""
    keep = keep or {}
    qids = sorted({m["qid"] for m in xwalk.values()})
    wd = wikidata(con, client, qids)
    ids = osm_ids(con, client, qids)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    rows = []
    print(f"\n4. coordinates: {len(qids)} venues")
    for qid in qids:
        pts = wd.get(qid) or []
        k = keep.get(qid)
        if (k and k.get("offset_km") is not None and len(pts) == 1
                and (k["latitude"], k["longitude"]) == (pts[0]["lat"], pts[0]["lon"])):
            rows.append({c: k.get(c) for c in POINT_COLS})
            print(f"   {qid:10} {k['label'][:30]:30} kept: offset {k['offset_km']:.3f} km "
                  f"(measured {k['retrieved_utc']})")
            continue
        if len(pts) != 1:
            # Two P625 values is two answers to one question; none is no answer.
            print(f"   {qid}: {len(pts)} Wikidata coordinates - not used")
            rows.append({"qid": qid, "label": pts[0]["label"] if pts else "",
                         "retrieved_utc": now})
            continue
        p = pts[0]
        # Candidates in order of how the link was made. A name-search hit must carry THIS
        # item in its own `wikidata` tag, or it is a coincidence of names.
        cands, rejected = [], []
        for etype, eid in ids.get(qid, []):
            tags, rings = osm_api_footprint(con, client, qid, etype, eid)
            cands.append((f"{etype}/{eid} via wikidata", tags or {}, rings))
        if not any(c[2] for c in cands):
            cands += [(f"{ref} via nominatim", t, r) for ref, t, r in
                      nominatim_footprint(con, client, qid, p["label"], p["lat"], p["lon"])]
        best = None
        for ref, tags, rings in cands:
            if not rings:
                continue
            tagged = tags.get("wikidata")
            if tagged is None and ref.endswith("via wikidata"):
                # Wikidata names this exact element by id (P402/P10689) and the element
                # names nothing back. That is a deliberate link, one way - kept, and said.
                ref += " (one-way)"
            elif tagged != qid:
                # A name search hit must name this item itself, and ANY element naming a
                # different item is refused, whichever way it was found.
                rejected.append(f"{ref} tagged {tagged}")
                continue
            inside, off, cen = nfl_venues.footprint_offset(p["lat"], p["lon"], rings)
            kind = tags.get("leisure") or tags.get("building") or "?"
            cand = (off, cen, inside, f"{ref}:{kind}")
            best = cand if best is None or cand[:2] < best[:2] else best
        if rejected:
            print(f"   {qid}: rejected, wikidata tag does not name this item: {rejected}")
        row = {"qid": qid, "label": p["label"], "latitude": p["lat"], "longitude": p["lon"],
               "precision_deg": p["prec"], "retrieved_utc": now}
        if best:
            off, cen, inside, ref = best
            row.update(osm_ref=ref, inside_footprint=int(inside), offset_km=round(off, 3),
                       centroid_km=round(cen, 3))
        rows.append(row)
        print(f"   {qid:10} {p['label'][:30]:30} {p['lat']:.5f},{p['lon']:.5f} "
              f"prec {p['prec']:.6f}  "
              + (f"{ref:34} inside={int(inside)} offset {off:.3f} km  centroid {cen:.3f} km"
                 if best else "NO OSM FOOTPRINT - offset unmeasured, venue not usable"))
        time.sleep(1.0)
    return rows


def write_points(rows, path=None):
    path = nfl_venues.POINTS if path is None else path
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=POINT_COLS, lineterminator="\n")
        w.writeheader()
        for r in sorted(rows, key=lambda r: int(r["qid"][1:])):
            w.writerow({c: r.get(c, "") for c in POINT_COLS})


def airports(con, client, g, xwalk, points):
    body = client.get("nfl_airports", AIRPORTS)
    fetch.archive(con, "nfl_airports", None, AIRPORTS, body, suffix=".csv.gz")
    ap = {r["team"]: r for r in csv.DictReader(io.StringIO(body.decode()))}
    latest = (g.filter(pl.col("game_type") == "REG", pl.col("location") == "Home")
              .sort("season", "week").group_by("home_team").last())
    print("\n5. the proxy it replaces: team's latest regular-season home stadium vs airport")
    ds = []
    for team, sid, name in latest.select("home_team", "stadium_id", "stadium").iter_rows():
        m = xwalk.get((sid, name))
        p = points.get(m["qid"]) if m else None
        a = ap.get(team)
        if not (p and a and p.get("latitude") is not None):
            print(f"   {team}: not comparable (airport {bool(a)}, point {bool(p)})")
            continue
        d = nfl_venues.haversine_km(p["latitude"], p["longitude"],
                                    float(a["latitude"]), float(a["longitude"]))
        ds.append(d)
        print(f"   {team:4} {name[:30]:30} {a['airport']:4} {d:6.1f} km")
    ds.sort()
    if ds:
        print(f"   n={len(ds)} median {ds[len(ds) // 2]:.1f} km  min {ds[0]:.1f}  max {ds[-1]:.1f}")
    return ds


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="coverage and roofs only")
    ap.add_argument("--fill", action="store_true",
                    help="re-query only venues whose offset is not yet measured")
    a = ap.parse_args(argv)
    from feeds import paths
    paths.ensure_dirs()
    con = J.connect()
    fetch.GAP_S = 1.1        # OSM API and Nominatim usage policies: <= 1 request/second
    client = fetch.Client(con)
    xwalk = nfl_venues.load_crosswalk()
    g = games(con, client)
    missing = coverage(g, xwalk)
    roofs(g, xwalk)
    if a.check:
        return 1 if missing else 0
    rows = measure_points(con, client, xwalk,
                          keep=nfl_venues.load_points() if a.fill else None)
    usable = [r for r in rows if r.get("offset_km") not in (None, "")]
    if not usable:
        raise SystemExit("no venue has a measured offset - refusing to write an empty file")
    write_points(rows)
    print(f"\nwrote {nfl_venues.POINTS}: {len(rows)} venues, {len(usable)} with a measured "
          f"offset")
    airports(con, client, g, xwalk, nfl_venues.load_points())
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
