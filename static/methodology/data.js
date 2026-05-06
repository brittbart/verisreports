// VS_DATA for Verum Signal methodology
window.VS_DATA = {
  meta: {
    brand: "VERUM SIGNAL",
    title: "Article Analysis Methodology",
    subtitle: "How Verum Signal Evaluates a Single Article",
    version: "v1.6",
    date: "May 5, 2026",
    principle: "We provide the signals. You decide.",
  },
  sections: [
    {
      id: "overview", num: "01", title: "Overview", kind: "prose",
      body: ["When a user submits an article URL to Verum Signal, the system extracts factual claims from the article, scores them for verification priority, and runs each claim through a three-step verification pipeline. The result is a claim-level verdict report alongside the outlet's overall reliability score.",
             "This document describes exactly what happens at each stage, what decisions are made, and what the user sees at the end."],
      callout: { label: "Brand principle", text: "Verum Signal never describes itself as a fact-checker. It evaluates claims on evidence. We provide the signals. You decide." },
    },
    {
      id: "ingestion", num: "02", title: "Stage 1 — Article Ingestion", kind: "stage", stageIndex: 1,
      body: ["The article URL is submitted by the user. Verum Signal fetches the article content and extracts the full text. The following checks happen at ingestion:"],
      checks: [
        ["Language detection", "non-English articles are flagged and excluded from scoring"],
        ["Published date extraction", "articles without a resolvable published_at timestamp are excluded from reliability scoring"],
        ["Breaking news gate", "articles published in the last 6 hours are tracked but not scored"],
        ["Source identification", "the article domain is matched to the outlet in the database"],
      ],
      callout: { label: "Why 6 hours", text: "Breaking news claims are frequently updated, corrected, or retracted within hours of publication. Scoring them immediately would penalise outlets for normal journalistic correction cycles." },
    },
    {
      id: "attribution", num: "2.5", title: "Claim Attribution", kind: "prose",
      body: ["Not every claim in an article counts against the publishing outlet. Two categories are excluded from outlet scoring:"],
      attribution: [
        ["Wire reprints", "articles that are verbatim reprints from wire services like Reuters, AP, AFP, or Bloomberg are excluded entirely from the outlet claim pool when detected."],
        ["Quoted claims", "when an outlet reports what someone else said, the outlet is evaluated on whether the quote is accurate, not on whether the speaker's claim is true."],
      ],
      bodyAfter: "This policy ensures outlets are scored on what they themselves originated, not what they republished or quoted.",
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
      body: ["Each extracted claim is assigned a priority score from 0 to 100. Only claims scoring 30 or above enter the verification queue. Claims below 30 are stored but not verified."],
      sub: [
        { title: "4.1  What raises a claim's priority score", items: [
          ["Political and policy content", "keywords like election, legislation, policy, congress, senate"],
          ["Economic claims", "GDP, unemployment, inflation, interest rates"],
          ["Public health claims", "vaccine, mortality, disease, clinical trial"],
          ["Attribution", "to named officials or institutions"],
          ["Numerical specificity", "claims with precise figures score higher than vague assertions"],
        ]},
        { title: "4.2  Why a threshold exists", intro: "The 30-point threshold means entertainment, lifestyle, sports, and editorial opinion claims are generally excluded from the verification queue. This is intentional -- Verum Signal evaluates factual claims about public affairs, not all content an outlet publishes." },
      ],
    },
    {
      id: "verification", num: "05", title: "Stage 4 — Verification Pipeline", kind: "pipeline", stageIndex: 4,
      body: ["Each claim above the priority threshold passes through a three-step verification process. Steps are run in order; a claim exits the pipeline as soon as a verdict is assigned."],
      steps: [
        { num: "1", title: "Cache Check", body: "The system checks whether an identical or near-identical claim (85% similarity threshold) has been verified within the last 24 hours. If a match exists, the cached verdict is reused immediately." },
        { num: "2", title: "Internal Consensus Check", body: "If 5 or more outlets have reported the same claim consistently without contradiction, the claim is marked corroborated and given a weight of +0.5." },
        { num: "3", title: "Web Search Verification", body: "Claims that pass neither the cache nor consensus check are sent to Claude Sonnet with web search tool enabled. Sonnet searches the web for evidence and returns a verdict with supporting sources and reasoning." },
      ],
    },
    {
      id: "verdicts", num: "06", title: "Verdict Types", kind: "verdicts",
      body: ["Eight possible verdicts are assigned. Six contribute to the outlet's reliability score; two (opinion and not_verifiable) are excluded from scoring. Each scoreable verdict carries a weight that feeds the outlet reliability formula."],
      verdicts: [
        { key: "supported",      weight: "+1.0",   tone: "pos",     meaning: "Confirmed by two genuinely independent sources" },
        { key: "plausible",      weight: "+0.5",   tone: "pos",     meaning: "Consistent with evidence, but only one credible source found" },
        { key: "corroborated",   weight: "+0.5",   tone: "pos",     meaning: "5+ outlets report consistently without contradiction" },
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
        note: "Divisor fixed at 2.5 (v1.6).",
      },
    },
    {
      id: "changes", num: "7.5", title: "Verdicts Can Change", kind: "prose",
      body: ["Verdicts are not permanent. They are reviewed when new editions of the methodology are released or when a verdict dispute is submitted."],
    },
    {
      id: "tiers", num: "08", title: "Outlet Inclusion & Tiers", kind: "prose",
      body: ["Outlets are placed into one of four inclusion tiers based on the total number of verdicts assigned to their claims. Tiers determine whether and how prominently an outlet appears on the public leaderboard."],
      sub: [
        { title: "Published \u2014 100 or more verdicts", intro: "Outlets at this tier have a fully established score. New verdicts adjust the score incrementally rather than meaningfully shifting it." },
        { title: "Stabilizing \u2014 50 to 99 verdicts", intro: "Outlets here have enough verdicts to produce a meaningfully stable score. The Stabilizing label indicates the score is reliable but still maturing." },
        { title: "Limited Data \u2014 20 to 49 verdicts", intro: "Outlets in this tier appear on the leaderboard with a published score, marked as Limited Data. Scores in this band can shift noticeably as more verdicts arrive." },
        { title: "Excluded \u2014 fewer than 20 verdicts", intro: "Outlets with fewer than 20 verdicts are not assigned a public score. Their claims continue to be analysed and stored, but the sample is too small to produce a stable signal." },
      ],
    },
    {
      id: "report", num: "09", title: "Report Page \u2014 Structure & Layout", kind: "prose",
      body: [
        "Each article analysis produces a report page showing the claims extracted, their verdicts, and the supporting sources used to assign each verdict.",
        "Reports include the article's source outlet, that outlet's current reliability score and tier, and a list of every claim assessed during analysis. Each claim displays its verdict, a brief evidence summary, and links to the sources consulted.",
      ],
      callout: { label: "On 'Unscored' articles", text: "An article that contains only opinion and not_verifiable claims (no scoreable verdicts) displays as Unscored on the report page rather than receiving a 0/100 score. An Unscored article does not contribute to the outlet's reliability score in either case; the change is presentational, ensuring the reader is not shown a misleading numeric score for an article that produced no scoreable signal." },
    },
    {
      id: "limits", num: "10", title: "What Verum Signal Does Not Do", kind: "prose",
      body: ["Verum Signal does not rate journalists, editors, or owners. It does not rate opinion content. It does not rate outlets on ideological grounds."],
    },
    {
      id: "changelog", num: "11", title: "Changelog", kind: "prose",
      body: ["This page is the public methodology document. The full version history is preserved at /methodology/archive."],
      sub: [
        { title: "v1.6 \u2014 May 5, 2026", intro: "Methodology consolidation and breaking-news gate consistency.", items: [
          ["Single source of truth", "All scoring constants \u2014 verdict weights, scoreable types, tier thresholds, and formula parameters \u2014 consolidated into a single canonical module. Eliminates drift between the leaderboard, the API, and outlet detail pages."],
          ["Breaking-news gate applied uniformly", "The 6-hour breaking-news gate (Section 02) now applies consistently to every scoring surface: leaderboard, /api/source endpoint, outlet detail aggregates, score history charts, and article-report outlet badges. Previously the gate was only enforced on one of these surfaces, causing live and persisted scores to drift apart by small but real amounts."],
          ["Articles with no scoreable claims display as Unscored", "An article that contains only opinion and not_verifiable claims now displays Unscored on its report page, rather than a misleading 0/100. The article does not contribute to the outlet's score in either case; this change is presentational."],
          ["Extraction prompt rewritten", "The article extraction prompt has been rewritten to acknowledge that opinion-genre articles can contain extractable factual claims. Previously the extraction step was over-filtering opinion articles even when they contained concrete factual assertions."],
          ["Paid extraction depth raised", "Paid (full) reports now extract up to 7 claims per article, raised from 3. Free reports continue to verify the top 2."],
          ["Brand language alignment", "User-facing strings and LLM system prompts updated to consistently use 'verification engine' and 'claim analysis platform' framing. Internal database column names retained for schema stability."],
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
