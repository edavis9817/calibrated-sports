"""Kalshi trading fees. See `docs/kalshi-fee-mechanics.md` for the source notes.

    fee = roundup(M * rate * C * P * (1 - P))

    rate   0.07 taker, 0.0175 maker
    C      contracts IN THE ORDER - the fee is charged on the whole order
    M      per-series multiplier; DEFAULTS ARE taker 1, maker 0

THE FEE IS ON THE ORDER, NOT ON EACH CONTRACT. Calling this with contracts=1
and then multiplying bills the ceil-to-cent rounding to every contract, which
overstates by ~1.1x at p=0.5, ~1.6x at p=0.1 and ~2.9x at p=0.05 - about 2.4x
across our ledger. `contracts` therefore has NO DEFAULT: a caller must say how
big the order is, because there is no safe guess.

MAKER DEFAULTS TO M = 0, WHICH MEANS EXACTLY ZERO, NOT ONE CENT. Only series
listed in the schedule's Non-Standard Fees table carry a maker multiplier, and
no NFL player-prop series appears there. So on our props a resting order is
fee-free under the published defaults - see `jobs/audit_fees.py --series`, which
checks that against the tickers we actually hold rather than assuming it.

ARITHMETIC IS IN Decimal, DELIBERATELY. In binary floats
`0.07 * 100 * 0.1 * 0.9` is 0.6300000000000001, and a ceil-to-cent turns an
exact $0.63 into $0.64 - an off-by-one-cent that looks like a formula error and
lands on precisely the round prices that occur most. The old implementation
papered over this with `round(x, 6)` before `ceil`, which works until it does
not. Decimal removes the class of bug rather than one instance of it.

OPEN QUESTION - CENT VERSUS CENTICENT ROUNDING
The schedule's prose says the fee rounds up so that fee + positionCost lands on
a centicent ($0.0001). Its own published table contradicts that and is
consistent with rounding up to a CENT: at 100 contracts, P = 0.35 / 0.45 / 0.25
the raw values are 1.5925 / 1.7325 / 1.3125 and the table shows 1.60 / 1.74 /
1.32. All 42 published vectors in `tests/test_fees.py` match ceil-to-cent, so
that is what is implemented here.

**If the real behaviour turns out to be centicent, change `QUANTUM` below and
nothing else.** At a 1-contract ticket the two rules differ by up to 3x; at 10+
contracts by ~0.1%, which is immaterial to any decision this project makes.
Resolving it needs one live fill, not more reading.
"""
from decimal import Decimal, ROUND_CEILING

# The rounding increment, in dollars. See "OPEN QUESTION" above: switch this to
# Decimal("0.0001") if a live fill shows centicent rounding.
QUANTUM = Decimal("0.01")

RATE = {"taker": Decimal("0.07"), "maker": Decimal("0.0175")}

# Published defaults for any series NOT in the Non-Standard Fees table.
DEFAULT_M = {"taker": Decimal(1), "maker": Decimal(0)}

# Non-Standard Fees table, as (maker_M, taker_M). Football entries only; the
# table also lists many 0/0 non-sports series that we never touch.
SERIES_M = {
    "KXNFLGAME": (1, 1),        # Professional Football Game
    "KXNCAAFGAME": (1, 1),      # College Football Game
    "KXSB": (1, 1),             # Super Bowl
    "KXMVE": (2, 1),            # Combos - the one entry where maker > taker
    # BRIEF 019: Kalshi's own `/series` endpoint marks these five
    # `fee_type=quadratic_with_maker_fees`, and the PDF's Non-Standard Fees
    # table omits them. The API is the authority - it is what the exchange
    # bills from - so they carry a maker fee. REC and RSHATT are
    # `fee_type=quadratic` and genuinely maker-free, so briefs 016-018, which
    # were measured only on those two, are unaffected.
    "KXNFLSPREAD": (1, 1), "KXNFLTOTAL": (1, 1), "KXNFLFIRSTTD": (1, 1),
    "KXNFLANYTD": (1, 1), "KXNFL2TD": (1, 1),
    # AP awards
    "KXNFLMVP": (1, 1), "KXNFLCOTY": (1, 1), "KXNFLOPOTY": (1, 1),
    "KXNFLDPOTY": (1, 1), "KXNFLOROTY": (1, 1), "KXNFLDROTY": (1, 1),
    "KXNFLCPOTY": (1, 1),
}

# Division and conference series are a family, not a fixed list.
SERIES_M_PREFIX = (("KXNFLAFC", (1, 1)), ("KXNFLNFC", (1, 1)))


def series_of(market_id: str) -> str:
    """`KXNFLREC-26SEP13DALNYG-NYGOBECKHAM13-6` -> `KXNFLREC`."""
    return (market_id or "").split("-", 1)[0].upper()


def series_multiplier(ticker: str):
    """(maker_M, taker_M) for a series or a full market id.

    Returns the published DEFAULTS for anything not in the table, which is the
    interesting case: an unlisted series is maker-fee-free.
    """
    s = series_of(ticker)
    if s in SERIES_M:
        m, t = SERIES_M[s]
        return Decimal(m), Decimal(t)
    for prefix, (m, t) in SERIES_M_PREFIX:
        if s.startswith(prefix):
            return Decimal(m), Decimal(t)
    return DEFAULT_M["maker"], DEFAULT_M["taker"]


def listed(ticker: str) -> bool:
    """Is this series in the Non-Standard Fees table at all?"""
    s = series_of(ticker)
    return s in SERIES_M or any(s.startswith(p) for p, _ in SERIES_M_PREFIX)


def kalshi_fee(price, contracts: int, side: str = "taker",
               multiplier=None) -> Decimal:
    """Fee in DOLLARS for the WHOLE ORDER, as a Decimal.

    `multiplier` overrides the per-side default; pass the value from
    `series_multiplier()` when the series is known. M = 0 returns exactly
    Decimal("0") - a fee-free order is free, not a cent.
    """
    if side not in RATE:
        raise ValueError(f"side must be 'taker' or 'maker', got {side!r}")
    if contracts is None or contracts <= 0:
        raise ValueError(f"contracts must be a positive order size, got {contracts!r}")
    p = Decimal(str(price))
    if not (0 < p < 1):
        raise ValueError(f"price must be in (0, 1), got {price!r}")
    m = DEFAULT_M[side] if multiplier is None else Decimal(str(multiplier))
    if m == 0:
        return Decimal("0")
    raw = m * RATE[side] * Decimal(int(contracts)) * p * (Decimal(1) - p)
    return (raw / QUANTUM).to_integral_value(rounding=ROUND_CEILING) * QUANTUM


def fee_per_contract(price, contracts: int, side: str = "taker",
                     multiplier=None) -> float:
    """The order fee spread over the order, as a float in dollars per contract.

    This is the form every edge calculation wants, and keeping it in one place
    stops call sites from dividing by the wrong thing.
    """
    return float(kalshi_fee(price, contracts, side, multiplier)) / int(contracts)


def edge_after_fees(fair_prob: float, price: float, contracts: int,
                    side: str = "taker", multiplier=None) -> float:
    """Expected profit PER CONTRACT in dollars, fees included.

    Positive is not sufficient - it must clear the Monte Carlo standard error
    on fair_prob as well, or you are trading simulation noise.
    """
    fee = fee_per_contract(price, contracts, side, multiplier)
    return fair_prob * (1.0 - price) - (1.0 - fair_prob) * price - fee
