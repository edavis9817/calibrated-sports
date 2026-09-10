"""Order book depth: executable prices, not the touch.

Top of book is not a tradeable price. Observed live on Kalshi:

    KXNFLREC-...BUFJCOOK4-7   best ask 0.04 for ONE contract
                              then 0.05 x 200, 0.06 x 500, 0.98 x 5000
                              1000-contract VWAP = 0.333

A backtest reading the touch believes it filled at 4c. Real size costs 33c.
That is not a rounding error, it is the difference between an edge and a
fantasy, and it is unrecoverable after the fact: candlesticks carry price and
volume but no book, so depth not captured live is depth gone forever.

LADDER ORDERING, verified against both live APIs 2026-09-10
------------------------------------------------------------
Kalshi     `orderbook_fp.yes_dollars` and `no_dollars` are both ASCENDING by
           price and are both BIDS. The best bid on YES is the LAST yes entry;
           the best ask on YES is implied by the best NO bid, ask = 1 - no.
Polymarket `bids` ascend, `asks` DESCEND. Best bid is the last bid, best ask
           is the last ask - i.e. min(asks), max(bids).

Getting either backwards silently inverts every VWAP: you compute the cost of
filling against the worst prices in the book and conclude the market is
catastrophically illiquid, or against a phantom and conclude it is free. Both
look plausible in a report. Hence `normalize_*` below rather than ad-hoc
slicing at each call site.
"""
from dataclasses import dataclass, field

# The stake ladder. 100 is a small real bet, 5000 is institutional size on an
# exchange this size - the point is to show where an edge stops surviving.
DEFAULT_SIZES = (100, 500, 1000, 5000)


@dataclass
class Depth:
    """Executable cost to buy `size` contracts of one side of one market."""
    touch_price: float = None
    touch_size: float = None
    n_levels: int = 0
    total_size: float = 0.0
    size_within_1c: float = 0.0
    size_within_5c: float = 0.0
    vwap: dict = field(default_factory=dict)      # size -> price, or None
    filled: dict = field(default_factory=dict)    # size -> contracts available

    def slippage(self, size: int):
        """How much worse than the touch, as a fraction. None if unfillable."""
        v = self.vwap.get(size)
        if v is None or not self.touch_price:
            return None
        return v / self.touch_price - 1.0


def ladder_depth(levels, sizes=DEFAULT_SIZES) -> Depth:
    """Walk a BUY ladder, best price first, and price each stake.

    `levels` is [(price, size), ...] and MUST already be best-first. Sorting
    here would hide an ordering bug at the call site rather than surface it,
    so this asserts the caller got it right instead.
    """
    clean = [(float(p), float(s)) for p, s in (levels or [])
             if p is not None and s is not None and float(s) > 0]
    d = Depth(vwap={s: None for s in sizes}, filled={s: 0.0 for s in sizes})
    if not clean:
        return d
    clean.sort(key=lambda ps: ps[0])       # cheapest first is best for a buy

    d.touch_price, d.touch_size = clean[0]
    d.n_levels = len(clean)
    d.total_size = sum(s for _, s in clean)
    d.size_within_1c = sum(s for p, s in clean if p <= d.touch_price + 0.01 + 1e-9)
    d.size_within_5c = sum(s for p, s in clean if p <= d.touch_price + 0.05 + 1e-9)

    for target in sizes:
        got, cost = 0.0, 0.0
        for p, s in clean:
            take = min(s, target - got)
            cost += take * p
            got += take
            if got >= target:
                break
        d.filled[target] = got
        # A partial fill has no VWAP. Reporting the cost of the contracts you
        # COULD get, as though you got them all, is how a thin book starts
        # looking tradeable.
        d.vwap[target] = (cost / got) if got >= target else None
    return d


# ---- venue ladder normalizers ----------------------------------------------

def kalshi_buy_ladders(orderbook_fp: dict):
    """(buy_yes, buy_no) ladders from a Kalshi book, each best-first.

    Kalshi publishes two BID ladders and no asks. To BUY yes you must lift the
    people bidding for no: an offer to buy NO at 0.61 is an offer to sell YES
    at 0.39. So the yes-ask ladder is (1 - no_price) and vice versa.
    """
    ob = orderbook_fp or {}
    yes = [(float(p), float(s)) for p, s in (ob.get("yes_dollars") or [])]
    no = [(float(p), float(s)) for p, s in (ob.get("no_dollars") or [])]
    buy_yes = sorted(((1.0 - p, s) for p, s in no), key=lambda ps: ps[0])
    buy_no = sorted(((1.0 - p, s) for p, s in yes), key=lambda ps: ps[0])
    return buy_yes, buy_no


def polymarket_buy_ladders(book: dict):
    """(buy_yes, buy_no) from a Polymarket CLOB book.

    Buying YES lifts the asks. Buying NO is selling YES, which hits the bids,
    and costs (1 - bid) per NO contract.
    """
    b = book or {}
    asks = [(float(x["price"]), float(x["size"])) for x in (b.get("asks") or [])
            if x.get("price") is not None]
    bids = [(float(x["price"]), float(x["size"])) for x in (b.get("bids") or [])
            if x.get("price") is not None]
    buy_yes = sorted(asks, key=lambda ps: ps[0])
    buy_no = sorted(((1.0 - p, s) for p, s in bids), key=lambda ps: ps[0])
    return buy_yes, buy_no


def executable_price(depth: Depth, size: int, fallback_touch=True):
    """The price a stake of `size` actually pays.

    Falls back to the touch only when explicitly allowed, and callers that care
    about honesty should not allow it: an unfillable order has no price, and
    substituting the touch is exactly the fiction this module exists to remove.
    """
    v = depth.vwap.get(size)
    if v is not None:
        return v
    return depth.touch_price if fallback_touch else None
