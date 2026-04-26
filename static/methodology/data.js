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
        ["Breaking news gate", "articles published in the last 6 hours are tracked but not scored"],
        ["Source identification", "the article's domain is matched to the outlet in the database"],
      ],
    },
    {
      id: "extraction",
      num: "03",
      title: "Stage 2 — Claim Extraction",
      kind: "stage",
      stageIndex: 2,
      body: [
        "The article text is sent to Claude Sonnet via the Anthropic API. Sonnet extracts discrete, verifiable factual claims from the article.",
      ],
    },
    {
      id: "verification",
      num: "04",
      title: "Stage 3 — Verification",
      kind: "stage",
      stageIndex: 3,
      body: [
        "Each claim is checked against independent primary sources. No guessing or inference. A verdict is assigned based on what the evidence shows.",
      ],
    },
    {
      id: "verdicts",
      num: "05",
      title: "Verdict Types",
      kind: "verdicts",
      subtitle: "Eight verdict labels are used. Six carry scoring weight; two are excluded from the outlet score.",
      items: [
        { label: "supported", weight: "+1.0", color: "#34d399", excluded: false, body: "Confirmed by two genuinely independent sources." },
        { label: "plausible", weight: "+0.5", color: "#60a5fa", excluded: false, body: "Consistent with available evidence; one credible source only." },
        { label: "corroborated", weight: "+0.5", color: "#93c5fd", excluded: false, body: "Five+ outlets report the same claim consistently." },
        { label: "overstated", weight: "-0.5", color: "#fbbf24", excluded: false, body: "The core fact is real but framing or magnitude is exaggerated." },
        { label: "disputed", weight: "-1.0", color: "#f87171", excluded: false, body: "At least one credible source directly contradicts the claim." },
        { label: "not_supported", weight: "-1.5", color: "#ef4444", excluded: false, body: "Evidence actively contradicts the claim." },
        { label: "not_verifiable", weight: "excl.", color: "#6b7280", excluded: true, body: "Nectessary sources unavailable or the claim cannot be empirically tested." },
        { label: "opinion", weight: "excl.", color: "#6b7280", excluded: true, body: "Editorial or opinion content. Excluded from outlet scoring." },
      ],
    },
    {
      id: "formula",
      num: "06",
      title: "Scoring Formula",
      kind: "formula",
      steps: [
        { label: "weighted_sum", expr: "\u2211 (verdict_weight \u00d7 verdict_count)" },
        { label: "scoreable", expr: "count of verdicts excluding not_verifiable and opinion" },
        { label: "normalised", expr: "(weighted_sum / scoreable + 1.5) / 2.5", highlight: true },
        { label: "score", expr: "min(max( normalised \u00d7 100, 0), 100)" },
      ],
      note: "Divisor fixed at 2.5 (v1.5). All not_supported scores 0; all supported scores 100.",
      bands: [
        { label: "High", range: "70\u2013100", color: "#34d399", desc: "Claims consistently well-supported." },
        { label: "Medium", range: "40\u201369", color: "#fbbf24", desc: "Mixed record." },
        { label: "Low", range: "0\u201339", color: "#f87171", desc: "Frequent disputes or unsupported claims." },
      ],
    },
    {
      id: "tiers",
      num: "07",
      title: "Outlet Tiers",
      kind: "tiers",
      subtitle: "Outlets are assigned to one of four tiers based on total scoreable verdicts.",
      items: [
        { id: "t1", name: "Excluded", range: "< 20", color: "#6b7280", desc: "Insufficient data." },
        { id: "t2", name: "Limited Data", range: "20\u201349", color: "#fbbf24", desc: "Score shown with wide confidence interval." },
        { id: "t3", name: "Stabilizing", range: "50\u201399", color: "#60a5fa", desc: "Score becoming statistically meaningful." },
        { id: "t4", name: "Published", range: "100+", color: "#34d399", desc: "Full confidence interval computed." },
      ],
    },
    {
      id: "limitations",
      num: "08",
      title: "Known Limitations",
      kind: "prose",
      body: [
        "Verum Signal produces verdict distributions, not per-article scores. Speaker accuracy tracking is explicitly out of scope for v1. Paywalled articles cannot be accessed for verification. RSS feeds are a subset of total publishing volume.",
      ],
    },
    {
      id: "disputes",
      num: "09",
      title: "Dispute Process",
      kind: "prose",
      body: [
        "Any outlet, journalist, or reader may submit a dispute on any verdict. Disputes are reviewed within 10 business days. If a dispute results in a verdict change, the change is logged in the verdict history.",
      ],
      disputeUrl: "/disputes",
    },
  ],
};

// Backwards compatibility
window.REPORT_DATA = {
  meta: {
    title: "Verum Signal Methodology v1.5",
    subtitle: "How we evaluate claims and score outlets",
    version: "v1.5",
    lastUpdated: "April 2026",
    status: "Locked",
  },
  principles: window.VS_DATA.sections.find(s => s.id === 'overview') ? {
    title: "Core Principles",
    items: [
      { id: "p1", title: "Evidence over alignment", body: "No claim is graded on a curve based on the outlet that published it." },
      { id: "p2", title: "Signals, not verdicts on people", body: "Verum Signal scores outlet reporting accuracy, not speaker truthfulness." },
      { id: "p3", title: "Documented and auditable", body: "Every element of the pipeline is documented here and subject to revision." },
    ],
  } : { title: "Core Principles", items: [] },
  verdicts: window.VS_DATA.sections.find(s => s.id === 'verdicts') || { title: "Verdict Types", items: [] },
  formula: window.VS_DATA.sections.find(s => s.id === 'formula') || { title: "Scoring Formula", steps: [] },
  tiers: window.VS_DATA.sections.find(s => s.id === 'tiers') || { title: "Outlet Tiers", items: [] },
  pipeline: { title: "Verification Pipeline", steps: window.VS_DATA.sections.filter(s => s.kind === 'stage').map(s => ({ id: s.id, title: s.title, body: (s.body || []).join(' ') })) },
  attribution: { title: "Quote & Attribution Rules", items: [] },
  limitations: { title: "Known Limitations", items: [] },
  disputes: { title: "Dispute Process", sla: "10 business days", body: "Any outlet, journalist, or neader may submit a dispute on any verdict.", include: "The specific claim, the verdict assigned, and the evidence you believe was missed.", url: "/disputes" },
};
