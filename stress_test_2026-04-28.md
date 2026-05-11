# Phase 0 Stress Test — 2026-04-28

**Pass criteria:** 8 of 10 URLs return either a full scored report OR an honest error card. No 500s. No fake/partial reports.

## Results

| # | URL | Category | Outcome | Pass | Render | Notes |
|---|---|---|---|---|---|---|
| 1 | cnn.com/...comey-again | tracked | full report 80/High | ✅ | 30-60s | clean |
| 2 | npr.org/...state-farm | tracked | full report | ✅ | 30-60s | clean |
| 3 | bbc.com/...cr7prm4ke8do | tracked | full report | ✅ | ~60s | clean |
| 4 | theguardian.com/...kimmel | tracked | full report | ✅ | ~60s | clean |
| 5 | breitbart.com/...king-charles | tracked | full report | ✅ | 30-60s | clean |
| 6 | thebulwark.com/...state-dept | untracked | full report | ✅ | ~60s | clean — confirms Substack works |
| 7 | 9news.com/...cocaine | untracked | full report 73/High | ⚠️ | 90-120s | TITLE = URL slug; callout undercounts (Plausible not in opening) |
| 8 | chicagotribune.com/...hospital | untracked | full report | ✅ | 30-60s | clean — confirms 9news title bug is publication-specific |
| 9 | wsj.com/...uae-opec | paywalled | partial report 100/High | ❌ | 60-90s | TITLE = URL slug; paywall not detected; only 1 trivial historical claim extracted; misleading 100 score |
| 10 | theatlantic.com/...tax-revolt | paywalled | full report 40/Medium | ✅ | 30-60s | initially flagged as opinion miss-classification, on review the methodology was applied correctly: factual claims in opinion essays are still scoreable; verdicts (2 Overstated) were methodology-defensible |

## Result: 9 of 10 PASSED

## Issues Identified (do not launch until resolved)

### Issue A: URL-slug-as-title fallback firing too often — HIGH
- **Affected:** 9news (test 7), WSJ (test 9). 2 of 10 submissions = 20% rate.
- **Root cause:** When direct scrape and Jina both fail to find a `<title>` tag, system falls back to URL slug → hyphens-to-spaces → titlecase. Result is unreadable: "73 0F8F9Cc5 E67E 4C90 8332 C93A5C6506Df?Tbref=Hp".
- **Fix:** Better title fallback chain. Try `<meta property="og:title">`, `<meta name="twitter:title">`, `<h1>` before URL slug. URL slug should be last resort with cleanup (strip GUIDs, query params, hash fragments).

### Issue B: Paywall detection missing — HIGH
- **Affected:** WSJ (test 9). Will affect WSJ, FT, NYT (post-meter), Atlantic (post-meter), Bloomberg.
- **Root cause:** No paywall-marker detection. System has BOT_TITLES set for Cloudflare/CDN walls but no equivalent for paywall pages (which return real-looking but truncated content).
- **Fix:** Detect paywall markers in retrieved content: short body length combined with phrases like "subscribe to read", "for subscribers", "create an account to continue", "register to keep reading". Route to "Paywall detected" error card when matched.

### Issue D: Methodology callout undercount — MEDIUM
- **Affected:** Articles where claims include Plausible or Corroborated verdicts (test 7).
- **Root cause:** Opening line of callout text only sums `c[5]=='supported'` and the trio (Overstated/Disputed/Not_supported). Plausible and Corroborated verdicts don't appear in the opening summary, but appear correctly in the verbose breakdown that follows.
- **Fix:** Update callout-builder logic at api.py line ~785 to include Plausible/Corroborated in the opening summary.

## Items considered but determined NOT to be issues

### Opinion section URL detection (originally proposed as Issue C)
- Initial concern was that the Atlantic /ideas/ article got scored as factual when it should have been classified as opinion.
- On methodology review: Verum Signal v1.5 evaluates factual claims wherever they appear. The Atlantic essay's two factual claims were correctly assessed (both Overstated, with substantive reasoning). The Opinion *verdict* is for claims that are assertions of view rather than assertions of fact — applied at the per-claim level, not the article level.
- Skipping claim extraction based on URL patterns would actually violate the methodology by refusing to evaluate verifiable claims based on where they appear.
- Decision: not a bug. System working as designed.

## Performance observations

- 9 of 10 rendered in 30-90s range
- 1 outlier at 90-120s (9news)
- No timeouts, no 500s, no service crashes during test
- Pipeline (fetch → GDELT → extract → load → verify) running stable under systemd

## What this test validated

- All 5 tracked outlets work cleanly across the political spectrum
- Untracked outlets work via on-demand extraction (Bulwark/Substack, Chicago Tribune)
- Random shuffle in claim extraction producing balanced ingestion
- analyse_claim None guard works (Daily Wire URL works without 500)
- Article-vs-outlet score split renders correctly across tier states (Stabilizing, Excluded, Insufficient)
- Methodology callout duplication fix from this morning landed cleanly
- Line 688 dead code cleanup landed cleanly
- Methodology v1.5 holds up under stress: 2 of 10 articles surfaced legitimate verdicts that demonstrate methodology fidelity (Atlantic Overstated reasoning, CNN Overstated reasoning)

