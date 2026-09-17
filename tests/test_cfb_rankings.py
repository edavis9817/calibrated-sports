"""CFBD /rankings -> cfb_rankings: one metered request, joined on ids not names.

Run: pytest -q tests/test_cfb_rankings.py

Fixtures are INVENTED: schools, ids and points are placeholders, not measurements.
"""
import pytest

import config
from cfb import cfbd, cfbd_normalize, fetch, paths, schema
from jobs import ingest_cfb
from tests.test_ingest_cfb_cfbd import FakeCFBD          # the CFBD mock server


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CFBD_RESERVE", 100)
    monkeypatch.setattr(config, "CFBD_BASE", "https://cfbd.test")
    monkeypatch.setattr(fetch, "DOWNLOAD_GAP_S", 0)
    paths.ensure_dirs()
    conn = ingest_cfb.connect()
    yield conn
    conn.close()


def _payload(week=3, final=None):
    return [{"season": 2026, "seasonType": "regular", "week": week, "polls": [
        {"poll": "AP Top 25", "isFinal": final, "ranks": [
            {"rank": 1, "teamId": 111, "school": "Alpha State", "conference": "Conf A",
             "firstPlaceVotes": 40, "points": 1500},
            {"rank": 2, "teamId": 222, "school": "Beta Tech", "conference": "Conf B",
             "firstPlaceVotes": 2, "points": 1400}]},
        {"poll": "Coaches Poll", "isFinal": final, "ranks": [
            {"rank": 1, "teamId": 222, "school": "Beta Tech", "conference": "Conf B",
             "firstPlaceVotes": 30, "points": 1490},
            # no team id: cannot be joined to a team, so it is counted and dropped
            {"rank": 2, "teamId": None, "school": "Gamma A&M", "conference": "Conf C",
             "firstPlaceVotes": 0, "points": 1300}]}]}]


def test_one_week_of_polls_costs_one_request():
    reqs = cfbd.rankings(2026, 3)
    assert len(reqs) == 1
    r = reqs[0]
    assert r.dataset == "cfbd_rankings" and r.part == "regular:w3"
    assert cfbd.build_url(r.endpoint, r.param_dict()).endswith(
        "/rankings?year=2026&week=3&seasonType=regular")


def test_a_per_team_rankings_url_cannot_be_built():
    for bad in ({"year": 2026, "team": "Alpha State"}, {"year": 2026, "id": 1},
                {"year": 2026, "gameId": 1}):
        with pytest.raises(ValueError):
            cfbd.build_url("rankings", bad)


def test_polls_parse_to_rows_and_a_rank_without_a_team_id_is_dropped(store):
    norm = cfbd_normalize.cfbd_rankings(_payload(), 2026)
    assert norm.table == "cfb_rankings"
    assert len(norm.rows) == 3
    assert norm.dropped == {"rank_without_team_id": 1}
    assert dict((k, v) for k, v, _d in norm.measurements)["cfbd_rankings.polls"] == 2
    cols = schema.columns("cfb_rankings")
    row = dict(zip(cols, norm.rows[0]))
    assert (row["season"], row["week"], row["poll"], row["rank"], row["team_id"],
            row["school"], row["first_place_votes"], row["points"]) == \
        (2026, 3, "AP Top 25", 1, 111, "Alpha State", 40, 1500)


def test_a_week_of_polls_lands_in_the_store_and_the_join_is_measured(store):
    fake = FakeCFBD()
    fake.payloads[("rankings", frozenset({("year", "2026"), ("week", "3"),
                                          ("seasonType", "regular")}))] = _payload()
    store.execute("INSERT INTO cfb_teams (season, team_id, display_name, sport, src_dataset, "
                  "src_file_id, row_sha, valid_from_ts) VALUES (2026, 111, 'Alpha State Aces', "
                  "'cfb', 'teams', 1, 'x', 1.0)")
    store.commit()
    ingest_cfb.run_cfbd(store, cfbd.rankings(2026, 3), client=fake.client(store))
    ingest_cfb.run_parse_cfbd(store, {2026})
    rows = store.execute("SELECT poll, rank, team_id, school FROM cfb_rankings WHERE "
                         "valid_to_ts IS NULL ORDER BY poll, rank").fetchall()
    assert rows == [("AP Top 25", 1, 111, "Alpha State"), ("AP Top 25", 2, 222, "Beta Tech"),
                    ("Coaches Poll", 1, 222, "Beta Tech")]
    assert len(fake.metered) == 1
    ingest_cfb.measure_joins(store)
    # 222 has no 2026 teams row in this fixture, 111 does: the measurement says so
    assert store.execute("SELECT value, detail FROM cfb_measurements WHERE "
                         "key='rankings.team_ids_without_team_row' AND season=2026").fetchone() == \
        (2.0, "of 3 poll rows")


def test_a_later_poll_for_the_same_week_supersedes_the_earlier_rows(store):
    fake = FakeCFBD()
    key = ("rankings", frozenset({("year", "2026"), ("week", "3"), ("seasonType", "regular")}))
    fake.payloads[key] = _payload()
    ingest_cfb.run_cfbd(store, cfbd.rankings(2026, 3), client=fake.client(store))
    ingest_cfb.run_parse_cfbd(store, {2026})
    fake.payloads[key] = _payload(final=True)
    ingest_cfb.run_cfbd(store, cfbd.rankings(2026, 3), client=fake.client(store))
    ingest_cfb.run_parse_cfbd(store, {2026})
    current = store.execute("SELECT DISTINCT is_final FROM cfb_rankings WHERE "
                            "valid_to_ts IS NULL").fetchall()
    closed = store.execute("SELECT COUNT(*) FROM cfb_rankings WHERE "
                           "valid_to_ts IS NOT NULL").fetchone()[0]
    assert current == [(1,)] and closed == 3


def test_rankings_stay_inside_the_scope_boundary():
    from cfb import guards
    assert guards.scope_violations({"cfb_rankings": schema.TABLES["cfb_rankings"]}) == []
