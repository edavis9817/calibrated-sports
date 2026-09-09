"""The join key for the entire system.

An OUTCOME is a semantic claim, independent of any venue:

    (nfl, 2026, week 1, player 00-0036355, receptions, line 5.5, over)

A MARKET is one venue's instrument that points at an outcome. A Kalshi ticker,
a Polymarket token id and a DraftKings market id are three MARKETs for one
OUTCOME. Without this split there is no cross-venue comparison and no way to
measure CLV against a sharper book than the one you bet at.

Everything carries `sport` from row one so a second sport is additive, not a
rewrite.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Sport(str, Enum):
    NFL = "nfl"
    MLB = "mlb"          # not implemented; the discriminator exists from day one
    NBA = "nba"


class Side(str, Enum):
    OVER = "over"
    UNDER = "under"
    YES = "yes"
    NO = "no"
    HOME = "home"
    AWAY = "away"


class MarketType(str, Enum):
    PLAYER_PROP = "player_prop"
    SPREAD = "spread"
    TOTAL = "total"
    MONEYLINE = "moneyline"
    FUTURE = "future"


# Canonical stat keys. Ordered by the forecastability ranking in research/:
# volume props carry the signal, yardage less, TDs and QB props least.
class Stat(str, Enum):
    TARGETS = "targets"
    RUSH_ATTEMPTS = "rush_attempts"
    RECEPTIONS = "receptions"
    RUSH_YARDS = "rush_yards"
    RECEIVING_YARDS = "receiving_yards"
    PASS_ATTEMPTS = "pass_attempts"
    COMPLETIONS = "completions"
    PASSING_YARDS = "passing_yards"
    ANYTIME_TD = "anytime_td"


_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(v) -> str:
    return _SLUG.sub("-", str(v).strip().lower()).strip("-")


def _fmt_line(line: Optional[float]) -> str:
    """Stable string form of a line. 5.5 -> '5.5', 5.0 -> '5', None -> 'na'.

    Must be stable: this string goes into the outcome_id, and an id that
    changes because a float formatted differently silently forks your history.
    """
    if line is None:
        return "na"
    return f"{line:g}"


@dataclass(frozen=True, slots=True)
class Outcome:
    """A venue-independent claim that resolves true or false."""
    sport: Sport
    season: int
    week: Optional[int]
    market_type: MarketType
    subject: str                      # player id (gsis_id) or team abbr
    stat: Optional[Stat] = None
    line: Optional[float] = None
    side: Side = Side.OVER
    event_id: Optional[str] = None    # canonical game id, when known
    meta: dict = field(default_factory=dict, compare=False)

    @property
    def key(self) -> str:
        """Human-readable canonical key. Deterministic and stable."""
        parts = [
            self.sport.value,
            str(self.season),
            f"wk{self.week}" if self.week is not None else "season",
            self.market_type.value,
            _slug(self.subject),
            self.stat.value if self.stat else "na",
            _fmt_line(self.line),
            self.side.value,
        ]
        return "|".join(parts)

    @property
    def outcome_id(self) -> str:
        """Short stable hash of the key, for use as a database primary key."""
        return hashlib.sha1(self.key.encode()).hexdigest()[:16]

    def opposite(self) -> "Outcome":
        """The other side of the same claim. Used to de-vig two-way markets."""
        flip = {Side.OVER: Side.UNDER, Side.UNDER: Side.OVER,
                Side.YES: Side.NO, Side.NO: Side.YES,
                Side.HOME: Side.AWAY, Side.AWAY: Side.HOME}
        return Outcome(self.sport, self.season, self.week, self.market_type,
                       self.subject, self.stat, self.line, flip[self.side],
                       self.event_id)

    def __str__(self) -> str:
        return self.key


@dataclass(frozen=True, slots=True)
class Market:
    """One venue's instrument pointing at an Outcome."""
    venue: str                 # kalshi | polymarket | oddsapi:pinnacle | ...
    venue_market_id: str
    outcome_id: str
    venue_event_id: Optional[str] = None
    title: Optional[str] = None

    @property
    def market_key(self) -> str:
        return f"{self.venue}:{self.venue_market_id}"


# --- helpers -----------------------------------------------------------------

def player_prop(season: int, week: int, player_id: str, stat: Stat,
                line: float, side: Side = Side.OVER,
                event_id: str | None = None,
                sport: Sport = Sport.NFL) -> Outcome:
    return Outcome(sport, season, week, MarketType.PLAYER_PROP, player_id,
                   stat, line, side, event_id)


def is_push_possible(line: Optional[float], stat: Optional[Stat]) -> bool:
    """True when the outcome can land exactly on the line.

    Half-point lines can never push. Integer lines on a discrete stat can, and
    a model that ignores this systematically misprices integer-line props -
    P(X > 5) and P(X >= 5) differ by the entire probability mass at 5, which
    for receptions is a large number.
    """
    if line is None:
        return False
    if not float(line).is_integer():
        return False
    return stat in {Stat.TARGETS, Stat.RUSH_ATTEMPTS, Stat.RECEPTIONS,
                    Stat.PASS_ATTEMPTS, Stat.COMPLETIONS, Stat.ANYTIME_TD}
