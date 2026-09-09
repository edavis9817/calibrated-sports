"""nflverse ingest tests. Run: pytest -q

The failure this whole brief exists to prevent is silent: nflverse applies stat
corrections retroactively, so a mirror that overwrites turns every past backtest
into a claim you can no longer check. Everything below is about keeping that
visible - versions that accumulate, an older version that stays byte-identical,
and a tier boundary that raises instead of handing back nulls in October.
"""
import io
import os
import time

import pytest

import config
import nflverse
import queries
import store
from jobs import ingest_nflverse, rotate_raw


def _parquet(rows):
    """Build a real parquet file in memory, so the normalizers are exercised
    against the format they will actually see."""
    import polars as pl
    buf = io.BytesIO()
    pl.DataFrame(rows).write_parquet(buf)
    return buf.getvalue()


def weekly_rows(season=2024, receptions=(4, 6, 8), player="00-0000001"):
    return [{"player_id": player, "player_display_name": "Test Player",
             "position": "WR", "team": "DET", "opponent_team": "CHI",
             "season": season, "week": i + 1, "season_type": "REG",
             "receptions": float(r), "targets": float(r) + 2,
             "receiving_yards": float(r) * 12, "receiving_tds": 0.0,
             "target_share": 0.25, "carries": 0.0, "rushing_yards": 0.0,
             "rushing_tds": 0.0, "attempts": 0.0, "completions": 0.0,
             "passing_yards": 0.0, "passing_tds": 0.0,
             "passing_interceptions": 0.0, "fantasy_points_ppr": float(r)}
            for i, r in enumerate(receptions)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    store.reset_disk_cache()
    yield tmp_path
    store.reset_disk_cache()


class FakeUpstream(dict):
    """A controllable nflverse. `set(dataset, season, bytes)` publishes; a
    dataset with nothing published 404s the way a pre-season file does."""

    def set(self, dataset, season, data):
        self[(dataset, season)] = data

    def fetch(self, ds, season=None, client=None):
        import hashlib
        key = (ds.name, season)
        if key not in self:
            raise nflverse.NotPublished(f"{ds.name} {season}")
        return self[key], hashlib.sha256(self[key]).hexdigest()


@pytest.fixture
def fake_upstream(monkeypatch):
    up = FakeUpstream()
    monkeypatch.setattr(nflverse, "fetch", up.fetch)
    monkeypatch.setattr(ingest_nflverse.nflverse, "fetch", up.fetch)
    return up


# --- the source registry -----------------------------------------------------

def test_weekly_stats_points_at_the_maintained_release():
    """The `player_stats` release froze on 2025-05-07 with no 2025/2026 assets.
    Pointing there means receptions, targets and carries silently stop at 2024
    while every job keeps reporting healthy - so pin the release explicitly."""
    ds = nflverse.DATASETS["weekly_stats"]
    assert ds.release == "stats_player"
    assert "stats_player_week_{season}" in ds.filename
    assert ds.url(2024).endswith(
        "/releases/download/stats_player/stats_player_week_2024.parquet")


def test_2019_needs_no_special_case_on_the_right_release():
    """The missing-2019 file is an artifact of the frozen release, not of
    nflverse. On stats_player, 2019 is addressed like any other season."""
    ds = nflverse.DATASETS["weekly_stats"]
    assert ds.url(2019) == ds.url(2024).replace("2024", "2019")


def test_contracts_uses_the_filename_that_exists():
    assert nflverse.DATASETS["contracts"].url().endswith("historical_contracts.parquet")
    assert not nflverse.DATASETS["contracts"].url().endswith("/contracts.parquet")


# --- the tier boundary -------------------------------------------------------

def test_participation_fields_raise_in_live_context():
    """The acceptance case. participation does not refresh in-season, so a
    feature reading `route` works in April against 2024 and is silently empty in
    October - and nulls look like a quiet player, not like a bug."""
    with pytest.raises(nflverse.TierViolation) as e:
        nflverse.require_live("receptions", "route")
    assert "route" in str(e.value)
    assert "participation" in str(e.value)

    for f in ("was_pressure", "defense_man_zone_type", "defense_coverage_type",
              "offense_players"):
        with pytest.raises(nflverse.TierViolation):
            nflverse.require_live(f)


def test_live_fields_pass_the_guard():
    assert nflverse.require_live("receptions", "targets", "carries",
                                 "is_play_action", "offense_snaps") is True


def test_every_field_resolves_to_exactly_one_tier():
    for f in nflverse.live_fields():
        assert nflverse.tier_of(f) == nflverse.LIVE
    for f in nflverse.offseason_fields():
        assert nflverse.tier_of(f) == nflverse.OFFSEASON
    assert not set(nflverse.live_fields()) & set(nflverse.offseason_fields())


# --- ingest ------------------------------------------------------------------

def test_ingest_is_idempotent(env, fake_upstream):
    """Acceptance: two runs produce identical row counts."""
    ds = nflverse.DATASETS["weekly_stats"]
    fake_upstream.set("weekly_stats", 2024, _parquet(weekly_rows()))

    first = ingest_nflverse.ingest_one(ds, 2024, version="2026-09-09")
    second = ingest_nflverse.ingest_one(ds, 2024, version="2026-09-10")

    assert first["status"] == "new" and first["rows"] == 3
    assert second["status"] == "unchanged" and second["rows"] == 0
    with store.db() as c:
        assert c.execute("SELECT COUNT(*) FROM nfl_player_week").fetchone()[0] == 3
        assert c.execute("SELECT COUNT(DISTINCT data_version) "
                         "FROM nfl_player_week").fetchone()[0] == 1


def test_unchanged_upstream_records_the_check_not_a_copy(env, fake_upstream):
    """A daily pull of 26 unchanged seasons must not be 26 new archive copies -
    that is ~265GB/year and it buries the real corrections."""
    ds = nflverse.DATASETS["weekly_stats"]
    fake_upstream.set("weekly_stats", 2024, _parquet(weekly_rows()))
    ingest_nflverse.ingest_one(ds, 2024, version="2026-09-09")
    before = store.versions("weekly_stats", 2024)[0]

    time.sleep(0.01)
    ingest_nflverse.ingest_one(ds, 2024, version="2026-09-10")
    after = store.versions("weekly_stats", 2024)

    assert len(after) == 1                       # no second version
    assert after[0][8] > before[8]               # but last_checked_ts moved
    files = os.listdir(os.path.join(config.RAW_DIR, "nflverse"))
    assert files == ["2026-09-09"]


def test_week_scopes_the_normalize_step(env, fake_upstream):
    """Acceptance: --week 3 touches only week 3. nflverse publishes
    season-grained files, so this can only ever be a normalize-time filter."""
    ds = nflverse.DATASETS["weekly_stats"]
    fake_upstream.set("weekly_stats", 2024, _parquet(weekly_rows(receptions=(4, 6, 8))))

    ingest_nflverse.ingest_one(ds, 2024, week=3, version="2026-09-09")

    with store.db() as c:
        weeks = [r[0] for r in c.execute("SELECT week FROM nfl_player_week")]
    assert weeks == [3]


def test_a_season_not_yet_published_is_healthy_not_failed(env, fake_upstream):
    """On the day before a season opener, pbp/snaps/FTN have no file for the new
    season at all. Treating that as an error pages somebody every night in
    August."""
    ds = nflverse.DATASETS["pbp"]

    res = ingest_nflverse.ingest_one(ds, 2026, version="2026-09-09")

    assert res["status"] == "not-published"
    assert store.versions("pbp", 2026) == []


# --- stat corrections: the reason any of this exists -------------------------

def test_a_stat_correction_adds_a_version_and_leaves_the_old_one_intact(
        env, fake_upstream):
    """Acceptance. Re-ingest a week whose upstream values changed, and show the
    prior data_version is still queryable and unchanged."""
    ds = nflverse.DATASETS["weekly_stats"]
    fake_upstream.set("weekly_stats", 2024, _parquet(weekly_rows(receptions=(4, 6, 8))))
    ingest_nflverse.ingest_one(ds, 2024, version="2026-09-09")

    # upstream restates week 2: 6 receptions becomes 7
    fake_upstream.set("weekly_stats", 2024, _parquet(weekly_rows(receptions=(4, 7, 8))))
    res = ingest_nflverse.ingest_one(ds, 2024, version="2026-09-16")

    assert res["status"] == "updated"
    with store.db() as c:
        old = c.execute("SELECT receptions FROM nfl_player_week WHERE week=2 "
                        "AND data_version='2026-09-09'").fetchone()[0]
        new = c.execute("SELECT receptions FROM nfl_player_week WHERE week=2 "
                        "AND data_version='2026-09-16'").fetchone()[0]
    assert old == 4 + 2                          # 6, exactly as first published
    assert new == 7

    # and the as-of query returns what we knew at the time, not what we know now
    before = queries.threshold_counts("00-0000001", "receptions", 7,
                                      as_of="2026-09-09")
    after = queries.threshold_counts("00-0000001", "receptions", 7)
    assert before[0][2] == 1                     # only the 8-reception week
    assert after[0][2] == 2                      # the restated week now counts


def test_both_versions_are_recoverable_from_the_raw_archive(env, fake_upstream):
    """Acceptance: two pulls on different dates produce two raw archive entries
    and both are recoverable."""
    ds = nflverse.DATASETS["weekly_stats"]
    a = _parquet(weekly_rows(receptions=(4, 6, 8)))
    fake_upstream.set("weekly_stats", 2024, a)
    ingest_nflverse.ingest_one(ds, 2024, version="2026-09-09")
    b = _parquet(weekly_rows(receptions=(4, 7, 8)))
    fake_upstream.set("weekly_stats", 2024, b)
    ingest_nflverse.ingest_one(ds, 2024, version="2026-09-16")

    rels = [r[0] for r in store.versions("weekly_stats", 2024)]
    assert len(store.versions("weekly_stats", 2024)) == 2
    assert store.read_archived("nflverse/2026-09-09/stats_player_week_2024.parquet") == a
    assert store.read_archived("nflverse/2026-09-16/stats_player_week_2024.parquet") == b


# --- health ------------------------------------------------------------------

def test_health_is_written_on_success(env, fake_upstream):
    fake_upstream.set("weekly_stats", 2024, _parquet(weekly_rows()))
    ingest_nflverse.run(datasets=["weekly_stats"], seasons=[2024])

    row = store.health("nflverse:weekly_stats")
    assert row is not None and row[1] == 1


def test_health_is_written_on_failure(env, fake_upstream, monkeypatch):
    def boom(ds, season=None, client=None):
        raise RuntimeError("upstream on fire")

    monkeypatch.setattr(ingest_nflverse.nflverse, "fetch", boom)
    stats = ingest_nflverse.run(datasets=["weekly_stats"], seasons=[2024])

    assert stats["failed"] == 1
    row = store.health("nflverse:weekly_stats")
    assert row is not None and row[1] == 0


# --- retention must not touch this corpus ------------------------------------

def test_the_nflverse_mirror_is_exempt_from_rotation(env, fake_upstream):
    """Those dated snapshots are the only defence against a stat correction
    rewriting history. Shipping them to R2 and deleting them locally after 7
    days defeats the entire reason for mirroring."""
    ds = nflverse.DATASETS["weekly_stats"]
    fake_upstream.set("weekly_stats", 2024, _parquet(weekly_rows()))
    ingest_nflverse.ingest_one(ds, 2024, version="2020-01-01")   # ancient

    rel = "nflverse/2020-01-01/stats_player_week_2024.parquet"
    assert os.path.exists(os.path.join(config.RAW_DIR, *rel.split("/")))
    assert rotate_raw.is_exempt(rel) is True
    assert rotate_raw.due(days=7) == []          # years old, still not due

    rotate_raw.run(days=7)
    assert os.path.exists(os.path.join(config.RAW_DIR, *rel.split("/")))


def test_market_shards_are_still_rotated(env):
    """The exemption must be narrow - market data is the storage problem."""
    d = os.path.join(config.RAW_DIR, "kalshi", "2020-01-01")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "04.jsonl.gz"), "wb").write(b"x")

    assert rotate_raw.is_exempt("kalshi/2020-01-01/04.jsonl.gz") is False
    assert [r for r, _ in rotate_raw.due(days=7)] == ["kalshi/2020-01-01/04.jsonl.gz"]


def test_pruning_quotes_cannot_touch_the_nflverse_tables(env, fake_upstream):
    """prune_quotes deletes from `quotes` alone; the reference corpus is not
    derived from anything we could re-fetch identically, so it is never pruned."""
    from jobs import prune_quotes
    ds = nflverse.DATASETS["weekly_stats"]
    fake_upstream.set("weekly_stats", 2024, _parquet(weekly_rows()))
    ingest_nflverse.ingest_one(ds, 2024, version="2020-01-01")

    prune_quotes.run(days=1)

    with store.db() as c:
        assert c.execute("SELECT COUNT(*) FROM nfl_player_week").fetchone()[0] == 3
        assert c.execute("SELECT COUNT(*) FROM nflverse_versions").fetchone()[0] == 1


# --- the product query -------------------------------------------------------

def test_threshold_counts_answers_the_product_question(env, fake_upstream):
    """N+ receptions by season - the shape Kalshi lists ("4+ receptions") and
    the fastest proof the ingest join works end to end."""
    ds = nflverse.DATASETS["weekly_stats"]
    fake_upstream.set("weekly_stats", 2023, _parquet(
        weekly_rows(season=2023, receptions=(2, 5, 9))))
    fake_upstream.set("weekly_stats", 2024, _parquet(
        weekly_rows(season=2024, receptions=(5, 5, 1))))
    ingest_nflverse.run(datasets=["weekly_stats"], seasons=[2023, 2024])

    rows = queries.threshold_counts("00-0000001", "receptions", 5)

    assert rows == [(2023, 3, 2, pytest.approx(2 / 3)),
                    (2024, 3, 2, pytest.approx(2 / 3))]


def test_threshold_is_inclusive_because_the_contract_is(env, fake_upstream):
    """Kalshi lists "4+ receptions" with floor_strike 3.5 - the market pays on
    >= 4. An exclusive comparison here would be an off-by-one against the exact
    contract being priced."""
    ds = nflverse.DATASETS["weekly_stats"]
    fake_upstream.set("weekly_stats", 2024, _parquet(
        weekly_rows(receptions=(3, 4, 5))))
    ingest_nflverse.ingest_one(ds, 2024, version="2026-09-09")

    assert queries.threshold_counts("00-0000001", "receptions", 4)[0][2] == 2


def test_unknown_stat_is_rejected_rather_than_interpolated(env):
    """Stat names reach SQL as column names, so they come from the whitelist."""
    with pytest.raises(ValueError):
        queries.threshold_counts("00-0000001", "receptions; DROP TABLE quotes", 5)


def test_resolve_player_accepts_a_gsis_id_or_a_name(env, fake_upstream):
    ds = nflverse.DATASETS["weekly_stats"]
    fake_upstream.set("weekly_stats", 2024, _parquet(weekly_rows()))
    ingest_nflverse.ingest_one(ds, 2024, version="2026-09-09")

    assert queries.resolve_player("00-0000001")[0][0] == "00-0000001"
    assert queries.resolve_player("Test Player")[0][0] == "00-0000001"
    assert queries.resolve_player("Nobody At All") == []
