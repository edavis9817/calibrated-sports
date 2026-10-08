"""A figure that may only be cited with a sentence carries that sentence (a-75).

Track F's reliability attack (f-26 run 1, f-27 run 2) passed the game record files
and returned some of their figures as "citable only with a stated sentence
attached" - and measured that the sentence was not attached where the figure is
served. This module is where it gets attached, and what refuses a file without it.

    figures    research/results/f27_required_sentences.json - F's measurements,
               CARRIED. Nothing here re-derives one.
    REQUIRED   the published figures that need a sentence, keyed `file|holder`.
               Derived from the carried file, so a fourth is one entry there.
    qualifier  the block a builder puts beside the figure: the sentence, WORDED
               FROM the carried figures (CLAUDE.md, Claims: exported prose is
               generated from figures, in code that can produce the other answer),
               the figures themselves, and the figure it was measured beside.
    require    the gate (`jobs.game_export.gate`). A served figure without its
               sentence, a sentence that is not the one the figures word, and a
               sentence on a figure nobody registered all raise.

THE SENTENCE IS A FOOTNOTE, NOT A VERDICT. It does not change `compared`: the
words beside a figure still come from that figure's own interval. Whether a
figure that does not survive correction should keep its verdict is Ethan's call
(a-75 report) and is not taken here.

`served_matches` is the staleness guard. The carried figures were measured
beside one served value; `forecast.json`'s stage figure is re-measured on the
store every export and can move. The sentence stays attached either way - absence
would be the worse error - and the flag says whether the ESTIMATE it sits beside
is still the one F attacked, at four decimals. `measured_against.interval` is F's
own draw and is carried for the reader, not compared.
"""
from __future__ import annotations

import functools
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARRIED = "research/results/f27_required_sentences.json"
FIELD = "qualifier"
STAGE_HOLDER = "season_stage[stage=weeks_1_4]"
STAGE_KINDS = ("game.forecast", "game.matchup")     # the matchup carries the forecast's stage
EARLY_STAGE = "weeks_1_4"


class MissingSentence(RuntimeError):
    pass


@functools.lru_cache(maxsize=None)
def carried():
    with open(os.path.join(HERE, *CARRIED.split("/")), encoding="utf-8") as f:
        return json.load(f)


def figure_id(file, holder):
    return f"{file}|{holder}"


def required():
    """-> {`file|holder`: the carried entry}. Refuses a duplicate id: two sentences
    for one figure is a second, unchecked claim."""
    out = {}
    for e in carried()["sentences"]:
        k = figure_id(e["file"], e["holder"])
        if k in out:
            raise ValueError(f"{CARRIED} names {k} twice")
        out[k] = e
    return out


REQUIRED = tuple(sorted(required()))


def _fig(d):
    e, iv = d["estimate"], d["interval"]
    return f"{e:+.4f}" if iv is None else f"{e:+.4f} [{iv[0]:+.4f}, {iv[1]:+.4f}]"


def _size(d):
    e, iv = d["estimate"], d["interval"]
    return f"{e:.4f}" if iv is None else f"{e:.4f} [{iv[0]:.4f}, {iv[1]:.4f}]"


def _wind(f, served):
    wo = f["without_wind"]
    # lower is better for a Brier difference, so a published figure BELOW the
    # no-wind one flatters the forecast; computed, so it can read the other way
    if served["estimate"] < wo["estimate"]:
        end = "which makes the published figure the favourable end"
    elif served["estimate"] > wo["estimate"]:
        end = "which makes the published figure the unfavourable end"
    else:
        end = "which leaves the published figure where it was"
    return ("Scored with each game's recorded wind, which no forecast made before kickoff "
            f"has. With wind taken out of the model the same comparison is {_fig(wo)}: recorded "
            f"wind moves it by {_size(f['wind_worth'])}, {end}.")


def _mde(f, _served):
    r = round(f["mde_ratio"], 2)
    where = "sits exactly on" if r == 1.0 else ("is below" if r < 1.0 else "is above")
    survives = "survives" if f["adjusted_p"] < f["alpha"] else "does not survive"
    return (f"This estimate {where} its own minimum detectable effect (ratio {r:.2f}) and "
            f"{survives} a {f['correction']} correction over the {f['intervals_registered']} "
            f"intervals the study registered (adjusted p {f['adjusted_p']:.2f}).")


def _grid(f, _served):
    seeds = "one seed" if f["seeds"] == 1 else f"{f['seeds']} seeds"
    return (f"Plain Elo's {f['parameter']} was held at the top of the fitting grid "
            f"({f['grid_max']}) in {f['seasons_at_max']} of {f['seasons']} seasons; with "
            f"{f['parameter']} allowed to {f['allowed_to']} the same comparison is "
            f"{_fig(f['alternative'])}, {seeds}.")


WORDING = {"recorded_wind": _wind, "at_mde_uncorrected": _mde, "comparator_at_grid_max": _grid}


def _matches(against, served):
    """The ESTIMATE, at four decimals. Not the interval: that is a bootstrap draw,
    and F's redraw of the stage figure is [-0.0024, +0.0009] beside a served
    [-0.0024, +0.0007] for the same -0.0008. An interval test would read False
    for ever on seed noise and stop meaning anything."""
    return served["estimate"] is not None and round(served["estimate"], 4) == against["estimate"]


def qualifier(file, holder, served):
    """The block that travels with the figure at `file|holder`. `served` is the
    published interval it sits beside. KeyError when no sentence is registered for
    that figure - a builder never gets a silent None."""
    e = required()[figure_id(file, holder)]
    src = carried()["source"]
    return {"kind": e["kind"],
            "statement": WORDING[e["kind"]](e["figures"], served),
            "measured_against": dict(e["measured_against"]),
            "served_matches": _matches(e["measured_against"], served),
            "figures": json.loads(json.dumps(e["figures"])),
            "source": {"unit": src["unit"], "commit": src["commit"],
                       "verdicts": src["verdicts"]}}


# =============================================================================
# the gate
# =============================================================================

def _stage_holders(payload):
    """Every block of a stage that holds the weeks 1-4 figure, with its path. The
    figure is in `season_stage` in weeks 1-4 and in `season_stage.other` after;
    in the postseason, and with no game left, it is not served at all."""
    st = payload["season_stage"]
    if st is None:
        return []
    out = []
    for path, blk in (("season_stage", st), ("season_stage.other", st["other"])):
        if blk is not None and blk["stage"] == EARLY_STAGE:
            out.append((path, blk))
    return out


def located(key, payload):
    """-> [(figure id, path, holder, entry)] for every registered figure this file
    serves. A registered path that does not resolve RAISES: a figure the gate could
    not find is not a figure that passed."""
    from jobs import metric_registry as MR
    out = []
    for fid, e in required().items():
        if e["holder"] == STAGE_HOLDER:
            if payload.get("kind") in STAGE_KINDS:
                out += [(fid, p, blk, e) for p, blk in _stage_holders(payload)]
        elif e["file"] == key:
            try:
                blk = MR.resolve(payload, e["holder"])
            except MR.Unresolved as x:
                raise MissingSentence(f"{key}: the figure at {e['holder']} needs a sentence "
                                      f"and is not where the registry says ({x})") from None
            if not isinstance(blk, dict):
                raise MissingSentence(f"{key}: {e['holder']} is not an object")
            out.append((fid, e["holder"], blk, e))
    return out


def _carriers(o, path=""):
    """Every object in a payload with a non-null qualifier -> [(path, object)]."""
    out = []
    if isinstance(o, dict):
        if o.get(FIELD) is not None:
            out.append((path, o))
        for k, v in o.items():
            if k != FIELD:
                out += _carriers(v, f"{path}.{k}" if path else k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out += _carriers(v, f"{path}[{i}]")
    return out


def require(files):
    """Every registered figure in `files` carries the sentence its figures word, and
    no other figure carries one. -> the statement; raises MissingSentence."""
    problems, n = [], 0
    for key in sorted(files):
        payload = files[key]
        expected = set()
        for _fid, path, blk, e in located(key, payload):
            n += 1
            expected.add(id(blk))
            got = blk.get(FIELD)
            if got is None:
                problems.append(f"{key}: {path}.{e['figure']} is served without its sentence")
            elif got != qualifier(e["file"], e["holder"], blk[e["figure"]]):
                problems.append(f"{key}: the sentence at {path} is not the one its figures word")
        for path, blk in _carriers(payload):
            if id(blk) not in expected:
                problems.append(f"{key}: {path} carries a sentence no registered figure owns")
    if problems:
        raise MissingSentence("; ".join(problems) + " - refusing")
    return (f"sentence check: {n} figure(s) that need a sentence carry it, across "
            f"{len(files)} file(s)")
