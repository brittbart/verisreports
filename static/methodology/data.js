// Shared content for the Verum Signal methodology report.
// Keep copy faithful to the source docx.

window.VS_DATA = {
  meta: {
    brand: "VERUM SIGNAL",
    title: "Article Analysis Methodology",
    subtitle: "How Verum Signal Evaluates a Single Article",
    version: "v1.5",
    date: "April 25, 2026",
    principle: "We provide the signals. You decide.",
  },
  sections: [
    {
      id: "overview",
      num: "01",
      title: "Overview",
      kind: "prose",
      body: [
        "When a user submits an article URL to Verum Signal, the system extracts factual claims from the article, scores them for verification priority, and runs each claim through a three-step verification pipeline. The result is a claim-level verdict report alongside the outlet's overall reliability score.",
        "This document describes exactly what happens at each stage, what decisions are made, and what the user sees at the end.",
      ],
      callout: {
        label: "Brand principle",
        text: "Verum Signal never describes itself as a fact-checker. It evaluates claims on evidence. We provide the signals. You decide.",
      },
    },
    {
      id: "ingestion",
      num: "02",
      title: "Stage 1 — Article Ingestion",
      kind: "stage",
      stageIndex: 1,
      body: [
        "The article URL is submitted by the user. Verum Signal fetches the article content and extracts the full text. The following checks happen at ingestion:",
      ],
      checks: [
        ["Language detection", "non-English articles are flagged and excluded from scoring"],
        ["Published date extraction", "articles without a resolvable published_at timestamp are excluded from reliability scoring"],
        ["Breaking news gate", "articles published in the last 6 hours are tracked but not scored, because facts in fast-moving stories often update or get corrected within hours of publication. This 6-hour gate is the v1 baseline; a dynamic gate with shorter windows for stable claim types and longer windows for evolving stories is planned for v1.1."],
        ["Source identification", "the article's domain is extracted and matched to the outlet's canonical name in the database"],
      ],
      callout: {
        label: "Why 6 hours",
        text: "Breaking news claims are frequently updated, corrected, or retracted within hours of publication. Scoring them immediately would penalise outlets for normal journalistic correction cycles.",
      },
    },
    {
      id: "attribution",
      num: "2.5",
      title: "Claim Attribution",
      kind: "prose",
      body: [
        "Not every claim in an article counts against the publishing outlet. Two categories are excluded from outlet scoring:",
      ],
      attribution: [
        ["Wire reprints", "articles that are verbatim reprints from wire services like Reuters, AP, AFP, or Bloomberg, where the outlet is carrying syndicated content without substantive editorial modification. These articles are excluded entirely from the outlet's claim pool when detected. Detection is based on byline analysis."],
        ["Quoted claims", "when an outlet reports what someone else said (for example, quoting a politician), the outlet is evaluated on whether the quote is accurate, not on whether the speaker's claim is true. False statements by public figures, accurately quoted, do not penalize the outlet. The speaker's claim is logged separately for record-keeping."],
      ],
      bodyAfter: "This policy ensures outlets are scored on what they themselves originated, not what they republished or quoted. Detection is best-effort; documented in the master methodology Section 5.1 as a known limitation.",
    },
    {
      id: "extraction",
      num: "03",
      title: "Stage 2 — Claim Extraction",
      kind: "stage",
      stageIndex: 2,
      body: [
        "The article text is sent to Claude Sonnet via the Anthropic API. Sonnet is instructed to extract discrete, verifiable factual claims from the article. A factual claim is a specific assertion about the world that can in principle be confirmed or arefuted by evidence.",
      ],
      sub: [
        {
          title: "3.1  What qualifies as a claim",
          intro: "Sonnet extracts claims that are:",
          items: [
            ["Specific and falsifiable", "'The unemployment rate fell to 3.7% in March' qualifies; 'the economy is struggling' does not"],
            ["Attributed or asserted as fact", "not clearly labelled as speculation, pred