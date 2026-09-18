// VS_DATA for Verum Signal methodology
window.VS_DATA = {
  meta: {
    brand: "VERUM SIGNAL",
    title: "Article Analysis Methodology",
    subtitle: "How Verum Signal Evaluates a Single Article",
    version: "v1.7.1",
    date: "September 2026",
    principle: "We provide the signals. You decide.",
  },
  sections: [
    {
      id: "overview", num: "01", title: "Overview", kind: "prose",
      body: ["When a user submits an article URL to Verum Signal, the system extracts factual claims from the article, scores them for verification priority, and runs each claim through a two-step verification pipeline. The result is a claim-level verdict report alongside the outlet's overall reliability score.",
             "This document describes exactly what happens at each stage, what decisions are made, and what the user sees at the end."],
      callout: { label: "Brand principle", text: "Verum Signal never describes itself as a fact-checker. It evaluates claims on evidence. We provide the signals. You decide." },
    },
    {
      id: "ingestion", num: "02", title: "Stage 1 — Article Ingestion", kind: "stage", stageIndex: 1,
      body: ["Articles reach Verum Signal by two paths. On-demand: a reader submits an article URL and the full text is fetched from the page. Background: publisher feeds are polled around the clock; each item arrives with the feed's summary, and the full text is then fetched from the article page (direct fetch first, a reader service second). Where a page is paywalled or blocks automated fetching, the article is recorded as such and is not scored.", "Only articles with at least 500 characters of text are eligible for claim extraction; feed summaries alone never are. Disclosure: before 16 September 2026 the background path stored the feed summary only, so most background articles never met that threshold and outlet scores rested on the minority whose feeds carried full text. Full-text fetching for the background path began 16 September 2026; the per-outlet 'scored through' date on the leaderboard shows how current each score is.", "The following checks happen at ingestion:"],
      checks: [
        ["Language detection", "non-English articles are flagged and excluded from scoring where detection succeeds; detection fails open (treats as English) on short text or errors -- measured impact as of July 2026 is effectively zero"],
        ["Published date extraction", "articles without a resolvable published_at timestamp are excluded from reliability scoring"],
        ["Breaking news gate", "articles published in the last 6 hours are tracked but not scored"],
        ["Source identification", "the article domain is matched to the outlet in the database"],
      ],
      callout: { label: "Why 6 hours", text: "Breaking news claims are frequently updated, corrected, or retracted within hours of publication. Scoring them immediately would penalise outlets for normal journalistic correction cycles." },
    },
    {
      id: "attribution", num: "2.5", title: "Claim Attribution", kind: "prose",
      body: ["Not every claim in an article counts against the publishing outlet. One category is excluded from outlet scoring:"],
      attribution: [
        ["Quoted claims", "when an outlet reports what someone else said, the claim is recorded as that person's, not the outlet's, and never counts toward the outlet's score. When a quoted claim is checked, the check is of the underlying assertion -- whether what was said is supported by the evidence -- not of whether the outlet quoted it accurately."],
      ],
      bodyAfter: "Verbatim wire-service content published under an outlet's own byline is scored as an ordinary outlet claim -- the outlet selected it, headlined it, and published it under its masthead. Outlets are scored only on claims they make in their own voice. Quoted claims may be checked in the background as well as when a reader requests an article's report; their verdicts are published with the claim and never count toward an outlet's score.",
    },
    {
      id: "speakers", num: "2.6", title: "Speaker Attribution (Debates)", kind: "prose",
      body: [
        "This section applies to debate coverage only. Every claim extracted from a debate carries the identity of the person who said it, and that identity is decided before extraction, not after: an utterance with no confirmed speaker is never extracted. Claims are published while the debate is in progress, before the post-event check in 2.6.5 has run. Claim Attribution (2.5) concerns the origin of claims in articles and is unaffected.",
        "Crediting a candidate with words they did not say can publish a false claim against them; leaving a candidate's words unattributed only loses coverage. Every rule below prefers the second failure to the first. Where a decision is marginal, the system abstains.",
      ],
      sub: [
        { title: "2.6.1  Voice prints and roster scoping", intro: "Attribution is acoustic. Each candidate on an event's roster is enrolled before the event with a voice print: a speaker embedding built from at least two audio clips from two independent sources, never from the event being covered. Each clip is checked by re-transcribing it against its source. A print is refused at enrolment if it sits too close to any print already in the store, and the clip responsible is re-sourced rather than overridden.", items: [
          ["Roster scoping", "matching is restricted to the prints of the speakers on the event's roster. Removing this restriction on a test event produced ten confident matches against people in other states."],
          ["All-or-none coverage", "either every candidate on the roster is enrolled or the event is captured without published attribution. A missing print would publish claims against some candidates and none against another."],
          ["Moderators are not enrolled", "moderators share a single generic speaker row and are excluded from extraction. An unenrolled voice receives no name rather than the nearest one."],
        ]},
        { title: "2.6.2  Live attribution during capture", intro: "Audio is captured from the event's English stream and transcribed live with per-word speaker diarization. Each transcript segment is split into runs of a single diarizer speaker index, so no stored utterance mixes two voices. Every utterance is stored with its diarizer index, its position in the stream, and its timestamp.", items: [
          ["Voice confirmation", "an index is confirmed as a roster speaker only by voice: after at least 30 seconds of that index's audio, several short windows are embedded, averaged and compared to the roster prints. A cosine distance below 0.55 confirms the index; anything at or above 0.55 confirms nothing. Each roster speaker may be confirmed on at most one index at a time. On confirmation, earlier utterances of the same index within the same capture run inherit the speaker; utterances of an unconfirmed index remain unattributed."],
          ["Text cues propose, voice binds", "a moderator handing to a candidate by surname may propose a speaker for the index that speaks next. Cues match only curated roster surnames and full names as whole words. A cue never overrides a voice confirmation."],
          ["Capture restarts", "if capture restarts mid-event the diarizer's indices restart at zero. Every back-fill is scoped to the run in which the confirmation was made; a confirmation in a later run cannot relabel rows written in an earlier one."],
        ]},
        { title: "2.6.3  What live attribution does not do", items: [
          ["No fallback by order", "it does not assign a speaker by speaking order, by most recent speaker, or by any fallback. Those paths were removed in August 2026 after they were shown to write a moderator's introduction to a candidate as a confident attribution."],
          ["No custom vocabulary", "it does not bias the recognizer toward strings chosen before the candidates speak; attribution is acoustic and does not read the text. Name spelling is corrected afterwards against the known roster, where it is auditable."],
          ["No downward re-attribution", "an utterance confirmed by voice keeps that speaker unless the post-event check corrects it."],
        ]},
        { title: "2.6.4  Claim extraction depends on attribution", intro: "Claim extraction operates on whole speaker turns -- consecutive utterances of one confirmed speaker -- and selects only turns whose speaker is a candidate. An utterance with no confirmed speaker neither joins nor closes a turn, and the selection joins on the speaker row, so an unattributed utterance cannot produce a claim by construction. Moderator turns are never selected." },
        { title: "2.6.5  Post-event independent check", intro: "After the event, the saved capture audio is diarized again by an independent method, which partitions the recording into speaker clusters without reference to the live indices. Each cluster is voice-identified once against the roster prints and every live utterance inside that cluster inherits the result. Clusters are classed as named (a clear match to exactly one roster print), weak, no match (this voice matches nobody enrolled), or no evidence (no turn long enough to embed). A live utterance labelled as a candidate whose cluster matches nobody enrolled is counted as wrong, not as abstained. Any non-zero count is inspected row by row and corrected." },
        { title: "2.6.6  Measured performance", intro: "Every figure is from a named recording, so the results can be reproduced. None of these recordings was used to build a print.", items: [
          ["Wyoming gubernatorial debate, 2026-08-21", "112 hand-labelled speech spans: 112/112 correct; the moderator (28/28) and panelists (9/9), who have no print, abstained; 0 false attributions. 380 rolling windows straddling speaker changes: 0 attributions to anyone absent from the roster. Cluster-level, run 1: 279/279 correct, 0 wrong, 0 abstained."],
          ["Cross-state control", "three Arizona candidates scored against three Wyoming prints: 3/3 abstained."],
          ["Arizona congressional rehearsal (two enrolled candidates, unenrolled moderator)", "acoustic clusters correct in two replays. Before the cue fix the text-cue path produced 16 misattributed utterances and 2 claims from them (kept as evidence; never published); after it, 1 misattributed utterance and 0 claims."],
          ["Arizona Corporation Commission debate, 2026-09-09 (first production event)", "capture restarted after the host machine suspended. Run 0: 112 correct, 14 wrong -- the moderator's opening relabelled to a candidate when a confirmation in run 1 back-filled index rows from run 0. Run 1: 521/521. Live voice identification was correct in both runs; the back-fill crossed the run boundary. The rows were corrected before publication, no claim had been extracted from them, and back-fill was scoped to the current run before the event was published."],
          ["Restart rehearsal, 2026-09-10", "deliberate kill and restart after run 0 had two confirmed indices: run 0's index map unchanged after run 1 confirmed; back-fill did not cross the restart."],
        ]},
        { title: "2.6.7  Known limitations", items: [
          ["Turn-boundary contamination", "if the independent diarizer merges a moderator's short handoff into the following candidate's cluster, the cluster's acoustics are the candidate's, the majority label agrees with the live label, and both are wrong about the moderator's words. One such case was found on the 2026-09-09 event by reading the transcript. No cluster-level method catches this; it is reported here rather than solved."],
          ["The threshold is measured on few fixtures", "0.55 was chosen from the Wyoming sweep, where accuracy was flat from 0.50 to 0.70 with a clear gap between correct and incorrect distances, and has since held on the rehearsals and the first production event. It is a fixed constant, not tuned per event."],
          ["The moderator-preference rule has never fired", "on a decision within 0.05 between a candidate and the moderator the design prefers the moderator; every measured decision to date was clear enough not to need it, so it is stated as a rule and not as a tested one."],
          ["Two similar voices in one race", "two candidates can sit below the confirmation threshold even when each is enrolled from independent sources. The remedies are re-enrolment from additional independent sources (one such pair was resolved this way in September 2026) or transcript-only coverage for that event, decided before the event. The internal attribution record names the voices; this page does not."],
          ["Moderators abstain rather than being named", "because moderators are not enrolled, a moderator's voice matches nobody. This is the intended safe direction; a moderator who never issues a recognized handoff has no label at all."],
          ["A restart produces a coverage gap", "each capture run writes its own recording and the post-event check runs per recording. Utterances during the restart window are lost."],
        ]},
        { title: "2.6.8  What this section does not claim", items: [
          ["Human review of every attribution", "the post-event check is automated; a human reviews its non-zero counts and the transcript around them."],
          ["A false-attribution rate of zero in production", "it claims zero on the fixtures in 2.6.6 and one measured incident on 2026-09-09 whose cause is fixed and whose rows were corrected before publication."],
          ["Reliability of the text-cue path on its own", "its role is to propose; voice confirmation binds."],
          ["Anything about articles", "speaker attribution applies to debate claims only."],
          ["A check before publication", "since 15 September 2026 debate pages are public before the event starts and claims appear while the debate runs; the independent check in 2.6.5 runs after the event."],
        ]},
      ],
      callout: { label: "Consistent on every surface", text: "A claim's speaker is held in three places -- the claim, the utterance it came from, and the public API record -- and the platform's status page reports whether all three agree; the same check runs before and after every debate. Corrections propagate to every surface within one refresh cycle." },
    },
    {
      id: "extraction", num: "03", title: "Stage 2 — Claim Extraction", kind: "stage", stageIndex: 2,
      body: ["The article text is sent to Claude Sonnet via the Anthropic API. Sonnet is instructed to extract discrete, verifiable factual claims from the article. A factual claim is a specific assertion about the world that can in principle be confirmed or refuted by evidence."],
      sub: [
        { title: "3.1  What qualifies as a claim", intro: "Sonnet extracts claims that are:", items: [
          ["Specific and falsifiable", "'The unemployment rate fell to 3.7% in March' qualifies; 'the economy is struggling' does not"],
          ["Attributed or asserted as fact", "not clearly labelled as speculation, prediction, or opinion"],
          ["About the external world", "not about the author's opinion or feelings"],
        ]},
        { title: "3.2  What is excluded at extraction", items: [
          ["Price predictions", "crypto, stocks, commodities"],
          ["Sports draft speculation", ""],
          ["Political opinion and commentary", "explicitly framed as opinion"],
          ["Editorial assertions", "without factual claim structure"],
          ["Rhetorical questions", ""],
        ]},
        { title: "3.3  Deduplication", intro: "Near-identical claims within the same article are merged via deduplicate_claims(). This produces approximately a 6% reduction per article." },
        { title: "3.4  On opinion content", intro: "Opinion content is a valuable part of the media ecosystem. Verum Signal does not score opinion content as factual reliability because it is not a factual signal." },
      ],
      callout: { label: "Model", text: "Claude Sonnet is used exclusively for claim extraction." },
    },
    {
      id: "priority", num: "04", title: "Stage 3 — Priority Scoring", kind: "stage", stageIndex: 3,
      body: ["Each extracted claim is assigned a priority score from 0 to 100. The threshold governs the background ingestion queue: of the roughly 3,400 articles ingested daily, only claims scoring 30 or above enter that queue -- claims below 30 are stored but not verified by that path.", "The background queue is a sample, not the whole corpus. Extraction runs on a fixed budget of 50 articles per hourly run, chosen from eligible articles by a stated rule: articles within two days of leaving the 30-day window are taken first (oldest first), then the longest articles. An outlet's score is therefore computed on the articles that reached extraction under this rule, not on everything it published. The per-outlet verdict count and 'scored through' date make the sample size and recency visible.", "On-demand reports work differently. When a user submits a specific article URL, every extracted claim is verified regardless of priority score: paid reports verify all extracted claims, free reports verify the first 2 (ordered by claim type -- outlet claims before attributed -- not by priority score). This is a deliberate exception, not an oversight."],
      sub: [
        { title: "4.1  What raises a claim's priority score", items: [
          ["Political and policy content", "keywords like election, legislation, policy, congress, senate"],
          ["Economic claims", "GDP, unemployment, inflation, interest rates"],
          ["Public health claims", "vaccine, mortality, disease, clinical trial"],
          ["Attribution", "to named officials or institutions"],
          ["Numerical specificity", "claims with precise figures score higher than vague assertions"],
        ]},
        { title: "4.2  Why a threshold exists", intro: "The 30-point threshold means entertainment, lifestyle, sports, and editorial opinion claims are generally excluded from the verification queue. This is intentional -- Verum Signal evaluates factual claims about public affairs, not all content an outlet publishes." },
        { title: "4.3  A second threshold governs the Live Feed", intro: "The Live Feed shows checked claims that scored 65 or above, twelve at a time. The score is assigned at extraction, before any checking has happened, so it says nothing about how a claim turned out. The 65 figure was set by measuring what it selects: at 65, claims found to be exaggerated or contradicted have made up between a third and a half of the feed (36% when the threshold was set in early September 2026, 45% when re-measured mid-September); at 70 they make up nearly two thirds. That is not because higher-scoring claims are more often wrong, but because claims specific and contestable enough to be worth checking are also more likely to be found wanting. A higher threshold would make the feed read as a list of failures rather than a record of work. The Live Feed is ordered by when checking finished, most recent first, and is never ordered by verdict. The gate selects what appears on the feed, not what is checked: every checked claim keeps its permalink. One verdict is excluded from the feed and its RSS and JSON versions: claims found not verifiable, which are about 2% of those passing the gate. They are checked and published on their permalinks like any other; the feed omits them because it exists to show what was found, and for those claims nothing could be." },
      ],
    },
    {
      id: "verification", num: "05", title: "Stage 4 — Verification Pipeline", kind: "pipeline", stageIndex: 4,
      body: ["Each claim above the priority threshold passes through a two-step verification process. Steps are run in order; a claim exits the pipeline as soon as a verdict is assigned."],
      steps: [
        { num: "1", title: "Opinion Pre-Filter", body: "The claim text is checked locally against a fixed list of opinion and speculation signals. A match assigns the opinion verdict immediately, with no model call." },
        { num: "2", title: "Web Search Verification", body: "Claims that pass the pre-filter are sent to Claude Sonnet with the web search tool enabled. Sonnet searches the web for evidence and returns a verdict, a confidence score, and sources with an independence assessment for each. Independence is judged by the model at verification time -- there is no separate deterministic consensus check. Corroboration (5+ outlets reporting consistently) is a small minority of assigned verdicts, measured at 4.8% of scored claims as of July 2026." },
      ],
    },
    {
      id: "verdicts", num: "06", title: "Verdict Types", kind: "verdicts",
      body: ["Eight possible verdicts are assigned. Six contribute to the outlet's reliability score; two (opinion and not_verifiable) are excluded from scoring. Each scoreable verdict carries a weight that feeds the outlet reliability formula."],
      verdicts: [
        { key: "supported",      weight: "+1.0",   tone: "pos",     meaning: "Confirmed by two genuinely independent sources" },
        { key: "plausible",      weight: "+0.5",   tone: "pos",     meaning: "Consistent with evidence, but only one credible source found" },
        { key: "corroborated",   weight: "+0.75",  tone: "pos",     meaning: "5+ outlets report consistently without contradiction, full independence not established" },
        { key: "overstated",     weight: "-0.5",   tone: "neg",     meaning: "Core fact is real but exaggerated or framed misleadingly" },
        { key: "disputed",       weight: "-1.0",   tone: "neg",     meaning: "At least one credible source directly contradicts the claim" },
        { key: "not_supported",  weight: "-1.5",   tone: "neg",     meaning: "Evidence actively contradicts the claim (stronger than disputed)" },
        { key: "not_verifiable", weight: "excluded",tone: "neutral", meaning: "Cannot confirm or deny -- sources unavailable" },
        { key: "opinion",        weight: "excluded",tone: "neutral", meaning: "Editorial or opinion content -- not a factual reliability signal" },
      ],
      callout: { label: "Note", text: "opinion and not_verifiable are excluded from ALL counts. An outlet is never penalised for publishing opinion content." },
    },
    {
      id: "score", num: "07", title: "How the Article Affects the Outlet Score", kind: "score",
      body: ["Each verdict from the article's claims is added to the outlet's cumulative reliability score."],
      formula: {
        steps: [
          { label: "weighted_sum", expr: "\u2211 (verdict_weight \u00d7 verdict_count)" },
          { label: "scoreable",    expr: "count of verdicts excluding not_verifiable and opinion" },
          { label: "normalised",   expr: "(weighted_sum / scoreable + 1.5) / 2.5", highlight: true },
          { label: "score",        expr: "min(max( normalised \u00d7 100, 0), 100)" },
        ],
        note: "Divisor fixed at 2.5, unchanged since v1.5.",
      },
    },
    {
      id: "changes", num: "7.5", title: "Verdicts Can Change", kind: "prose",
      body: [
        "Verdicts are not permanent. They are reviewed when new editions of the methodology are released or when a verdict dispute is submitted.",
        "A correction changes who a claim is attributed to or what its evidence shows; when a claim's speaker is corrected, its verdict is reset and the claim is checked again. A repair aligns records that disagree about a claim without changing the claim itself, and does not reset the verdict. Both are logged against the claim with a reason and a date, and both propagate to every surface that shows the claim within one refresh cycle.",
      ],
    },
    {
      id: "tiers", num: "08", title: "Outlet Inclusion & Tiers", kind: "prose",
      body: ["Outlets are placed into one of four inclusion tiers based on the number of scoreable verdicts assigned to their claims (opinion and not_verifiable do not count). Tiers determine whether and how prominently an outlet appears on the public leaderboard.", "Display conventions, not scoring rules: scores of 70 and above are labelled High, 40 to 69 Medium, below 40 Low. The outlet page draws a confidence band of plus or minus 20 divided by the square root of the scoreable count around the score history; it is a simple width heuristic that narrows as the count grows, not a statistical interval. Each leaderboard row shows the date of the outlet's most recent verdict."],
      sub: [
        { title: "Published \u2014 100 or more scoreable verdicts", intro: "Outlets at this tier have a fully established score. New verdicts adjust the score incrementally rather than meaningfully shifting it." },
        { title: "Stabilizing \u2014 50 to 99 scoreable verdicts", intro: "Outlets here have enough verdicts to produce a meaningfully stable score. The Stabilizing label indicates the score is reliable but still maturing." },
        { title: "Limited Data \u2014 20 to 49 scoreable verdicts", intro: "Outlets in this tier appear on the leaderboard with a published score, marked as Limited Data. Scores in this band can shift noticeably as more verdicts arrive." },
        { title: "Excluded \u2014 fewer than 20 scoreable verdicts", intro: "Outlets with fewer than 20 scoreable verdicts are not assigned a public score. Their claims continue to be analysed and stored, but the sample is too small to produce a stable signal." },
      ],
    },
    {
      id: "report", num: "09", title: "Report Page \u2014 Structure & Layout", kind: "prose",
      body: [
        "Each article analysis produces a report page showing the claims extracted, their verdicts, and the supporting sources used to assign each verdict.",
        "Reports include the article's source outlet, that outlet's current reliability score and tier, and a list of every claim assessed during analysis. Each claim displays its verdict, a brief evidence summary, and links to the sources consulted.",
        "Three parts of each report are written by the model when the report is displayed: the article summary, the overall signal, and what to watch for. They are generated from the article's title, the outlet's score, and each claim's verdict and reasoning; what to watch for names people, documents or institutions whose evidence would confirm or contradict the claims. These sections describe what the checks found. They are not verdicts and do not change any score. If generation fails, the overall signal is a fixed sentence built from the verdict counts.",
      ],
      callout: { label: "On 'Unscored' articles", text: "An article that contains only opinion and not_verifiable claims (no scoreable verdicts) displays as Unscored on the report page rather than receiving a 0/100 score. An Unscored article does not contribute to the outlet's reliability score in either case; the change is presentational, ensuring the reader is not shown a misleading numeric score for an article that produced no scoreable signal." },
    },
    {
      id: "limits", num: "10", title: "What Verum Signal Does Not Do", kind: "prose",
      body: ["Verum Signal does not rate journalists, editors, or owners. It does not rate opinion content. It does not rate outlets on ideological grounds."],
    },
    {
      id: "changelog", num: "11", title: "Changelog", kind: "prose",
      body: ["This page is the public methodology document."],
      sub: [
        { title: "v1.7.1 \u2014 September 2026", intro: "Speaker Attribution (Debates) added as Section 2.6 after the first production debate; verdict-change rules stated; page corrected where it had drifted from the engine. Scoring rules, verdict types and outlet methodology are unchanged; the version stamp on claims remains v1.7.", items: [
          ["17 September 2026 -- tiers, debate publication timing and three statements corrected", "The web leaderboard and outlet pages counted opinion and not-verifiable verdicts toward an outlet's tier and inclusion, contrary to Sections 06 and 08; they now count scoreable verdicts only, as the mobile app and API already did. One outlet moved from Published to Stabilizing; no score changed. Since 15 September 2026 debate claims are published while the debate is in progress and the independent check in Section 2.6.5 runs after the event; Section 2.6 had described that check as running before publication. The speaker-agreement check runs on the status page and before and after every debate, not nightly as stated. Section 09 now states that three parts of each report are written by the model."],
          ["17 September 2026 -- quoted claims described correctly", "Section 2.5 said quoted claims were judged on whether the quote was accurate and were checked only when a reader requested an article's report. Neither was the engine's behaviour: a quoted claim's check is of the underlying assertion, and quoted claims are also checked in the background. Both statements are corrected above; the Quoted claims item below is superseded by this one. Section 2.5's quoted-claims definition and Section 05's two verification steps -- including the statement that source independence is judged by the model rather than by a deterministic check -- were in this page's source but did not display; they now do. No scoring rule changed."],
          ["16 September 2026 -- ingestion corrected and disclosed", "Section 02 now states the two ingestion paths and the full-text fetch step added to the background path on this date, the 500-character eligibility threshold, and the disclosure that background articles before this date carried feed summaries only. Section 04 states the extraction budget and selection rule so that scores are understood as computed on a sample. Section 08 says scoreable verdicts where it said verdicts, and records the score bands and the confidence-band heuristic as display conventions. Two page labels that still showed the pre-v1.7 corroborated weight (+0.5) were corrected to +0.75; the engine had used +0.75 since v1.7. The leaderboard now shows each outlet's most recent verdict date. No scoring rule changed."],
          ["Section 2.6 added", "The live method is documented as built -- streaming diarization with per-index voice confirmation and run-scoped back-fill -- with the post-event independent check, measured performance on named recordings, and the known limitations. As with v1.7 itself, the code preceded this text."],
          ["Quoted claims", "Section 2.5 now states that quoted claims are checked only when a reader requests an article's report and never count toward an outlet's score. This was the engine's behaviour; it was not written down."],
          ["Verdicts Can Change", "Section 7.5 distinguishes a correction (verdict reset, claim re-checked) from a repair (records aligned, verdict kept), and states that both propagate to every surface within one refresh cycle. A check that the claim, its source utterance and the public API record agree on the speaker was added in September 2026; it runs on the status page and before and after every debate."],
          ["Same inclusion rule on every surface", "The date requirement and the 6-hour gate in Section 02 are applied identically wherever a score is published -- web leaderboard, mobile app and public API. Until 17 September 2026 the API and mobile aggregates omitted both; on 14 September 18 of 27 published outlet scores differed from the web by up to 5 points. Aligned on 17 September; the web figures were the correct ones."],
          ["Page corrected", "The Overview described a three-step pipeline (two-step since July); the formula note cited v1.6 for a divisor unchanged since v1.5."],
        ]},
        { title: "v1.7 \u2014 July 2026", intro: "Verification pipeline rewritten to match the live engine; corroborated weight corrected; priority threshold and language detection described accurately; wire-reprint exclusion retired.", items: [
          ["Corroborated weight corrected", "corroborated changed from +0.5 to +0.75, matching the engine since a Session 3 change. Rationale: v1.6 gave plausible and corroborated identical weight, erasing the distinction the verdict definitions themselves draw."],
          ["Verification Pipeline rewritten", "The three-step cache/consensus/web-search process described in v1.6 no longer reflects the engine. Both short-circuits were removed in code May 28, 2026. Verification is now described as the two-step process it actually is."],
          ["Priority Scoring corrected", "The 30-point threshold governs the background ingestion queue only; on-demand report requests verify claims regardless of priority score. This exception existed in the code but was never documented."],
          ["Language detection described precisely", "Enforced where detection succeeds; fails open on short text or errors. Measured impact as of this version: effectively zero."],
          ["Wire-reprint exclusion retired", "The wire-reprint category was retired as a deliberate methodology decision, folding wire-service content into the ordinary outlet-claim structure. Verbatim wire content published under an outlet's own byline is scored as an ordinary outlet claim -- an editorial decision the outlet is accountable for."],
          ["Archive-history claim corrected", "This changelog previously stated the full version history is preserved at /methodology/archive. No general index exists; the claim has been removed rather than left inaccurate."],
        ]},
        { title: "v1.6 \u2014 May 5, 2026", intro: "Methodology consolidation and breaking-news gate consistency.", items: [
          ["Single source of truth", "All scoring constants \u2014 verdict weights, scoreable types, tier thresholds, and formula parameters \u2014 consolidated into a single canonical module. Eliminates drift between the leaderboard, the API, and outlet detail pages."],
          ["Breaking-news gate applied uniformly", "The 6-hour breaking-news gate (Section 02) now applies consistently to every scoring surface: leaderboard, /api/source endpoint, outlet detail aggregates, score history charts, and article-report outlet badges. Previously the gate was only enforced on one of these surfaces, causing live and persisted scores to drift apart by small but real amounts."],
          ["Articles with no scoreable claims display as Unscored", "An article that contains only opinion and not_verifiable claims now displays Unscored on its report page, rather than a misleading 0/100. The article does not contribute to the outlet's score in either case; this change is presentational."],
          ["Extraction prompt rewritten", "The article extraction prompt has been rewritten to acknowledge that opinion-genre articles can contain extractable factual claims. Previously the extraction step was over-filtering opinion articles even when they contained concrete factual assertions."],
          ["Paid extraction depth raised", "Paid (full) reports now extract up to 7 claims per article, raised from 3. Free reports continue to verify the top 2."],
          ["Brand language alignment", "User-facing strings and LLM system prompts updated to consistently use 'verification engine' and 'claim analysis platform' framing. Internal database column names retained for schema stability."],
          ["Pipeline infrastructure", "The ingestion, extraction, and verdict pipeline runs on cloud cron infrastructure independent of any local machine. The extraction queue reads directly from the database, ensuring all recently ingested articles are eligible for extraction rather than only the most recent fetch cycle."],
          ["Scoring robustness clarifications (May 2026)", "Three clarifications to extraction and verification behavior, within existing v1.6 verdict definitions and weights: (1) Headline claims are now explicitly prioritized for extraction — headlines are where factual distortion most commonly occurs. (2) Claims that omit context material enough to change a reader's understanding are now explicitly flagged as check-worthy for overstated classification. (3) The overstated verdict definition is clarified to include material omission of context, not only exaggerated figures. No changes to verdict weights, scoring formula, or verdict types."],
          ["Source attribution cleanup (May 2026)", "A one-time cleanup redistributed 65 verdicts originally attributed to news.google.com to their actual publishing outlets via title-suffix resolution. Claims from non-article pages (help center, search results) were deleted. No changes to scoring formula or verdict definitions."],
          ["Known data limitation", "A subset of ingested articles arrive without a parseable publication timestamp and are excluded from outlet scoring under the date-required policy described in Section 02. The ingestion pipeline is being improved to reduce this exclusion rate."],
        ]},
        { title: "v1.5 \u2014 April 25, 2026", intro: "Initial public methodology release.", items: [
          ["Eight verdict types defined", "Six scoreable (supported, plausible, corroborated, overstated, disputed, not_supported) and two excluded (opinion, not_verifiable)."],
          ["Outlet reliability score formula", "(weighted_sum / scoreable + 1.5) / 2.5, normalised to 0-100."],
          ["Public leaderboard launched", "Outlets meeting the 20-verdict inclusion threshold appear with their score and tier."],
          ["Six-hour breaking-news gate", "Articles published in the last 6 hours are tracked but not scored. Defined in this version; consistency improvements deferred to v1.6."],
        ]},
      ],
      callout: { label: "Versioning policy", text: "Methodology versions are bumped when scoring math, verdict definitions, or inclusion rules change. Substantive changes are documented above. Archived versions remain available at /methodology/archive/<version>." },
    },
  ],
};

// Backwards compat
window.REPORT_DATA = window.VS_DATA;
