"""MLB stats ingest (c-03): the source guard, the parser's refusals, NULL-is-unknown,
versioning by ingestion time, parts-sum-to-whole, and the contract probe.

Run: pytest -q tests/test_ingest_mlb.py

Every fixture is INVENTED and no test touches the network. The one exception to
"invented" is REAL_HEADERS: the literal header rows of Retrosheet's 2025 bundle, so the
parser is pinned to what the source actually publishes rather than to itself.
"""
import csv
import io
import zipfile

import pytest

import config
from mlb import normalize, paths, schema, sources
from jobs import ingest_mlb as J

# Copied from the 2025 bundle (fetched 2026-09-22), not generated from our schema.
REAL_HEADERS = {
    "gameinfo": "gid,visteam,hometeam,site,date,number,starttime,daynight,innings,tiebreaker,usedh,htbf,timeofgame,attendance,fieldcond,precip,sky,temp,winddir,windspeed,oscorer,forfeit,suspend,umphome,ump1b,ump2b,ump3b,umplf,umprf,wp,lp,save,gametype,vruns,hruns,wteam,lteam,line,batteries,lineups,box,pbp,season",
    "batting": "gid,id,team,b_lp,b_seq,stattype,b_pa,b_ab,b_r,b_h,b_d,b_t,b_hr,b_rbi,b_sh,b_sf,b_hbp,b_w,b_iw,b_k,b_sb,b_cs,b_gdp,b_xi,b_roe,dh,ph,pr,date,number,site,vishome,opp,win,loss,tie,gametype,box,pbp",
    "pitching": "gid,id,team,p_seq,stattype,p_ipouts,p_noout,p_bfp,p_h,p_d,p_t,p_hr,p_r,p_er,p_w,p_iw,p_k,p_hbp,p_wp,p_bk,p_sh,p_sf,p_sb,p_cs,p_pb,wp,lp,save,p_gs,p_gf,p_cg,date,number,site,vishome,opp,win,loss,tie,gametype,box,pbp",
    "allplayers": "id,last,first,bat,throw,team,g,g_p,g_sp,g_rp,g_c,g_1b,g_2b,g_3b,g_ss,g_lf,g_cf,g_rf,g_of,g_dh,g_ph,g_pr,first_g,last_g",
}
REAL_HEADERS["teamstats"] = ",".join(
    ["gid", "team"] + [f"inn{i}" for i in range(1, 29)] + ["lob", "mgr", "stattype"]
    + schema.BAT_STATS + schema.PIT_STATS + schema.FLD_STATS
    + [f"start_l{i}" for i in range(1, 10)] + [f"start_f{i}" for i in range(1, 11)]
    + ["date", "number", "site", "vishome", "opp", "win", "loss", "tie", "gametype", "box", "pbp"])

SEASON = 2025


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    """No test writes outside its own tree: pin the ROOT, not the leaf."""
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "market_log_unused.db"))
    paths.ensure_dirs()
    yield


def _csv(member, rows):
    header = REAL_HEADERS[member].split(",")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([r.get(h, "") for h in header])
    return buf.getvalue().encode("utf-8")


GAME = {"gid": "HOM202504010", "date": "20250401", "number": "0", "gametype": "regular",
        "box": "y", "pbp": "y", "site": "HOM01"}


def _bat(pid, team, opp, vh, win, **stats):
    base = {**GAME, "id": pid, "team": team, "opp": opp, "vishome": vh, "win": win,
            "loss": 1 - win, "tie": 0, "stattype": "value", "b_lp": "1", "b_seq": "1"}
    base.update({c: "0" for c in schema.BAT_STATS})
    base.update({k: str(v) for k, v in stats.items()})
    return base


def _pit(pid, team, opp, vh, win, **stats):
    base = {**GAME, "id": pid, "team": team, "opp": opp, "vishome": vh, "win": win,
            "loss": 1 - win, "tie": 0, "stattype": "value", "p_seq": "1"}
    base.update({c: "0" for c in schema.PIT_STATS})
    base.update({k: str(v) for k, v in stats.items()})
    return base


def _team(team, opp, vh, win, bat, pit):
    t = {**GAME, "team": team, "opp": opp, "vishome": vh, "win": win, "loss": 1 - win,
         "tie": 0, "stattype": "value", "lob": "5", "mgr": f"mgr{team}"}
    for c in schema.BAT_STATS:
        t[c] = str(sum(int(b.get(c) or 0) for b in bat))
    for c in schema.PIT_STATS:
        t[c] = str(sum(int(p.get(c) or 0) for p in pit))
    for c in schema.FLD_STATS:
        t[c] = "0"
    return t


def bundle(season=SEASON, *, extra_col=False, drop_member=None, mutate=None,
           runs_home=3):
    """One game, HOM beats VIS. `swit001` bats for BOTH teams (the Jansen shape)."""
    bat = [_bat("homa001", "HOM", "VIS", "h", 1, b_pa=4, b_ab=4, b_h=2, b_r=runs_home,
                b_hr=1),
           _bat("swit001", "HOM", "VIS", "h", 1, b_pa=1, b_ab=1),
           _bat("swit001", "VIS", "HOM", "v", 0, b_pa=2, b_ab=2, b_h=1),
           _bat("visb001", "VIS", "HOM", "v", 0, b_pa=4, b_ab=3, b_w=1, b_r=1)]
    pit = [_pit("homp001", "HOM", "VIS", "h", 1, p_ipouts=27, p_h=1, p_r=1, p_er=1,
                wp="1", p_gs="1", p_cg="1"),
           _pit("visp001", "VIS", "HOM", "v", 0, p_ipouts=24, p_h=2, p_r=runs_home,
                p_er=runs_home, lp="1", p_gs="1")]
    for p in pit:
        for f in ("wp", "lp", "save", "p_gs", "p_gf", "p_cg"):
            p.setdefault(f, "")
    teams = [_team("HOM", "VIS", "h", 1, bat[:2], pit[:1]),
             _team("VIS", "HOM", "v", 0, bat[2:], pit[1:])]
    gi = {**GAME, "visteam": "VIS", "hometeam": "HOM", "innings": "9", "usedh": "true",
          "vruns": "1", "hruns": str(runs_home), "wteam": "HOM", "lteam": "VIS",
          "wp": "homp001", "lp": "visp001", "season": str(season)}
    people = [{"id": p, "last": p[:4].title(), "first": "X", "bat": "R", "throw": "R",
               "team": t, "g": "1", "first_g": "20250401", "last_g": "20250401"}
              for p, t in (("homa001", "HOM"), ("swit001", "HOM"), ("swit001", "VIS"),
                           ("visb001", "VIS"), ("homp001", "HOM"), ("visp001", "VIS"))]
    for p in people:
        for c in schema.APPEARANCES:
            p.setdefault(c, "0")
    members = {"gameinfo": [gi], "teamstats": teams, "batting": bat, "pitching": pit,
               "allplayers": people}
    if mutate:
        mutate(members)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for m, rows in members.items():
            if m == drop_member:
                continue
            data = _csv(m, rows)
            if extra_col and m == "batting":
                lines = data.decode().splitlines()
                data = ("\n".join([lines[0] + ",b_new"] + [l + ",1" for l in lines[1:]])
                        + "\n").encode()
            z.writestr(f"{season}{m}.csv", data)
        z.writestr(f"{season}fielding.csv", "gid,id\n")
        z.writestr(f"{season}plays.csv", "gid\n")
    return buf.getvalue()


class FakeHTTP:
    """Stands in for httpx.Client; records every URL it is asked for."""

    def __init__(self, bodies):
        self.bodies, self.calls = bodies, []

    def get(self, url):
        self.calls.append(url)

        class R:
            pass
        r = R()
        body = self.bodies.get(url)
        r.status_code = 200 if body is not None else 404
        r.content = body if body is not None else b""
        return r


NOTICE = (b"Recipients of Retrosheet data are free to make any desired use of\n"
          b"the information ...")


def client(con, bodies):
    J.GAP_S = 0.0
    http = FakeHTTP({sources.NOTICE_URL: NOTICE, **bodies})
    return J.Client(con, http=http), http


# ---------------------------------------------------------------------------
# the source guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["https://statsapi.mlb.com/api/v1/people/660271",
                                 "https://baseballsavant.mlb.com/statcast_search",
                                 "https://www.fangraphs.com/leaders",
                                 "https://example.test/2025csvs.zip"])
def test_only_retrosheet_is_fetchable(url):
    with pytest.raises(sources.DeniedSource):
        sources.check_url(url)


def test_check_url_returns_what_it_approved():
    u = sources.season_url(2025)
    assert u == "https://www.retrosheet.org/downloads/2025/2025csvs.zip"
    assert sources.check_url(u) == u


def test_a_season_retrosheet_has_not_published_is_refused():
    with pytest.raises(ValueError, match="only after it ends"):
        sources.season_url(sources.LAST_PUBLISHED + 1)


def test_the_client_refuses_a_denied_host_before_any_request():
    con = J.connect()
    c, http = client(con, {})
    with pytest.raises(sources.DeniedSource):
        c.get("https://statsapi.mlb.com/api/v1/schedule")
    assert http.calls == []


def test_attribution_is_retrosheets_statement_verbatim():
    assert "obtained free of charge from and is copyrighted by Retrosheet" in \
        sources.ATTRIBUTION


# ---------------------------------------------------------------------------
# the parser
# ---------------------------------------------------------------------------

def test_the_real_2025_headers_parse():
    parsed = normalize.bundle(bundle(), SEASON)
    assert {m: len(rows) for m, (_t, rows, _m) in parsed.items()} == {
        "gameinfo": 1, "teamstats": 2, "batting": 4, "pitching": 2, "allplayers": 6}


def test_an_unrecognised_column_refuses_the_file():
    with pytest.raises(normalize.LayoutChanged, match="unrecognised.*b_new"):
        normalize.bundle(bundle(extra_col=True), SEASON)


def test_a_missing_member_refuses_the_file():
    with pytest.raises(normalize.LayoutChanged, match="pitching"):
        normalize.bundle(bundle(drop_member="pitching"), SEASON)


def test_a_player_on_both_teams_in_one_game_keeps_both_lines():
    _t, rows, _m = normalize.bundle(bundle(), SEASON)["batting"]
    cols = schema.columns("mlb_batting")
    both = [(r[cols.index("team")], r[cols.index("b_pa")]) for r in rows
            if r[cols.index("player_id")] == "swit001"]
    assert sorted(both) == [("HOM", 1), ("VIS", 2)]


def test_a_blank_stat_is_null_not_zero():
    def blank(m):
        m["batting"][0]["b_sb"] = ""
    _t, rows, _m = normalize.bundle(bundle(mutate=blank), SEASON)["batting"]
    cols = schema.columns("mlb_batting")
    assert rows[0][cols.index("b_sb")] is None
    assert rows[1][cols.index("b_sb")] == 0


def test_a_blank_flag_is_zero_with_a_box_score_and_null_without():
    def nobox(m):
        m["pitching"][1]["box"] = "n"
    _t, rows, _m = normalize.bundle(bundle(mutate=nobox), SEASON)["pitching"]
    cols = schema.columns("mlb_pitching")
    save, box = cols.index("save"), cols.index("box")
    assert rows[0][box] == "y" and rows[0][save] == 0
    assert rows[1][box] == "n" and rows[1][save] is None
    assert rows[0][cols.index("wp")] == 1


def test_a_flag_value_that_is_not_one_refuses():
    def weird(m):
        m["pitching"][0]["save"] = "2"
    with pytest.raises(normalize.LayoutChanged, match="save"):
        normalize.bundle(bundle(mutate=weird), SEASON)


def test_a_game_from_another_season_refuses():
    def wrong(m):
        m["gameinfo"][0]["season"] = "2024"
    with pytest.raises(normalize.LayoutChanged, match="season 2024"):
        normalize.bundle(bundle(mutate=wrong), SEASON)


# ---------------------------------------------------------------------------
# the ingest
# ---------------------------------------------------------------------------

def _fetch(con, body):
    c, http = client(con, {sources.season_url(SEASON): body})
    out = J.run_fetch(con, [SEASON], client=c, verbose=False)
    return out, http


def test_fetch_archives_the_terms_and_the_bundle_then_parses():
    con = J.connect()
    out, http = _fetch(con, bundle())
    assert http.calls[0] == sources.NOTICE_URL
    assert out[SEASON].startswith("new ")
    feeds = [r[0] for r in con.execute("SELECT feed FROM mlb_raw_files ORDER BY file_id")]
    assert feeds == ["retrosheet_notice", "retrosheet_csv"]
    assert con.execute("SELECT COUNT(*) FROM mlb_batting").fetchone()[0] == 4
    rep = J.audit(con)
    assert rep["clean"], rep["statement"]


def test_refuses_to_ingest_if_the_permission_is_gone_from_the_notice():
    con = J.connect()
    c, _h = client(con, {sources.NOTICE_URL: b"All rights reserved."})
    with pytest.raises(J.raw.FetchError, match="permission"):
        J.run_fetch(con, [SEASON], client=c, verbose=False)
    assert con.execute("SELECT COUNT(*) FROM mlb_raw_files").fetchone()[0] == 0


def test_an_unchanged_republish_changes_nothing():
    con = J.connect()
    _fetch(con, bundle())
    out, _h = _fetch(con, bundle())
    assert out[SEASON].startswith("unchanged_content")
    assert "+0 -0" in out[SEASON]
    assert con.execute("SELECT COUNT(*) FROM mlb_raw_files WHERE feed='retrosheet_csv'"
                       ).fetchone()[0] == 1


def test_a_restatement_closes_the_old_row_and_keeps_it():
    con = J.connect()
    _fetch(con, bundle(runs_home=3))
    _fetch(con, bundle(runs_home=4))
    rows = con.execute("SELECT b_r, valid_to_ts IS NULL FROM mlb_batting "
                       "WHERE player_id='homa001' ORDER BY valid_from_ts").fetchall()
    assert rows == [(3, 0), (4, 1)]
    assert J.season_totals(con, SEASON, player_id="homa001")[0]["b_r"] == 4


def test_a_total_over_an_unknown_game_is_null_not_a_partial_sum():
    def blank(m):
        m["batting"][2]["b_h"] = ""          # swit001's VIS line
    con = J.connect()
    _fetch(con, bundle(mutate=blank))
    t = J.season_totals(con, SEASON, player_id="swit001")[0]
    assert t["b_h"] is None
    assert t["b_pa"] == 3 and t["games"] == 1 and set(t["teams"].split(",")) == {"HOM", "VIS"}


def test_parts_sum_to_the_whole_and_the_check_can_fail():
    con = J.connect()
    _fetch(con, bundle())
    r = J.reconcile(con, SEASON)
    assert r["team_games"] == 2 and r["mlb_batting.team_games_joined"] == 2
    assert all(v == 0 for k, v in r.items() if "." in k and not k.endswith("_joined"))

    def off(m):
        m["teamstats"][0]["b_h"] = "9"
    con2_dir = config.STORAGE_DIR + "/other"
    config.STORAGE_DIR = con2_dir
    paths.ensure_dirs()
    con2 = J.connect()
    _fetch(con2, bundle(mutate=off))
    assert J.reconcile(con2, SEASON)["mlb_batting.b_h"] == 1


def test_a_tiebreaker_counts_toward_the_regular_season():
    """Retrosheet files Game 163 as `playoff`; MLB counts it as regular season."""
    def tiebreak(m):
        for member in ("gameinfo", "teamstats", "batting", "pitching"):
            for r in m[member]:
                r["gametype"] = "playoff"
    con = J.connect()
    _fetch(con, bundle(mutate=tiebreak))
    assert J.season_totals(con, SEASON, player_id="homa001")[0]["b_h"] == 2
    assert J.season_totals(con, SEASON, gametypes=("regular",), player_id="homa001") == []
    assert J.team_records(con, SEASON)["HOM"] == (1, 1, 0, 0)


def test_team_records_come_from_the_team_lines():
    con = J.connect()
    _fetch(con, bundle())
    assert J.team_records(con, SEASON) == {"HOM": (1, 1, 0, 0), "VIS": (1, 0, 1, 0)}


def test_parse_replays_the_archive_at_zero_requests():
    con = J.connect()
    _fetch(con, bundle())
    out = J.run_parse(con, [SEASON, 2024], verbose=False)
    assert out[2024] == "not archived"
    assert "=4" in out[SEASON] and "+0" in out[SEASON]


# ---------------------------------------------------------------------------
# the contract probe
# ---------------------------------------------------------------------------

def test_the_probe_export_validates_against_the_contract():
    from jobs import export_mlb_web as E
    con = J.connect()
    _fetch(con, bundle())
    con.close()
    files = E.export(str(config.STORAGE_DIR) + "/probe", [SEASON], dry_run=True,
                     verbose=False)
    season = files["mlb/players/swit001/2025.json"]
    # one game, two teams: two PeriodRows with ONE index and one game_id (finding M-1)
    assert [(p["team"], p["index"]) for p in season["periods"]] == [("hom", 91), ("vis", 91)]
    assert len({p["game_id"] for p in season["periods"]}) == 1
    man = files["mlb/manifest.json"]
    assert man["period_type"] == "date" and man["current"]["stale"] is True
    assert not any(k.startswith("mlb/market") for k in files)
