# Session Notes — Day 12 → Day 13 Handoff

**Last session:** 2026-04-28 (Day 12 — full afternoon/evening)
**Next session:** 2026-04-29 (Day 13)
**Days since project start:** 12

## How to use this document

Read this BEFORE starting any work tomorrow. It contains:
- What was shipped today and is now in production
- What's confirmed working
- Known issues documented but not yet fixed
- Action items for next session, prioritized
- Important methodology clarifications that came up during today's session
- Workflow patterns that worked well

## Where the project is right now

**Up and running:**
- Live site at https://www.verumsignal.com — healthy, responsive
- Scheduler running under `systemd` as `veris.service` — stable, auto-restarts on crash, logs to `journalctl -u veris.service`
- Pipeline cycling every 3 hours with all 7 steps: Fetch → GDELT → Extract → Load → Pre-verify → Score priorities → Assign verdicts
- Database: ~13,165 articles, ~4,049 claims, ~1,030 verified
- Ingestion is now politically balanced for the first time in project history (BBC/CNN/NPR/Fox/NYT all in top 10 in any 30-min window; previously was Bloomberg/Fox-dominated)

**Eight production deploys today, all live:**
1. Random shuffle in claim extraction (kills feed-order bias)
2. GDELT port fix (5432 → 35370)
3. GDELT exception handler (logs failures with type, returns `[]`)
4. Methodology callout duplication fix (api.py:785)
5. Line 688 dead code cleanup (no_claims dict shape)
6. Title fallback chain (Issue A — og:title, twitter:title, h1, cleaned slug)
7. Paywall detection (Issue B — `_is_paywall` helper, wired into all helpers, plus probe)
8. Methodology callout undercount (Issue D — Plausible/Corroborated buckets)

Plus: 2 cached articles with bad titles re-cleaned via DB UPDATE.

## What was discovered about systemd

Surprise from today: there was already a systemd service `veris.service` running the scheduler — Britt didn't realize this was set up. It's at `/etc/systemd/system/veris.service`. We discovered it after killing schedulers manually and they kept respawning.

**Important practical implications:**
- Don't kill scheduler PIDs manually expecting them to stay dead — systemd will respawn within seconds
- To stop the scheduler: `sudo systemctl stop veris.service`
- To restart: `sudo systemctl restart veris.service`
- To see logs: `sudo journalctl -u veris.service -f` (live tail) or `sudo journalctl -u veris.service --since "1 hour ago" --no-pager`
- The service file has a typo on lines 2-4 (stray `y` before `[Unit]`) — cosmetic, doesn't affect operation, but worth fixing

**Why the pre-fix scheduler was producing empty `claims_*.json` files:** The systemd-launched process was running with whatever environment was current when systemd started it. We confirmed `WorkingDirectory=/home/veris/projects/veris` is set correctly, so `os.path.exists(".env")` finds the file. But for some reason the systemd-launched scheduler was still failing to extract claims. We never definitively figured out the historical cause; the current state is working because the new scheduler launched today after explicit env loading. If issues recur, check `cat /proc/PID/environ | tr '\0' '\n' | grep ANTHROPIC` to see if the running scheduler has the API key. If it doesn't, the fix is `sudo systemctl restart veris.service` (which on its own should work because of `WorkingDirectory`).

## Issues documented but not yet fixed

These came up during Day 12 work and are explicitly NOT blockers, but should be tackled in tomorrow's or a future session.

### A. Sources_used display rendering — investigate next

When the verifier finds independent corroboration but expresses it in prose (e.g., "court filings", "WSJ and Reuters reporting", "the Eagle County Sheriff's Office press release"), the source-extraction logic in `api.py` rendering can fail to pull discrete domain pills. Result: report shows "No independent sources found" even though the verifier's `full_analysis` field clearly cites multiple sources.

Example case: WSJ Comey article (Day 12 retest) — verdict was Supported with detailed analysis citing court filings and statute-of-limitations dates, but Sources Consulted showed "No independent sources found".

**Hypothesis:** The source-name parser in `api.py` is extracting domain names from `sources_used` field. When the verifier returns prose like "WSJ and Reuters reporting, plus court filings from..." it can't extract clean domains. The parser needs to either:
- Better handle prose (extract entity names like "WSJ", "Reuters" without domain)
- OR change the verifier prompt to return a structured list of `{name, url}` objects

**Recommended next step:** Read `api.py` source-rendering logic (around line 1080-1100), find where sources_used gets parsed into pills, decide between (a) better parser or (b) prompt change. Both have tradeoffs.

### B. WSJ-style title currency stripping

`Los Angeles Megamansion Asks $135 Million` came back as `Los Angeles Megamansion Asks Million` — the dollar sign and number were stripped during extraction. Likely happens in BeautifulSoup HTML entity handling or a regex. Affects only titles with $-amounts. Narrow scope.

**Recommended next step:** Lower priority — investigate during a quieter session. Probably in `_try_direct_scrape`'s og:title handling.

### C. URL-slug acronym joining (e.g., "U A E" should become "UAE")

The `_clean_url_slug` helper produces "U A E To Leave Opec Opec" because URL slugs separate every letter of acronyms with hyphens. We discussed adding a second-pass collapse rule but decided not to ship — too risky without a test suite (could break "U Penn" or "I am" cases).

**Recommended next step:** Tackle as v1.1 polish. Low priority since og:title fallback usually catches the real headline.

### D. Cached articles with bad titles (now fixed for the 2 known cases)

Only 2 articles had bad cached titles from today's testing (9news, WSJ). Both were manually re-cleaned via DB UPDATE using the new `_clean_url_slug` helper. **No mass migration needed** — diagnostic confirmed the broader DB is clean.

## Action items for next session, in priority order

### Priority 1: Methodology page stubs (Phase 0 blocker)

The methodology page references sections 2.5, 7.5, and 09 that need real content. Per Bible: pre-launch defensibility. Pure prose work, no code.

- Section 2.5: TBD (check current methodology page to see what surrounding content suggests)
- Section 7.5: TBD
- Section 09: TBD

**Recommended approach:** Pull up the current methodology page on the live site, see what each section needs to cover. Draft language outside Verum Signal first (in a Google Doc or text file), then commit. This is the kind of work that benefits from a fresh head — don't try to rush it.

### Priority 2: Claim extraction depth (Phase 1 prep)

Change `extract_claims.py:63` from "top 3 most check-worthy" to "top 3-5 most check-worthy claims, depending on article's length and density of factual content."

Rationale: Today's 2-3 claims per report sometimes understates analytical depth on longer articles. Going to 3-5 (variable by article length) gives better signal-to-noise than a flat 5.

**Cost impact:** Modest. Each additional claim is one Anthropic web search call (~$0.05-0.15). Per-article cost increases ~33-67%. Per-day cost goes from ~$120 to ~$160-200 at current volume.

### Priority 3: Sources_used display issue (Phase 0)

See Issue A above. This is the most methodology-relevant issue remaining. A user submitting an article and seeing "No independent sources found" alongside a 100/High score will lose trust in the system.

### Priority 4: Cost optimization (Phase 1 prep)

Bible threat-mitigation: target $0.08-0.15/report (currently $0.36-0.70). Specific items:
- Claim caching (24hr window via pg_trgm — already exists, review effectiveness)
- Batch API for pre-verification (NEW)
- Smarter dedup
- Tiered verification depth

### Priority 5 and below: Items in ROADMAP_2026-04-29.md

The roadmap has the full Phase 1-4 list. Pull from there as you finish higher-priority items.

## Important methodology clarifications from Day 12

These came up during conversation today and are worth documenting because they shaped today's decisions:

### Opinion verdicts apply per-claim, not per-article

We initially proposed an "Issue C: Opinion section URL detection" that would have skipped claim extraction for /opinion/, /ideas/, /op-ed/ URLs. **This was rejected as a methodology violation.**

Reasoning: Methodology v1.5 evaluates factual claims against evidence wherever they appear. Opinion essays often contain factual claims that ARE verifiable (statistics, dates, court records). Pre-classifying entire articles by URL would refuse to evaluate verifiable content based on where it sits, which contradicts the methodology.

The Atlantic /ideas/ test article got 2 Overstated verdicts on factual claims embedded in an opinion piece — this was correctly methodology-applied. The Opinion verdict applies to per-claim assertions of view (like "this policy will fail"), not to all content from opinion-section URLs.

### Verdicts and their scoring weights

Just to have it explicitly written somewhere:
- Supported: +1.0
- Corroborated: +0.5
- Plausible: +0.5
- Overstated: -0.5
- Disputed: -1.0
- Not_supported: -1.5
- Not_verifiable: excluded from scoring
- Opinion: excluded from scoring

### Independence rule and consensus exception

These are tightly defined in the verdict_engine prompt at `verdict_engine.py:91-200`:
- Two sources are only independent if they obtained info via different means
- Wire-service copies = ONE source
- ≥5 outlets consistently reporting → Supported at confidence 2/3 (consensus exception)
- Any credible contradiction → Disputed regardless

## Workflow patterns that worked well today

### Patch-then-verify-then-deploy

Every code change followed this pattern:
1. Write a Python script in `/tmp/patch_X.py` using `cat <<'PYEOF'` heredoc
2. Run it to apply the patch
3. Verify with `python3 -c "import ast; ast.parse(open('FILE').read()); print('PARSE OK')"` plus a `grep` for the new pattern
4. (If applicable) sanity-test by importing the module and calling the new function
5. Deploy with `git add FILE && git commit -m "..." && git push`
6. Wait ~30-60s for Railway deploy
7. Test on live site

This worked very well. **Do not deviate** — the heredoc approach makes patches reproducible and reversible. Inline `sed -i` or `awk` substitutions on api.py are dangerous because the file is large and complex.

### Small patches over large ones

Issue A (title fallback) was split into 4 smaller patches: helper, direct-scrape update, jina update, web-search update. Each one was independently verifiable. When Patch 1 had a duplicate-injection bug, only Patch 1 had to be re-done — Patches 2-4 weren't affected.

Issue B (paywall detection) was split into 3 smaller patches with the same approach. One of them errored silently (heredoc quoting issues — recurring problem this session) and we caught it by checking the file state before writing the next patch.

**Recommendation for tomorrow:** Continue this pattern. If a fix touches more than 2 functions or 20 lines, split it.

### Ground rules during stress test

For Phase 0 stress test:
- One URL at a time
- Fully resolve before next
- 500s = stop, capture trace, decide whether to fix-and-continue or document-and-skip
- No re-testing same URL after a failure (first-attempt result is what counts)
- 90s soft threshold for time-to-render

These rules kept the test honest. Don't soften them for v2 stress tests.

## Critical files reference

- `~/projects/veris/api.py` — Flask app, ~1330 lines. Contains: `_clean_url_slug` (line 391), `_is_bot_protection`, `_is_paywall` (line 392), `_try_direct_scrape` (line 444+), `_try_jina_reader`, `_try_web_search`, `fetch_article_content` (line 533+), `report_page` route, methodology callout builder (line 875+).
- `~/projects/veris/extract_claims.py` — Claim extractor. Line 63 has the "top 3" prompt. Line 136 has the random shuffle.
- `~/projects/veris/scheduler.py` — systemd-launched. Line 52 has the GDELT port fix. Line 63+ calls run_full_pipeline.
- `~/projects/veris/gdelt_seed.py` — Line 50 has the new fail logger.
- `~/projects/veris/load_to_database.py` — Handles JSON → DB insert with ON CONFLICT DO NOTHING.
- `~/projects/veris/verdict_engine.py` — Line 91 `analyse_claim`, line 48 `check_database_first`. Verdict logic, prompt, definitions.
- `~/projects/veris/feeds.py` — RSS feed config, ~50+ outlets.
- `~/projects/veris/.env` — Gitignored. Has DB credentials and ANTHROPIC_API_KEY. CRITICAL DO NOT COMMIT.
- `/etc/systemd/system/veris.service` — systemd unit. Has typo lines 2-4 (cosmetic).
- `~/projects/veris/stress_test_2026-04-28.md` — Phase 0 test record (5,010 bytes).
- `~/projects/veris/BIBLE_2026-04-29.md` — Updated bible.
- `~/projects/veris/ROADMAP_2026-04-29.md` — Updated roadmap.
- `~/projects/veris/SESSION_NOTES_2026-04-29.md` — This document.

## Today's commits in chronological order

- `686a41c` — analyse_claim None guard + article/outlet score split foundations (Day 11 evening)
- `0649614` — top-of-page article only, bottom outlet only, mid-page inset removed (Day 11 evening)
- (random shuffle, GDELT port, GDELT exception handler — Day 12 morning, no separate commit hashes given in notes)
- `48106a3` — methodology callout duplication fix
- `376c787` — line 688 dead code cleanup
- `712cae0` — title fallback chain (og:title, twitter:title, h1, markdown headings, cleaned slugs)
- `322ab60` — paywall detection (helper, wire into 3 fetch helpers, probe on total failure, route to paywall card)
- `e2bf87b` — methodology callout opening summary now includes plausible and corroborated buckets

## Honest assessment of where the project sits

Day 12 was meaningful. Eight production fixes shipped, all confirmed working. The system went from "ingestion broken for ~10 days, leaderboard skewed conservative, pipeline producing empty output" to "balanced ingestion, all major edge cases handled, Phase 0 stress test 9/10 passing."

That said:
- Outlet score scaling will take time. Currently 1 outlet at "Stabilizing" and the rest at "Insufficient" or "Excluded." This isn't a code problem — it's a data accumulation problem. The fixes today should accelerate it, but you won't see meaningful leaderboard density for 2-3 weeks of normal operation.
- The methodology page is the most important pre-launch work and is the most likely thing to be picked apart by a critical journalist. It needs careful drafting in a fresh session.
- The cost ratio (currently $0.36-0.70/report) is unsustainable for unmoderated public access. Cost optimization needs to land before any wide promotion.

## Final note on tone

Today involved several rounds where the model proposed a fix and Britt pushed back on whether it aligned with methodology. Examples:
- Proposed Opinion-section URL detection → Britt correctly rejected (methodology violates own principles)
- Proposed combining Corroborated + Supported in callout summary → Britt correctly rejected (no need to change verdict logic)
- Proposed "consistent" as Plausible label → Britt correctly identified the term was confusing

These pushbacks improved decisions. **The pattern to continue: when the model proposes something, ask whether it actually solves the problem or just creates a new one.** Methodology defensibility matters more than code elegance.

