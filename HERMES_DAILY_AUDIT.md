# Hermes standing order — daily quality audit

Run this on demand, typically morning and evening, while the collection window is open
(through 2026-08-28). Two runs a day is enough; the tracker is hourly and self-healing.

## The command

```bash
cd C:\Users\SURFACE\flight_tracker && git pull --quiet && python tools/audit.py
```

`git pull` first, always. The Actions runner is the source of truth — the local clone is stale
between pulls, and auditing a stale CSV produces a false "stalled" verdict.

## Division of labour

**`tools/audit.py` decides. You explain.** Every check has a hard threshold in the script, so
the verdict is reproducible. Do not re-grade it, soften it, or substitute your own judgement
about whether something "looks fine". If you think a threshold is wrong, say so as a separate
recommendation — do not quietly work around it.

Exit codes: `0` = HEALTHY, `1` = DEGRADED (at least one WARN), `2` = BROKEN (at least one FAIL).

## What to do per verdict

**HEALTHY (0)** — report the summary block and stop. No investigation. Three lines is a
complete report; do not pad it.

**DEGRADED (1)** — report, then investigate only the specific WARN checks:

- `coverage` — open the Actions tab, find the missed hours. GitHub skipping or delaying a
  scheduled run by 5–20 minutes is normal and is not a defect. Report the count and move on
  unless the gap exceeds 3 consecutive hours.
- `error_rate` / `date_coverage` — read the `error` column text and the run's uploaded
  diagnostics artifact. Report the verbatim error string.
- `price_jump` — check whether the move is real (fare genuinely changed) or a parsing artifact
  (e.g. a Business Class card leaking into the Economy filter). Compare against the raw CSV rows.
- `duplicates` — likely two runs overlapped. Check the concurrency group is still in the workflow.

**BROKEN (2)** — report immediately with the failing check, the last known good run timestamp,
and the most recent 3 error strings. Then stop and wait for Subu. Do not attempt a repair on
your own initiative.

## Report format

Keep it this short. Subu reads this twice a day; length is a cost.

```
AUDIT <date> <HH:MM IST> — <VERDICT>
Freshness: <x> min | Coverage: <n>/<m> | Errors: <n>/<m> (<pct>)
Cheapest: Sep1 ₹x | Sep2 ₹x | Sep3 ₹x
<one line per WARN/FAIL, with the specific finding — omit this section entirely if HEALTHY>
Action: <none needed | what you propose | escalating>
```

## Hard rules

- **Every local or sandbox scraper run MUST pass `--csv` with a temporary output path.**
  For example: `python src/scrape_google_flights.py --csv "$TEMP/flight_prices_sandbox.csv"`.
  Never run the scraper locally against `data/flight_prices.csv`, and never restore or overwrite
  that production file after a local run.
- **Never edit, backfill, reorder, or delete rows in `data/flight_prices.csv`.** Gaps are data.
  A missing hour tells us the schedule slipped; a fabricated row destroys that signal
  permanently. This applies even if a gap looks trivially fillable.
- **Never rewrite git history** on this repo (no `push --force`, no rebase of pushed commits).
  The commit series is the audit trail for when each fare was observed.
- **Never relax a threshold in `tools/audit.py` to make a run pass.** Propose the change and
  explain the reasoning; Subu decides.
- **Never add a secret, PAT, or credential.** Nothing in this project needs one.
- If the scraper needs a fix, produce the diff and the reasoning first. Do not push scraper
  changes mid-window without approval — a mid-flight change makes the before/after data
  non-comparable.

## Known non-issues — do not raise these as findings

- `flight_number` blank on a majority of rows. The deterministic
  `airline|dep|arr|nonstop|Economy` fallback in `flight_key` is intentional and keeps each
  itinerary uniquely identifiable. It is reported as INFO, not a defect.
- Scheduled runs landing at :20–:40 instead of :17. GitHub cron is best-effort.
- The window countdown shrinking. Expected; the workflow self-disables after 2026-08-28.
