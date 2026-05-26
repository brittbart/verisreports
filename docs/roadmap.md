# VERUM SIGNAL — ROADMAP

**The phase-by-phase plan from current state (May 21, 2026) through acquisition target (end of 2026 / early 2027).**

- **Last updated:** May 21, 2026
- **Companion:** PROJECT_BIBLE.md (what is), TECH_PAPER.md (how it works), SESSION_HANDOFF.md (what's next)
- **Cadence:** updated at every major milestone

---

## How to read this document

The roadmap is organized by phase, not by date. Each phase has:
- A goal
- Required preceding phase(s)
- Estimated duration
- Specific deliverables
- Decision points

Phases run in parallel where possible. Sequential dependencies are explicit.

---

## Phase map (single page)

| Phase | Goal | Duration | Status |
|---|---|---|---|
| **A. v1.7 backend deploy** | Schema, code, methodology stamping live | 1 day | ✅ COMPLETE (May 21) |
| **A.1. Auto-promotion job** | Provisional → final promotion every 5 min | 2 hours | ✅ COMPLETE (May 21) |
| **A.2. Pre-debate verification** | Verify debate_routes.py writes provisional | 1 hour | 🟡 IN PROGRESS |
| **B. May 26 debate coverage** | First post-v1.7 live debate | 1 day (live) | 🟡 NEXT (May 26) |
| **C. Attorney approval** | Methodology v1.7 cleared for public | 1-2 weeks | 🟡 IN FLIGHT |
| **D. Public methodology page update** | data.js → v1.7, archive v1.6 | 2-3 hours | 🔴 GATED on Phase C |
| **E. Parallel verifier promotion** | Flip shadow → production | 30 min + monitoring | 🟡 ACCUMULATING DATA |
| **F. Backfill remaining queue** | ~907 claims to verdicts | Organic via scheduler | 🟡 IN PROGRESS |
| **G. Session 6 post-deploy validation** | Confirm v1.7 stable for 7+ days | 4 hours | 🔴 GATED on Phase D + 7 days |
| **H. Methodology paper drafting** | Defensible IP artifact | 1-2 weeks | 🔴 GATED on Phase G |
| **I. Render migration** | Production maturity, escape Railway | 5-6 weeks | 🔴 GATED on Phase G |
| **J. Mobile app build** | iOS + Android via React Native + Expo | 8-10 weeks | 🔴 GATED on Phase I |
| **K. Partner outreach** | First commercial pilot conversations | Ongoing | 🟡 NOT STARTED |
| **L. Election cycle coverage** | Fall 2026 debates | September-November | 🔴 The moment |
| **M. Acquisition conversations** | Strategic buyer engagement | October-January | 🔴 The exit |

🟢 = active and on track  
🟡 = in progress / awaiting input  
🔴 = not yet started or gated

---

# IMMEDIATE WORK (this week)

## Phase A.2 — Pre-debate verification (CRITICAL)

**Goal:** Confirm that `debate_routes.py` writes `verdict_status='provisional'` when creating debate claims during live coverage. If it writes NULL instead, the auto-promotion job will never see them and the entire provisional/final flow is silently broken.

**Why critical:** Without this verification, the May 26 debate will produce verdicts that don't go through the provisional/final lifecycle. The methodology promise won't hold operationally.

**Steps:**

1. Grep debate_routes.py for verdict_status writes:
```bash
grep -n "verdict_status\|provisional" debate_routes.py
```

2. If found and correctly set to 'provisional': verify with a synthetic debate claim insert + auto-promotion cycle.

3. If NOT found or incorrectly set: patch debate_routes.py to write `verdict_status='provisional'` on debate claim insertion. Backup before edit.

4. Smoke test end-to-end: simulate a debate claim, watch it appear as provisional, verify auto-promotion at the 60-minute mark.

**Owner:** Sonnet (verification + fix), Britt (approval)

**Deadline:** May 25 (one day before debate, for buffer)

## Phase B — May 26 debate coverage

**Goal:** Successfully cover the Colorado Gubernatorial GOP R2 debate as the first live debate under v1.7 methodology. Demonstrate the provisional/final distinction works in practice. Add a second clean debate to the track record (after Iowa R2 in May).

**Pre-debate readiness checklist** (from MAY26_READINESS_CHECKLIST.md):

- [x] Anthropic credits topped up
- [ ] Credit balance alert configured (Britt action)
- [x] veris-verdicts running cleanly
- [x] Auto-promotion job deployed
- [x] Auto-promotion smoke tested
- [x] is_public gate confirmed working
- [ ] Debate event row created with is_public=FALSE pre-debate
- [ ] verdict_status='provisional' stamping verified in debate_routes.py
- [ ] Pre-debate dry run completed
- [ ] Post-debate review procedure documented

**Live coverage protocol:**

1. **T-24 hours:** Final dry run with synthetic debate event
2. **T-1 hour:** Verify all crons green, credits sufficient (alert at $100 minimum), Anthropic API responding
3. **T-30 min:** Create debate event row in DB with is_public=FALSE
4. **T-0 (debate starts):** Flip is_public=TRUE
5. **During debate:** Monitor extraction → verdict pipeline; watch for provisional verdicts appearing
6. **T+90 min (after debate):** First batch of auto-promotions to final
7. **T+24 hours:** Post-debate review begins

**Post-debate documentation:**
- Total claims processed
- Verdict distribution
- Provisional → final transition counts
- Any methodology issues surfaced
- Lessons learned
- Pattern adjustments for next debate

This goes into the acquisition narrative: "We covered Iowa R2 in May, Colorado in May 26, [additional debates] through summer, fall debates at scale."

**Owner:** Britt (live coverage), Sonnet (post-debate analysis)

## Phase F — Backfill remaining queue (passive)

The ~907 unverified claims in the queue will process organically through the normal scheduler. No dedicated backfill session needed. Estimated cost: ~$27 (vs the originally planned $1,350 for the 45K backfill that turned out to be much smaller).

Monitor every 24-48 hours:
```sql
SELECT COUNT(*) FROM claims
WHERE verdict IS NULL
  AND priority_score >= 30
  AND COALESCE(verification_attempts, 0) < 3;
```

When queue is below 100: backfill is effectively complete.

---

# NEAR-TERM WORK (1-3 weeks)

## Phase C — Attorney approval

**Goal:** Receive attorney sign-off on v1.7 methodology before public-facing surfaces flip.

**Documents in attorney's hands:**
- METHODOLOGY_V17_DRAFT_v2.md (with utterance clock revision)
- V17_CHANGE_LOG_v2.md
- AUDIT_REPORT.md
- AUDIT_VERIFICATION.md

**Expected feedback categories:**
- **Minor:** word changes, clarifications — apply, resubmit
- **Moderate:** dispute process details, eligibility language — workshop with attorney
- **Major:** structural objections to a verdict type or scoring rule — revisit Session 2 (v1.7 design)

**Decision point:** when attorney approves, proceed to Phase D. If significant revisions required, loop back through Session 2.

**Britt's prep work during attorney review:**
- Draft Terms of Service
- Draft Privacy Policy
- Draft competitive defense analysis
- Set credit balance alert (operational hygiene)

## Phase D — Public methodology page update (GATED on Phase C)

**Goal:** Update verumsignal.com/methodology to show v1.7 content. Archive v1.6 at /methodology/archive/v1.6. Flip PUBLIC_METHODOLOGY_VERSIONS env var.

**Steps:**

1. Translate METHODOLOGY_V17_DRAFT_v2.md (markdown) to `static/methodology/data.js` (JavaScript structure with `window.VS_DATA`)
2. Save current v1.6 content to `static/methodology/archive/data_v1.6.js`
3. Create Flask route for `/methodology/archive/v1.6`
4. Update PUBLIC_METHODOLOGY_VERSIONS env var on Railway from `['v1.6']` to `['v1.6', 'v1.7']`
5. Smoke test both pages
6. Verify in browser

**Owner:** Sonnet (implementation), Britt (review of translated content)

**Duration:** 2-3 hours

## Phase E — Parallel verifier promotion (parallel track)

**Goal:** Validate that parallel verifier produces results matching sequential on clean post-purge claims, then promote to production.

**Current state:**
- Parallel verifier code at verdict_engine_parallel.py
- 3 bugs fixed in commit 0aa7b32
- Shadow mode running (PARALLEL_VERIFIER_ENABLED=true, PARALLEL_VERIFIER_SHADOW=true)
- Shadow log accumulating data on clean post-purge claims

**Thresholds for promotion:**
- 50+ clean comparisons logged
- ≥99% agreement rate
- No systematic disagreement patterns
- methodology_version stamping patch applied to verdict_engine_parallel.py (mirror the verdict_engine.py changes)

**Steps when threshold met:**

1. Sonnet checks shadow stats (every 24-48 hours)
2. When threshold met, surface to Britt with recommendation
3. Apply methodology_version stamping patch to verdict_engine_parallel.py (CRITICAL — without this, parallel-written verdicts get column default 'v1.6' instead of 'v1.7')
4. Britt approves promotion
5. Flip PARALLEL_VERIFIER_SHADOW=false on Railway
6. Monitor for 24-48 hours
7. If issues: kill switch (PARALLEL_VERIFIER_ENABLED=false) reverts immediately

**Why this matters:** Parallel verifier ~4x faster than sequential. Important for fall election cycle when verdict volume spikes. Less critical now but worth getting in place before September.

---

# MEDIUM-TERM WORK (1-2 months)

## Phase G — Session 6 post-deployment validation

**Goal:** Confirm v1.7 is stable for 7+ days, backfill is complete, no critical regressions, the three priority concerns from the original audit are resolved.

**Inputs:**
- v1.7 live for 7+ days
- Phase D complete (public methodology page updated)
- Phase F complete (backfill processed)

**Steps:**

1. Pull post-v1.7 verdict distribution; compare to V1.5.1 baseline (62.9% supported, 17.8% overstated, etc.)
2. Pull current outlet scores; compare against pre-v1.7 baseline
3. Sample 50 random v1.7-stamped verdicts; manually verify accuracy
4. Re-run the 3 priority concerns from Session 1 (corroborated frequency, outlet score plausibility, debate attribution)
5. Verify content quality gate is working (no new claims from degraded content articles)
6. Cost check (per-claim verdict cost)

**Output:** V17_POST_DEPLOY_REPORT.md — sign-off that v1.7 is production-stable.

**Duration:** 2-4 hours

## Phase H — Methodology paper drafting

**Goal:** Publish a defensible methodology paper that strengthens IP protection and supports acquisition narrative.

**Why this matters:** Methodology rigor is what makes Verum Signal hard to copy. A formal paper:
- Establishes intellectual property
- Provides citation surface for academic/journalistic engagement
- Supports attorney defense if challenged
- Demonstrates rigor to acquisition buyers

**Approach:**
- Length: 15-25 pages (between blog post and academic paper)
- Audience: journalists, attorneys, civic tech researchers, potential acquirers
- Style: academic but accessible
- Distribution: Verum Signal site + LinkedIn + civic tech community

**Sections:**
1. Problem statement
2. Existing approaches (NewsGuard, Factiverse, Ground News, AllSides, Full Fact, Community Notes)
3. The outlet_claim vs attributed_claim distinction
4. The verification pipeline
5. The scoring formula
6. Operational considerations (live debate coverage, content quality gates)
7. Limitations and future work

**Duration:** 1-2 weeks of focused drafting

**Owner:** Britt (primary author), Claude (editing/research support)

## Phase I — Render migration

**Goal:** Migrate Verum Signal off Railway to Render. Three-domain architecture (verumsignal.com production, verumsignal.app warm-standby mirror, break-glass dormant domain).

**Why migrate:** Railway had two production-impacting events in 30 days (Runtime V2 env var bug, May 19 multi-hour outage). Render is more stable. Migration is also the moment to clean up Railway-specific workarounds (api.py:33 fallback) and execute the deferred side-table refactor.

**Reference documents:** MIGRATION_BIBLE.md, MIGRATION_ROADMAP.md, SECURITY_RUNBOOK.md (all in outputs)

**Migration phases:**

| Phase | Duration | Focus |
|---|---|---|
| 0: Stabilize and document | 3-5 days | Backups, inventory |
| 1: Render parallel deployment | 5-7 days | Get Render running alongside Railway |
| 2: Data and validation | 3-5 days | Database sync, testing |
| 3: Cutover preparation | 2-3 days | DNS, SSL, final testing |
| 4: Cutover | 1 day | DNS flip, monitor |
| 5: Hardening | 7-10 days | Cloudflare, mirror, break-glass |
| 6: Documentation and decommission | 3-5 days | Clean up Railway, finalize docs |

**Total elapsed:** 5-6 weeks  
**Active work:** ~30-50 hours

**Deferred work to include:**
- Side-table refactor (articles core / extraction_state / cache / meta)
- Drop vestigial columns (claims_extracted, claims_verified, processed)
- Fix duplicate articles bug at api.py:1306
- Consolidate api.py duplicated helpers
- Address The Hill / Politico anti-bot blocks (Jina fallback)

---

# LONG-TERM WORK (2-6 months)

## Phase J — Mobile app build

**Goal:** Ship iOS + Android app focused on live debate experience. The killer feature is push notifications during debates — verdicts arrive in the user's pocket within minutes of utterance.

**Why this matters:** Web cannot deliver the live-debate experience well. Users won't sit refreshing verumsignal.com/debates for 90 minutes. An app puts a buzz in their pocket when a verdict lands. Critical for fall election cycle.

**Stack decision:** React Native + Expo (solo founder velocity)

**v1 scope:**
- Signal feed (vertical scroll of recent reports)
- Article report viewer
- Leaderboard
- Outlet detail pages
- Live debate tab with push notifications
- Account system (email-based)
- "My Reports" saved articles
- Outlet subscription notifications

**v1 NOT in scope:**
- In-app purchases / paid tier (web handles this initially)
- Comments / discussion
- Social sharing beyond OS-level
- Apple Watch / WatchOS

**Timeline (working backward from fall debates):**
- Development start: late June / early July (after Phase I migration stable)
- Feature complete: early August
- App Store submission: late August
- App live: early September
- Iteration and bug fixes: throughout September
- VP debate (October): app handles its first major test
- Presidential debates (October-November): app at peak relevance

**Risks:**
- App Store review delays (multi-week wildcard for new apps)
- iOS push notification deliverability
- Apple's potential scrutiny of "media credibility" apps during election season
- Founder bandwidth competing with election coverage

**Duration:** 8-10 weeks of focused development + ongoing maintenance

## Phase K — Partner outreach

**Goal:** Establish at least one commercial pilot before acquisition window opens.

**Target categories:**
- Media companies (NYT, WaPo, Bloomberg — internal newsroom tools)
- Civic tech platforms (Ballotpedia, Vote.org, Civic Eagle)
- Election infrastructure
- AI/methodology buyers (Anthropic, OpenAI, Hugging Face)
- Platform companies (Bluesky, Threads, X)

**Approach:**
- Identify 10-15 specific companies, ranked by strategic fit
- Cold outreach to relevant executives (LinkedIn, warm intros where possible)
- Pitch: API access + custom integration for specific use cases
- Pilot pricing: low (or free) for first pilot in exchange for case study rights

**Timeline:**
- Start outreach: post-v1.7 stable (June)
- First pilot signed: target August
- Pilot data accumulating: September-October
- Pilot case study: October (becomes acquisition asset)

**Duration:** Ongoing

## Phase L — Election cycle coverage

**Goal:** Cover fall 2026 debates at scale. Establish Verum Signal as the credibility platform for election coverage.

**Calendar (key debates):**
- Various primary debates (summer)
- General election candidate debates (September)
- VP debate (October)
- Presidential debates (October-November)
- State-level high-profile races

**Operational requirements:**
- All Phase A-G work complete
- App live (Phase J)
- Render migration complete (Phase I)
- Methodology paper published (Phase H)
- Auto-promotion + dispute process battle-tested
- Pre-debate checklist refined from each prior debate

**Metrics that matter for acquisition:**
- Number of debates covered
- Total verdicts produced during live coverage
- Average time-to-verdict from utterance
- Provisional/final accuracy (post-review revision rate)
- Press citations of Verum Signal verdicts
- App downloads/active users during debates

## Phase M — Acquisition conversations

**Goal:** Acquired by end of 2026 or early 2027 at target valuation.

**Don't:** publicly announce that you're for sale. Buyers stop competing if they know you're shopping.

**Do:** be findable, ship well, generate visible traction.

**Active engagement:**
- Inbound interest from buyers (likely from press coverage during election season)
- Outbound: targeted conversations with the top 5 strategic fits
- Use methodology paper, deployment logs, audit reports, partner case studies as artifacts

**Diligence preparation (run in parallel from August):**
- Clean financial records
- Documented technical architecture (TECH_PAPER.md)
- Documented operational maturity (SECURITY_RUNBOOK.md, incident logs)
- Documented methodology rigor (AUDIT_REPORT.md, AUDIT_VERIFICATION.md, METHODOLOGY_V17_DRAFT.md)
- Clear IP ownership
- Working transferability (anyone competent could maintain this)

**Decision points:**
- First serious offer: evaluate strategic fit, not just price
- Multiple offers: competitive dynamic raises valuation
- No offers by January 2027: pivot to continued solo operation, defer acquisition to 2027

---

# Parallel workstreams (always active)

These run continuously throughout all phases:

## Brand voice and editorial discipline

Every public surface, code comment, methodology document, and external artifact follows the banned-vocabulary rules and non-partisan posture from PROJECT_BIBLE.md Section 4. Violations are caught at code review or before any external publication.

## Security hygiene

Per SECURITY_RUNBOOK.md:
- Credential rotation calendar (currently not enforced — Phase I migration is when this becomes active)
- DB backup verification (quarterly restore drills)
- 2FA audit (monthly)
- CertSpotter monitoring (configured at Phase I)
- Incident logging (every issue gets a markdown file in docs/incidents/)

## Operational monitoring

- Anthropic credit balance (alerts at $100 soft, $50 hard — Britt to configure)
- Railway service health (manual check via dashboard until external monitoring)
- Daily backup verification (post-Phase I)
- Cron status (post-Phase I cron_status table)

## Documentation maintenance

- PROJECT_BIBLE.md updated at every major milestone
- ROADMAP.md updated at phase transitions
- TECH_PAPER.md updated when methodology or architecture changes
- SESSION_HANDOFF.md updated at end of each significant session

---

# Decision log (rolling)

Capture decisions made during roadmap execution. Each entry: decision, date, rationale, approval.

| Date | Decision | Rationale | Approved by |
|---|---|---|---|
| May 19 | Migrate to Render post-v1.7 | Railway stability issues | Britt |
| May 20 | Google News purge as part of v1.7 deploy | Single coordinated deployment | Britt |
| May 20 | Non-news exclusion includes gao.gov + 11 others | Eligibility definition needed | Britt |
| May 20 | Side-table refactor deferred to Render migration | v1.7 already has significant changes | Britt |
| May 20 | No outlet notification for v1.7 leaderboard changes | Pre-launch, no public presence | Britt |
| May 20 | Sequence Sonnet sessions, attorney gate after Session 2 | Methodology rigor | Britt |
| May 21 | Auto-promotion uses first_seen as utterance proxy | first_seen is closest available | Britt |
| May 21 | Methodology language: "extracts from live transcript" | Match implementation precisely | Britt |
| May 21 | Session 4 deployed without waiting on attorney | Backend can deploy; public surface waits | Britt |
| May 21 | Parallel verifier stays in shadow pending clean comparison data | Conservative — 3 bugs found in code review | Britt |

---

# Open questions / pending decisions

These need resolution at upcoming decision points.

1. **Credit balance alert thresholds** — Britt to configure. Recommended $100 soft / $50 hard.
2. **Auto-promotion job verification** — does debate_routes.py actually write verdict_status='provisional'? (Phase A.2)
3. **May 26 pre-debate dry run** — when and how
4. **Post-debate review procedure** — Sonnet to draft separately
5. **Render migration start date** — pending Phase G stable
6. **Mobile app stack final decision** — React Native + Expo is the recommendation; confirm before build starts
7. **Partner outreach prioritization** — top 5 targets to identify
8. **Methodology paper publication venue** — academic, civic tech blog, Verum Signal site?
9. **Acquisition target list** — top 5 buyer companies to identify

---

# Definition of done at each milestone

When the following statements are true, a phase is complete:

- **Phase A.2 complete:** debate_routes.py confirmed writing verdict_status='provisional'; smoke test passes end-to-end
- **Phase B complete:** May 26 debate covered, post-debate review written
- **Phase C complete:** attorney provides written approval (or formal letter)
- **Phase D complete:** verumsignal.com/methodology shows v1.7 content; /methodology/archive/v1.6 loads correctly; PUBLIC_METHODOLOGY_VERSIONS=['v1.6','v1.7']
- **Phase E complete:** PARALLEL_VERIFIER_SHADOW=false; verdicts being written by parallel; no error rate spike for 48 hours
- **Phase F complete:** Unverified queue below 100 claims
- **Phase G complete:** V17_POST_DEPLOY_REPORT.md written and approved by Britt
- **Phase H complete:** Methodology paper published at a stable URL
- **Phase I complete:** Verumsignal.com running on Render; Railway services deleted
- **Phase J complete:** App live in App Store + Google Play
- **Phase K complete:** First commercial pilot signed (paid or unpaid with case study rights)
- **Phase L complete:** All planned fall debates covered with documented track record
- **Phase M complete:** Acquisition closed OR explicit decision to continue solo operation through 2027

---

**End of ROADMAP.md**

*Updated when phases complete or strategic direction changes. Reference PROJECT_BIBLE.md for current state. Reference TECH_PAPER.md for technical detail. Reference SESSION_HANDOFF.md for what tomorrow's session picks up.*
