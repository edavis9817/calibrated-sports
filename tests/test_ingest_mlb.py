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


# ---------------------------------------------------------------------------
# c-07: Retrosheet's statement travels with the export, and the publish tree refuses
# ---------------------------------------------------------------------------

def _store_with_one_game(mutate=None):
    con = J.connect()
    _fetch(con, bundle(mutate=mutate))
    con.close()


def test_the_export_writes_the_statement_verbatim_beside_the_tree(tmp_path):
    from jobs import export_mlb_web as E
    _store_with_one_game()
    out = tmp_path / "probe"
    E.export(str(out), [SEASON], verbose=False)
    body = (out / "mlb" / "NOTICE.txt").read_text(encoding="utf-8")
    assert body.startswith(sources.ATTRIBUTION)
    # and a re-run with nothing changed does not rewrite it
    assert E.write_notice(str(out)) is False


def test_the_publish_tree_is_refused_while_the_manifest_cannot_carry_the_statement(
        tmp_path, monkeypatch):
    from jobs import export_mlb_web as E
    _store_with_one_game()
    web = tmp_path / "web"
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", str(web))
    assert not E.contract_admits_attribution()          # today's contract: no field (A-C10)
    for dest in (web, web / "nested"):
        with pytest.raises(E.AttributionNotCarried, match="WEB_EXPORT_DIR"):
            E.export(str(dest), [SEASON], verbose=False)
    assert not web.exists(), "a refused export must write nothing, NOTICE included"
    # the same export to a SIBLING whose name merely starts with the publish tree's is
    # allowed - so the refusal above is about the destination, not a broken export, and
    # the prefix test is by path component, not by string
    E.export(str(tmp_path / "web-probe"), [SEASON], verbose=False)
    assert (tmp_path / "web-probe" / "mlb" / "manifest.json").exists()


def test_once_the_contract_has_the_field_the_manifest_carries_it_and_the_gate_opens(
        tmp_path, monkeypatch):
    import copy
    from jobs import export_mlb_web as E
    _store_with_one_game()
    assert "attribution" not in E.build(E.ro(), [SEASON])["mlb/manifest.json"]
    contract = copy.deepcopy(E.CONTRACT)
    contract["$defs"]["SportManifest"]["properties"]["attribution"] = {"type": "object"}
    monkeypatch.setattr(E, "CONTRACT", contract)
    man = E.build(E.ro(), [SEASON])["mlb/manifest.json"]
    assert man["attribution"] == sources.attribution_block()
    assert man["attribution"]["statement"] == sources.ATTRIBUTION
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", str(tmp_path / "web"))
    assert E.check_destination(str(tmp_path / "web"), man) == str(tmp_path / "web")
    with pytest.raises(E.AttributionNotCarried, match="verbatim"):
        E.check_destination(str(tmp_path / "web"),
                            {"attribution": {"statement": "Data: Retrosheet."}})


def test_the_limitation_row_describes_the_state_it_is_in():
    """c-03's row claimed 'Any export of MLB data carries the statement' - false of its own
    export (0 of 4,757 files, f-03). The row is now pinned to the contract's real state:
    when the field lands, THIS FAILS until the row is rewritten to say what is then true."""
    from jobs import export_mlb_web as E
    consequence = {lid: c for lid, _sev, _t, _s, c in J.LIMITATIONS}["mlb.attribution_required"]
    assert "Any export of MLB data carries the statement" not in consequence
    assert E.contract_admits_attribution() is False, (
        "the contract now has SportManifest.attribution - rewrite the "
        "mlb.attribution_required consequence in jobs/ingest_mlb.py to say what is true "
        "now, then update this test")
    assert consequence.startswith("NOT YET MET")
    assert "NOTICE.txt" in consequence and "WEB_EXPORT_DIR" in consequence


# ---------------------------------------------------------------------------
# c-07: one aggregation rule, not two
# ---------------------------------------------------------------------------

def _aggregates_in(source, names=None):
    """Every SQL SUM string and every builtin `sum(` call in the source (or in the named
    functions of it), by AST. Docstrings are skipped: one that NAMES the old rule is not
    code, and a text grep cannot tell the two apart."""
    import ast
    tree = ast.parse(source)
    roots = [tree] if names is None else [
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    if names is not None:
        assert {r.name for r in roots} == set(names), "a named function is missing"
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.Module)) and n.body
            and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    # attribute every hit to its enclosing TOP-LEVEL function (None at module level)
    owner = {}
    for top in tree.body:
        for n in ast.walk(top):
            owner[id(n)] = top.name if isinstance(top, ast.FunctionDef) else None
    hits = []
    for root in roots:
        for n in ast.walk(root):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                    and id(n) not in docs and "SUM(" in n.value.upper():
                hits.append(("sql", owner.get(id(n))))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "sum":
                hits.append(("sum", owner.get(id(n))))
    return sorted(hits, key=str)


def test_the_aggregation_rule_exists_once():
    """f-03: the rule lived in SQL (season_totals) AND in Python (the export). They agreed
    on 90,116 cells and nothing kept them agreeing. Both now fold through mlb.totals."""
    import inspect
    from jobs import export_mlb_web as E
    assert _aggregates_in(inspect.getsource(J), {"season_totals", "team_records"}) == []
    # The export: NO SQL aggregate anywhere, and builtin sum() only where it is not a
    # published stat total. Exact, so a new sum() anywhere in the module fails:
    #   build   x2  the P/B position classifier - g_p against g appearances, NULL read as
    #               0 on purpose, because a majority vote needs a number (finding M-4)
    #   measure x2  counting period-index collisions (finding M-1)
    assert _aggregates_in(inspect.getsource(E)) == \
        [("sum", "build")] * 2 + [("sum", "measure")] * 2
    # the checker can fail: both shapes it looks for are caught when planted
    planted = ('def season_totals(con):\n'
               '    """SUM( in a docstring is prose."""\n'
               '    return con.execute("SELECT SUM(b_h) FROM mlb_batting")\n'
               'def team_records(rows):\n'
               '    return sum(r["win"] for r in rows)\n')
    assert len(_aggregates_in(planted, {"season_totals", "team_records"})) == 2


def test_the_store_and_the_export_agree_on_every_cell_including_an_unknown_one():
    """The two consumers of the rule on one fixture with a blank cell: every total the
    export publishes equals the store's reader, and the unknown game makes BOTH null."""
    from jobs import export_mlb_web as E

    def blank(m):
        m["batting"][2]["b_h"] = ""          # swit001's VIS line
    _store_with_one_game(blank)
    files = E.build(E.ro(), [SEASON])
    ro = J.connect_ro()
    compared = 0
    for kind, cols in (("batting", schema.BAT_STATS),
                       ("pitching", schema.PIT_STATS + schema.PIT_FLAGS)):
        for row in J.season_totals(ro, SEASON, kind):
            summary = files[f"mlb/players/{row['player_id']}/summary.json"]
            reg = [t for t in summary["season_totals"] if t["season_type"] == "regular"][0]
            for c in cols:
                assert reg["stats"][c] == row[c], (row["player_id"], c)
                assert summary["career"]["stats"][c] == row[c], (row["player_id"], c)
                compared += 1
    assert compared == 3 * len(schema.BAT_STATS) + 2 * len(schema.PIT_STATS + schema.PIT_FLAGS)
    sw = files["mlb/players/swit001/summary.json"]["season_totals"][0]
    assert sw["stats"]["b_h"] is None and sw["stats"]["b_pa"] == 3


def test_a_null_is_contagious_in_the_fold():
    from mlb import totals
    assert totals.total([1, 2, 3]) == 6
    assert totals.total([1, None, 3]) is None
    assert totals.total([None, 1]) is None
    assert totals.fold_into({"a": 1}, {"a": None, "b": 2}) == {"a": None, "b": 2}
    assert totals.season_type("playoff") == "regular"
    assert totals.season_type("worldseries") == "worldseries"


# ---------------------------------------------------------------------------
# c-07: a read does not open the store for writing
# ---------------------------------------------------------------------------

def _snapshot(db):
    import hashlib
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    with open(db, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_status_totals_and_audit_leave_the_store_byte_identical(capsys, monkeypatch):
    _store_with_one_game()
    db = paths.db_path()
    before = _snapshot(db)
    real_connect = J.connect

    def no_write_connection(*a, **k):
        raise AssertionError("a read path opened the store through connect()")
    monkeypatch.setattr(J, "connect", no_write_connection)
    assert J.main([]) == 0
    assert J.main(["--totals", str(SEASON), "--player", "homa001"]) == 0
    assert J.main(["--audit"]) == 0
    assert "homa001" in capsys.readouterr().out
    assert _snapshot(db) == before
    # the snapshot can see a write: one real write through the write path moves it
    con = real_connect()
    J.measure(con, "probe.write", "x", 1)
    con.close()
    assert _snapshot(db) != before


def test_the_read_connection_refuses_to_write():
    import sqlite3
    _store_with_one_game()
    ro = J.connect_ro()
    assert ro.execute("SELECT COUNT(*) FROM mlb_batting").fetchone()[0] == 4
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("UPDATE mlb_limitations SET recorded_ts = 0")


def test_a_read_of_a_store_that_does_not_exist_refuses_and_creates_nothing(capsys):
    import os
    db = paths.db_path()
    assert not os.path.exists(db)
    assert J.main([]) == 1
    assert J.main(["--totals", str(SEASON)]) == 1
    assert "REFUSING" in capsys.readouterr().out
    assert not os.path.exists(db)


def test_a_limitation_is_re_recorded_only_when_its_text_changes(monkeypatch):
    con = J.connect()
    first = dict(con.execute("SELECT id, recorded_ts FROM mlb_limitations"))
    con.close()
    con = J.connect()
    assert dict(con.execute("SELECT id, recorded_ts FROM mlb_limitations")) == first
    con.close()
    changed = [(lid, sev, t, s, c + " (restated)") if lid == "mlb.no_current_season"
               else (lid, sev, t, s, c) for lid, sev, t, s, c in J.LIMITATIONS]
    monkeypatch.setattr(J, "LIMITATIONS", changed)
    con = J.connect()
    after = dict(con.execute("SELECT id, recorded_ts FROM mlb_limitations"))
    assert after["mlb.no_current_season"] > first["mlb.no_current_season"]
    assert {k: v for k, v in after.items() if k != "mlb.no_current_season"} == \
        {k: v for k, v in first.items() if k != "mlb.no_current_season"}
