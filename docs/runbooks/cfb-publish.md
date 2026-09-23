# Runbook: publishing the CFB export

The first CFB publish lands a whole section's data at once. Why the path is shaped
this way: `jobs/export_cfb_web.py`'s docstring (c-05). What was measured before this
runbook was written: `_relay/reports/track-c.md` for unit c-10.

**This is Ethan's run.** The export writes into `WEB_EXPORT_DIR`, and the scheduled
`weekly_refresh` uploads every key in that tree whose bytes changed. Writing `cfb/`
there IS the publish, even before anyone runs the upload by hand.

---

## Publishing the data fills no page on its own

On web `main` (checked at `9884f96`), every CFB route tests `config.awaitingExport`
**before** it reads the manifest. While `config/sports/cfb.ts` says
`awaitingExport: true`, a published CFB tree changes nothing a reader sees on
`/cfb`, `/cfb/teams`, `/cfb/players`, `/cfb/analytics` or `/cfb/live`. The only new
reader-visible surface is the raw JSON at `/data/cfb/...`.

That is what makes data-first safe here (the change is additive), and it is also why
the publish is only half of "the pages fill". **Do not flip the flag until `/cfb` has
a CFB home:** with the flag off, `app/[sport]/page.tsx` renders the NFL `SportHome`,
which states "{counts.players} players." (0 for CFB), the Kalshi between-slates copy,
and links to `/cfb/fantasy` and `/cfb/news`, neither of which is a declared route.

**Checked by rendering, c-13 (2026-09-23): true, and incomplete.** It is the shared `SportHome`,
whose copy and card list are NFL-shaped, not a separate NFL page. `/cfb/players` is worse than
`/cfb`. The Shell already reads the manifest on `/cfb` while the flag is on. Every published
spread has the wrong sign. The checklist that replaces this paragraph is
`docs/C13-cfb-gate.md`, and the web half is `docs/track-b-requests.md` B-C13-1..7.

## Preconditions: check them, don't assume them

1. **The checkout contains c-05's exporter.** `python -m jobs.export_cfb_web --help`
   must list `--dest {own,web}`. On a checkout without it, `--dest` is an unknown
   argument.
2. **`WEB_EXPORT_DIR` is clean against its upload record.** Every key a run uploads is
   a key that differs, from ANY producer. A CFB publish on top of a pending NFL change
   publishes both. At c-10 (2026-09-22 ~20:45 ET) it was 23,392 local, 23,392 in the
   record, 0 changed, 0 absent, 0 under `cfb/`. That figure goes stale on the next NFL
   export or `weekly_refresh`. Re-measure it with the dry run in step 2 below and read
   `changed`: it should equal the CFB key count and nothing more.
3. **The staged tree is current.** `python -m jobs.export_cfb_web` (staging, the
   default) must print `written 0` on a second run.

## The run

From a checkout whose `.env` carries `WEB_EXPORT_DIR` and the `WEB_R2_*` settings
(today, the `calibrated-sports` clone, once the exporter has landed there):

    python -m jobs.export_cfb_web --dest web
    # expect: files 140 ... and a last line "REFRESHED cfb/"

    python -m jobs.export_web --upload-only --refreshed "cfb/" --dry-run
    # read it: changed == 140 (all cfb/), removed 0, removed_withheld 0

    python -m jobs.export_web --upload-only --refreshed "cfb/"

`--refreshed "cfb/"` scopes deletion to `cfb/`. Omit it and nothing is deleted.

## Verify from outside: the page, not the export

- Fetch at least four `/data/cfb/...` keys over HTTP (the manifest and three team
  files) and compare each against the local file, ignoring nothing. One sample is not
  a check.
- Fetch `/cfb` and `/cfb/teams`. While the flag is on, they must still show the
  awaiting state. If they changed, something other than this publish changed them.

## Rolling back

Delete the local `cfb/` directory under `WEB_EXPORT_DIR`, then run
`python -m jobs.export_web --upload-only --refreshed "cfb/"`. Deletion is scoped to
the declared prefix, so this removes the CFB keys from R2 and touches nothing else.
Run it with `--dry-run` first and read `removed`.

## After the first publish

Wire `jobs.export_cfb_web --dest web` into `weekly_refresh` as a non-fatal step whose
`parse_refreshed` output joins `concat_declarations`, as the analytics step does.
Until then, published CFB keys go stale, but they are never deleted.
