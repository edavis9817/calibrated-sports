"""Is the nflverse contracts table usable, and for what? A survey, not a build.

    python -m analytics.contracts_survey
    python -m analytics.contracts_survey --upstream   # compare against today's

Same shape as the NGS survey: what exists, what it covers, what it cannot
answer. A negative answer is a result - "this exists and cannot support four of
the six" is worth more than a build that discovers it later.

THE TABLE IS `contracts/historical_contracts.parquet` (NOT `contracts.parquet`,
which 404s). 26 columns, two of them nested lists of structs that carry most of
the value: `season_history` is per-year cap detail and `contract_history` is
per-contract terms.

THREE TRAPS THIS MEASURES, and the third one caught the first version of this
very module:

  A PER-PLAYER DUPLICATION OF BOTH NESTED COLUMNS. The table is one row per
  (player, CONTRACT), and each row carries that player's ENTIRE
  `season_history` and `contract_history`, byte-identical. A player with 38
  contract rows carries the same 8-year history 38 times. Exploding from the
  flat frame therefore multiplies every nested figure by that player's own row
  count - median 2, max 38, 6.19x overall on season rows and 8.45x on contract
  rows. Combined with the "Total" trap below, a naive explode-and-sum reports
  $1,054,673m of cap against a true $112,992m: **9.33x**.

  This is worse than the "Total" double in one specific way: the multiplier is
  PER PLAYER and variable, so no sanity check on magnitude catches it
  consistently, and a ratio between two figures computed the same wrong way
  comes out right. `per_player()` is the fix and every nested accessor here
  goes through it.

TWO FURTHER TRAPS, both already tabled classes:

  A SILENT DOUBLE inside a nested column. `season_history` carries a row whose
  `year` is the STRING "Total", and for 98.6% of players it equals the sum of
  the real years. Explode and sum without excluding it and every cap figure
  doubles. Same class as NGS week 0, one level less visible because it is
  inside a list.

  A SILENT ZERO. `year_signed` is 0 on 1,106 rows rather than null, so a filter
  like `year_signed >= 2010` drops them without saying so and a decade grouping
  files them under 1980.

Licensing is NOT measurable from the file and is reported separately in
`docs/F06-contracts-survey.md`, from the terms themselves.
"""
import argparse
import sys

from analytics import paths

ASSET = "historical_contracts.parquet"
TOTAL_ROW = "Total"


def _pl():
    import polars as pl
    return pl


def per_player(df):
    """One row per player, before touching a nested column.

    THE TABLE IS ONE ROW PER (PLAYER, CONTRACT) AND EACH ROW CARRIES THE
    PLAYER'S WHOLE NESTED HISTORY. Verified, not assumed: the player with 38
    rows has 38 byte-identical copies of one 8-year `season_history`. Any
    explode from the flat frame multiplies by the row count.
    """
    return df.unique(subset=["otc_id"], keep="first")


def duplication(df):
    """How much a naive explode would inflate each nested column."""
    pl = _pl()
    one = per_player(df)
    out = {}
    for col in ("season_history", "contract_history"):
        naive = df.select(["otc_id", col]).explode(col).height
        correct = one.select(["otc_id", col]).explode(col).height
        out[col] = {"naive": naive, "correct": correct,
                    "inflation": round(naive / max(correct, 1), 2)}
    counts = df.group_by("otc_id").agg(pl.len())["len"]
    out["rows_per_player"] = {"min": int(counts.min()),
                              "median": float(counts.median()),
                              "max": int(counts.max())}
    return out


def load(path=None):
    pl = _pl()
    if path is None:
        got = paths.latest_asset(ASSET)
        if not got:
            raise SystemExit("%s is not in the archive - nothing to survey" % ASSET)
        path, _pull = got
    return pl.read_parquet(path)


def shape(df):
    pl = _pl()
    return {"rows": df.height, "columns": len(df.columns),
            "players_otc": df["otc_id"].n_unique(),
            "players_gsis": df["gsis_id"].n_unique(),
            "active_rows": df.filter(pl.col("is_active")).height}


def depth(df):
    """Rows by signing decade. `year_signed == 0` is its own bucket, named."""
    pl = _pl()
    out = {}
    for row in (df.group_by((pl.col("year_signed") // 10 * 10).alias("decade"))
                .agg(pl.len().alias("rows")).sort("decade").to_dicts()):
        key = "unknown (year_signed = 0)" if row["decade"] == 0 else "%ds" % row["decade"]
        out[key] = row["rows"]
    return out


def total_row_trap(df):
    """(total_rows, real_rows, players, agreeing) for the nested "Total" row.

    `agreeing` is the count where Total equals the sum of the real years - the
    reconciliation that proves it is a duplicate rather than a distinct fact,
    the same check the NGS week-0 trap needed.
    """
    pl = _pl()
    sh = (per_player(df).select(["otc_id", "season_history"])
          .explode("season_history").unnest("season_history"))
    tot = sh.filter(pl.col("year") == TOTAL_ROW)
    real = sh.filter((pl.col("year") != TOTAL_ROW) & pl.col("year").is_not_null())
    t = tot.group_by("otc_id").agg(pl.col("cap_number").sum().alias("t"))
    r = real.group_by("otc_id").agg(pl.col("cap_number").sum().alias("r"))
    j = t.join(r, on="otc_id", how="inner").drop_nulls()
    agree = j.filter((pl.col("t") - pl.col("r")).abs() < 1).height
    return {"total_rows": tot.height, "real_rows": real.height,
            "null_year_rows": sh.filter(pl.col("year").is_null()).height,
            "compared": j.height, "agreeing": agree}


def join_to_spine(df, con):
    """How much of the play-by-play spine has a contract, by era.

    THE ERA SPLIT IS THE ANSWER. A flat "60.6% of the spine joins" hides that
    the table is effectively a 2015+ table: before 2010 it is nothing, and from
    2015 it is complete.
    """
    pl = _pl()
    have = set(df["gsis_id"].drop_nulls().to_list())
    rows = con.execute("SELECT player_id, MAX(season) FROM f_play_usage "
                       "GROUP BY player_id").fetchall()
    eras = {}
    for pid, last in rows:
        bucket = (last // 5) * 5
        hit, miss = eras.get(bucket, (0, 0))
        eras[bucket] = (hit + (pid in have), miss + (pid not in have))
    spine = {p for p, _ in rows}
    return {"spine": len(spine), "joined": len(spine & have),
            "by_era": {"%d-%d" % (b, b + 4): eras[b] for b in sorted(eras)}}


def identity(df, players_path=None):
    """The id questions, which track A flagged as not incidental."""
    pl = _pl()
    out = {"rows_with_gsis": df["gsis_id"].drop_nulls().len(),
           "rows": df.height,
           "players_otc": df["otc_id"].n_unique(),
           "players_gsis": df["gsis_id"].n_unique()}
    out["players_without_any_gsis"] = out["players_otc"] - out["players_gsis"]
    got = paths.latest_asset("players.parquet") if players_path is None else (players_path, None)
    if got:
        known = set(_pl().read_parquet(got[0])["gsis_id"].drop_nulls().to_list())
        mine = set(df["gsis_id"].drop_nulls().to_list())
        out["gsis_not_in_players_parquet"] = len(mine - known)
    act = df.filter(pl.col("is_active"))
    out["active_players"] = act["otc_id"].n_unique()
    out["active_with_gsis"] = act["gsis_id"].drop_nulls().n_unique()
    return out


# Fields a cap feature would need, and whether the table has one. Checked
# against the real schema including both nested structs, so a column appearing
# upstream later turns a MISSING into a hit without anyone remembering to look.
NEEDED = {
    "dead money": ("dead",),
    "void years": ("void",),
    "restructures": ("restructur",),
    "incentives / escalators": ("incentive", "escalat"),
    "guarantee structure (injury vs full)": ("injury", "vesting", "skill_guarantee"),
    "cap hit per year": ("cap_number",),
    "cash paid per year": ("cash_paid",),
    "guarantee totals": ("guaranteed", "guarantees"),
    "draft capital": ("draft_round", "draft_overall"),
}


def field_names(df):
    names = list(df.columns)
    for col in ("season_history", "contract_history"):
        inner = df.schema[col].inner
        names += [f.name for f in inner.fields]
    return names


def gaps(df):
    names = [n.lower() for n in field_names(df)]
    return {label: sorted({n for n in names if any(p in n for p in pats)})
            for label, pats in NEEDED.items()}


def restructure_proxy(df):
    """`status` is the only thing resembling a restructure record."""
    pl = _pl()
    ch = (per_player(df).select(["otc_id", "contract_history"])
          .explode("contract_history").unnest("contract_history"))
    return {r["status"]: r["len"] for r in
            ch.group_by("status").agg(pl.len()).sort("len", descending=True)
            .head(8).to_dicts()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--upstream", action="store_true",
                    help="download today's copy and diff it against the archive")
    a = ap.parse_args(argv)
    df = load()
    con = paths.connect(read_only=True)

    print("SHAPE      ", shape(df))
    print("DUPLICATION", duplication(df))
    print("DEPTH      ", depth(df))
    print("IDENTITY   ", identity(df))
    print("TOTAL TRAP ", total_row_trap(df))
    j = join_to_spine(df, con)
    print("SPINE JOIN  %d of %d" % (j["joined"], j["spine"]))
    for era, (hit, miss) in j["by_era"].items():
        print("   last seen %s: %4d with, %4d without (%3.0f%%)"
              % (era, hit, miss, 100 * hit / max(hit + miss, 1)))
    print("GAPS")
    for label, found in gaps(df).items():
        print("   %-38s %s" % (label, found or "ABSENT"))
    print("RESTRUCTURE PROXY (contract_history.status)")
    print("  ", restructure_proxy(df))

    if a.upstream:
        import hashlib

        import httpx

        import nflverse
        url = nflverse.DATASETS["contracts"].url()
        r = httpx.get(url, follow_redirects=True, timeout=180)
        local = paths.latest_asset(ASSET)[0]
        old = open(local, "rb").read()
        print("UPSTREAM   last-modified %s" % r.headers.get("last-modified"))
        print("   content moved: %s  (archived %d bytes, upstream %d)"
              % (hashlib.sha256(old).hexdigest() != hashlib.sha256(r.content).hexdigest(),
                 len(old), len(r.content)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
