"""Retrosheet season bundle -> store rows. Pure: bytes in, rows out, no I/O.

THE HEADER IS CHECKED EXACTLY. Every source column is either stored or named in
`DROPPED`; a column that is neither, or one that has gone missing, REFUSES the file.
Upstream re-publishes old seasons (the 2024 bundle's members are dated 2026-08-07), so a
layout change arriving in a restatement is a real possibility, and a parser that guessed
at it would write plausible wrong rows.

BLANK IS NULL. See `mlb.schema`: a blank stat is unknown. A blank credit flag is 0 in a
game with a box score and NULL in one without.
"""
import csv
import io
import zipfile

from mlb import schema

SOURCE_RENAME = {"gid": "game_id", "id": "player_id"}

# Source columns deliberately not stored (all remain in the archived bundle):
DROPPED = {
    "gameinfo": ["htbf", "oscorer", "umphome", "ump1b", "ump2b", "ump3b", "umplf", "umprf",
                 "line", "batteries", "lineups"],
    "teamstats": [f"inn{i}" for i in range(1, 29)]
                 + [f"start_l{i}" for i in range(1, 10)]
                 + [f"start_f{i}" for i in range(1, 11)]
                 + ["site"],
    "batting": ["site"],
    "pitching": ["site"],
    "allplayers": [],
}

FLAGS = set(schema.BAT_FLAGS + schema.PIT_FLAGS)


class LayoutChanged(Exception):
    pass


def _members(body: bytes, season: int) -> dict:
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = {n.lower(): n for n in z.namelist()}
        out = {}
        for m in list(schema.MEMBERS) + list(schema.NOT_PARSED):
            n = names.get(f"{season}{m}.csv")
            if n is None:
                raise LayoutChanged(f"{season} bundle has no {season}{m}.csv "
                                    f"(members: {sorted(z.namelist())})")
            if m in schema.MEMBERS:
                out[m] = z.read(n)
        return out


def _int(v, col, where):
    if v == "":
        return None
    try:
        return int(v)
    except ValueError:
        raise LayoutChanged(f"{where}: {col}={v!r} is not an integer") from None


def _flag(v, col, box, where):
    if v == "":
        return 0 if box == "y" else None
    if v == "1":
        return 1
    raise LayoutChanged(f"{where}: flag {col}={v!r} (expected '1' or blank)")


def member_rows(member: str, raw: bytes, season: int):
    """(rows, measurements) for one bundle member. Rows are tuples in `schema.columns`
    order. Refuses on any header drift."""
    table = schema.MEMBERS[member]
    cols = schema.columns(table)
    types = dict(schema.TABLES[table][1])
    text = raw.decode("utf-8")          # strict: a decode guess would corrupt names
    rd = csv.reader(io.StringIO(text))
    header = next(rd)
    stored = [SOURCE_RENAME.get(h, h) for h in header]
    wanted = set(cols) - {"sport"}
    dropped = set(DROPPED[member])
    have = set(stored)
    missing = wanted - have
    unknown = have - wanted - dropped
    if member != "gameinfo":
        # Only gameinfo publishes `season`; every other member's row takes the bundle's
        # own year, and gameinfo's is checked against it below.
        missing.discard("season")
    if missing or unknown:
        raise LayoutChanged(f"{season}{member}.csv: missing {sorted(missing)}, "
                            f"unrecognised {sorted(unknown)}")
    idx = {c: stored.index(c) for c in cols if c in have}
    rows, n_in, stattypes = [], 0, {}
    for lineno, rec in enumerate(rd, start=2):
        n_in += 1
        if len(rec) != len(header):
            raise LayoutChanged(f"{season}{member}.csv line {lineno}: {len(rec)} fields, "
                                f"header has {len(header)}")
        where = f"{season}{member}.csv:{lineno}"
        box = rec[idx["box"]] if "box" in idx else "y"
        out = []
        for c in cols:
            if c == "sport":
                out.append(schema.SPORT)
            elif c == "season" and c not in idx:
                out.append(season)
            elif c in FLAGS and member in ("batting", "pitching"):
                out.append(_flag(rec[idx[c]], c, box, where))
            elif types[c] == "INTEGER":
                out.append(_int(rec[idx[c]], c, where))
            else:
                v = rec[idx[c]]
                out.append(v if v != "" else None)
        if "season" in idx and out[cols.index("season")] != season:
            raise LayoutChanged(f"{where}: season {out[cols.index('season')]} in the "
                                f"{season} bundle")
        if "stattype" in idx:
            st = rec[idx["stattype"]]
            stattypes[st] = stattypes.get(st, 0) + 1
        rows.append(tuple(out))
    return rows, {"rows_in": n_in, "stattypes": stattypes}


def bundle(body: bytes, season: int) -> dict:
    """{member: (table, rows, measurements)} for every parsed member of one bundle."""
    return {m: (schema.MEMBERS[m], *member_rows(m, raw, season))
            for m, raw in _members(body, season).items()}
