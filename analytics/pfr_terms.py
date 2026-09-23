"""Sports-Reference's terms, per column the drafting predictor reads (f-07).

THE QUESTION WAS "SILENT OR PROHIBITS?" AND THE ANSWER IS NEITHER. The posture
settled on 22 September - publish where terms are silent, refuse where they
prohibit - does not reach these columns, because Sports-Reference's Terms of Use
(last updated May 19, 2023, read 2026-09-22) are not silent on republishing.
They PERMIT it, expressly, with conditions, in section 5:

    "sharing, using, modifying, repackaging, or publishing data found on
    individual SRL webpages is welcomed, whether for commercial or
    non-commercial purposes, but (2) any such sharing, use, modification,
    repackaging, or publication should explicitly credit SRL as the source of
    the data to the maximum extent possible and (3) ... must not violate any
    express restrictions set forth in this Section 5, especially the
    restrictions set forth in subparts 5(i) and 5(j) below."

So a column is publishable here when three things hold, and each is checked
below rather than assumed: the file CREDITS Sports-Reference (the attribution
block this module supplies travels in the predictor file); what is published
is not "a database, archive, or other data store that competes with or
constitutes a material substitute" for theirs (5(i) - the predictor publishes
32 team aggregates per slice and no per-pick row, asserted in the tests); and
nothing is used "for purposes of training, fine-tuning, prompting, or
instructing artificial intelligence models ... or (ii) supporting machine
learning methods used to predict, classify, label, or score inputs into the
models" (5(j) - the drafting record is a walk-forward Pearson r between a
team's closed-class mean and its next class, which is not a model trained on
anything; that is a READING of 5(j), flagged as such in `JUDGEMENTS`).

PROVENANCE, because nflverse's position is not PFR's. We never fetch from
Sports-Reference. `draft_picks` is nflverse's release "courtesy of Pro Football
Reference" (release notes and nflreadr's `load_draft_picks`), and `snap_counts`
is "provided by Pro Football Reference" (nflreadr's `load_snap_counts`). The
nflverse-data repository is CC-BY-4.0, which licenses what nflverse holds
rights in and cannot grant what it does not; nflverse states no permission from
Sports-Reference. So the terms are applied to these columns AS IF we had read
them off the site, which is the stricter of the two readings: a party that
never accessed the site is arguably not bound by its terms at all, and nothing
here relies on that argument.

THE ONE LINE THAT READS THE OTHER WAY is on the "SR and Data Use" page, which
glosses the terms: "you should not create websites or tools based on data you
scrape from Sports Reference or any of our sites ... without our permission."
It is quoted in `QUOTES` because a reader deciding this must see it. It is read
here as a gloss on 5(i) and the automated-access clause - it follows directly
from them on that page - and not as a prohibition on republishing that section
5's own text expressly welcomes. That is a judgement, not a measurement, and it
is the first thing to reverse if Sports-Reference says otherwise.
"""

TERMS_URL = "https://www.sports-reference.com/termsofuse.html"
DATA_USE_URL = "https://www.sports-reference.com/data_use.html"
TERMS_LAST_UPDATED = "2023-05-19"
TERMS_READ = "2026-09-22"

# Verbatim from the pages above, as read on TERMS_READ. Both directions.
QUOTES = {
    "permits": (
        "Our guiding principles are that (1) sharing, using, modifying, "
        "repackaging, or publishing data found on individual SRL webpages is "
        "welcomed, whether for commercial or non-commercial purposes, but (2) "
        "any such sharing, use, modification, repackaging, or publication "
        "should explicitly credit SRL as the source of the data to the maximum "
        "extent possible and (3) any such sharing, use, modification, "
        "repackaging, or publication must not violate any express restrictions "
        "set forth in this Section 5, especially the restrictions set forth in "
        "subparts 5(i) and 5(j) below."),
    "encourages": (
        "we encourage the sharing and reuse of data and statistics our users "
        "find on our Site as a general matter, but our business obviously would "
        "be harmed if a bad apple were to abuse that privilege to create a "
        "competing statistical database or to copy a materially significant "
        "portion of our data."),
    "prohibits_5i": (
        "use any material or Content from the Site, including without "
        "limitation any statistics or data, (i) to create any database, "
        "archive, or other data store that competes with or constitutes a "
        "material substitute for the services or data stores offered on the "
        "Site or by the Site's Data Providers or (ii) to provide any service "
        "that competes with or constitutes a material substitute for the "
        "services or data stores offered on the Site or by the Site's Data "
        "Providers"),
    "prohibits_5j": (
        "copy or use any material or Content from the Site, including without "
        "limitation any statistics, data, text, graphics, or images, for "
        "purposes of training, fine-tuning, prompting, or instructing "
        "artificial intelligence models or technologies in any manner, "
        "including without limitation for purposes of (i) generating answers, "
        "text, scores, statistics, notes, graphics, images, or any other "
        "output; or (ii) supporting machine learning methods used to predict, "
        "classify, label, or score inputs into the models"),
    "prohibits_automation": (
        "without our express written permission, use any automated means to "
        "access or use the Site, including scripts, bots, scrapers, data "
        "miners, or similar software, in a manner that adversely impacts site "
        "performance or access"),
    "data_use_gloss": (
        "This means that you should not create websites or tools based on data "
        "you scrape from Sports Reference or any of our sites or use our data "
        "to train generative artificial intelligence models without our "
        "permission."),
    "facts": (
        "As an aside, copyright law is clear that facts cannot be copyrighted, "
        "so you are free to reuse facts found on this site in accordance with "
        "copyright laws."),
    "some_licenses_preclude": (
        "For some of our datasets, our licenses completely preclude any "
        "redistribution of the data."),
}

# Where a reading was needed rather than a quote. Each is reversible.
JUDGEMENTS = [
    "5(j) is read as not reaching the drafting record: a walk-forward Pearson r "
    "between a team's closed-class mean and its next class trains no model. A "
    "LEARNED model fitted on AV or PFR snap counts would be a different case, "
    "and this module does not clear one.",
    "The data-use page's 'should not create websites or tools based on data you "
    "scrape' is read as a gloss on 5(i) and the automated-access clause, not as "
    "a prohibition overriding section 5's express permission. We do not scrape; "
    "nflverse does.",
    "'Some licenses completely preclude any redistribution' names no dataset. "
    "Neither draft tables nor snap counts are marked on PFR as licensed-in, but "
    "that was not verified page by page (the PFR pages refuse this machine's "
    "client with HTTP 403).",
]

ATTRIBUTION = {
    "statement": ("Draft picks, Approximate Value and snap counts: "
                  "Pro-Football-Reference.com, a Sports Reference LLC site, "
                  "via nflverse. Team figures here are derived; no "
                  "Pro-Football-Reference table is reproduced."),
    "source": "Pro-Football-Reference.com (Sports Reference LLC)",
    "url": "https://www.pro-football-reference.com/",
    "terms_url": TERMS_URL,
    "terms_read": TERMS_READ,
}

# How a column is used decides what the terms ask of it.
#   published   - enters a published team aggregate: needs the credit, and
#                 5(i) / 5(j) must hold for what is published
#   join_only   - an identifier used to link rows, never published
#   diagnostic  - read for a printed count, never published
USES = ("published", "join_only", "diagnostic")

# nflverse's draft_picks dictionary lists these as PFR's; `gsis_id` is the one
# column nflverse adds ("ID for joining with nflverse data"). snap_counts is
# PFR-provided whole.
PFR_DATASETS = {
    "draft_picks": ("season round pick team pfr_player_id cfb_player_id "
                    "pfr_player_name hof position category side college age to "
                    "allpro probowls seasons_started w_av car_av dr_av games "
                    "pass_completions pass_attempts pass_yards pass_tds "
                    "pass_ints rush_atts rush_yards rush_tds receptions "
                    "rec_yards rec_tds def_solo_tackles def_ints "
                    "def_sacks").split(),
    "snap_counts": ("game_id pfr_game_id season game_type week player "
                    "pfr_player_id position team opponent offense_snaps "
                    "offense_pct defense_snaps defense_pct st_snaps "
                    "st_pct").split(),
}

# Every PFR column `analytics.drafting` reads, and how. `tests/test_pfr_terms`
# walks drafting.py and fails on a PFR column read that is not listed here.
COLUMNS = {
    ("draft_picks", "season"): ("published", "draft class; a public fact"),
    ("draft_picks", "pick"): ("published", "draft slot; a public fact, used to "
                              "form the slot expectation"),
    ("draft_picks", "team"): ("published", "drafting club; a public fact"),
    ("draft_picks", "category"): ("published", "position group, used only for "
                                  "the per-position refit"),
    ("draft_picks", "w_av"): ("published", "Sports-Reference's own Approximate "
                              "Value; the w_av slice aggregates it per team"),
    ("draft_picks", "pfr_player_id"): ("join_only", "links a pick to snap counts "
                                       "and roster rows"),
    ("draft_picks", "pfr_player_name"): ("join_only", "a check on the gsis "
                                         "recovery, never a key"),
    ("draft_picks", "games"): ("diagnostic", "counts id-less picks with games "
                               "for a printed line"),
    ("snap_counts", "season"): ("join_only", "aligns snaps to a pick's horizon"),
    ("snap_counts", "game_type"): ("join_only", "regular season filter"),
    ("snap_counts", "pfr_player_id"): ("join_only", "links snaps to a pick"),
    ("snap_counts", "offense_snaps"): ("published", "summed into snaps4, "
                                       "published per team"),
    ("snap_counts", "defense_snaps"): ("published", "summed into snaps4, "
                                       "published per team"),
}

# The brief named four columns. Two of them are not read at all or not
# published, and saying so is the answer for them.
BRIEF_COLUMNS = {
    "w_av": "published in the w_av slice, with credit",
    "dr_av": "not read by any outcome; nothing to decide",
    "games": "read for one diagnostic count; never published",
    "snap_counts": "offense_snaps + defense_snaps published in snaps4, with credit",
}


def column_verdict(dataset, column):
    """What the terms require of one column as the predictor uses it.

    `credit_required` for a published PFR column, `none` for one that is only
    joined on or counted, and a KeyError for a column nobody classified - an
    unclassified read is a question, never a default.
    """
    use, _ = COLUMNS[(dataset, column)]
    if use not in USES:
        raise ValueError("unknown use %r for %s.%s" % (use, dataset, column))
    return "credit_required" if use == "published" else "none"


def slice_datasets():
    """Which PFR-provided datasets each drafting slice publishes from. Every
    slice rests on the draft order, which is PFR's copy of a public fact."""
    return {"roster4": ["draft_picks"], "bust": ["draft_picks"],
            "snaps4": ["draft_picks", "snap_counts"],
            "w_av": ["draft_picks"]}


def attribution_block(slices):
    """The credit section 5 asks for, naming the slices it covers."""
    return dict(ATTRIBUTION, applies_to=sorted(slices))
