// data.js -- Verum Signal Methodology Report data
window.REPORT_DATA = {
  meta: {
    title: "Verum Signal Methodology v1.5",
    subtitle: "How we evaluate claims and score outlets",
    version: "v1.5",
    lastUpdated: "April 2026",
    status: "Locked",
  },
  principles: {
    title: "Core Principles",
    items: [
      { id: "p1", title: "Evidence over alignment", body: "No claim is graded on a curve based on the outlet that published it or the political tendency it supports. A claim is evaluated against what independent, credible sources report -- nothing else." },
      { id: "p2", title: "Signals, not verdicts on people", body: "Verum Signal scores outlet reporting accuracy, not speaker truthfulness. An outlet that accurately reports a false statement receives no penalty." },
      { id: "p3", title: "Documented and auditable", body: "Every element of the pipeline -- prompts, weights, formula constants, gate thresholds -- is documented here and subject to revision through the public methodology changelog." },
    ],
  },
  verdicts: {
    title: "Verdict Types",
    subtitle: "Eight verdict labels are used. Six carry scoring weight; two are excluded from the outlet score denominator.",
    items: [
      { id: "v1", label: "supported", weight: "+1.0", color: "#34d399", excluded: false, body: "Confirmed by two genuinely independent sources. Anonymous sources cannot produce this verdict." },
      { id: "v2", label: "plausible", weight: "+0.5", color: "#60a5fa", excluded: false, body: "Consistent with available evidence; one credible source only. Not contradicted, but not independently confirmed." },
      { id: "v3", label: "corroborated", weight: "+0.5", color: "#93c5fd", excluded: false, body: "Five or more outlets report the same claim consistently, with no external contradicting source." },
      { id: "v4", label: "overstated", weight: "-0.5", color: "#fbbf24", excluded: false, body: "The core fact is real, but the framing, magnitude, or implication is exaggerated or misleading." },
      { id: "v5", label: "disputed", weight: "-1.0", color: "#f87171", excluded: false, body: "At least one credible independent source directly contradicts the claim." },
      { id: "v6", label: "not_supported", weight: "-1.5", color: "#ef4444", excluded: false, body: "Evidence actively contradicts the claim. The strongest negative verdict." },
      { id: "v7", label: "not_verifiable", weight: "excl.", color: "#6b7280", excluded: true, body: "Necessary sources are unavailable, paywalled, or the claim cannot be empirically tested. Excluded from scoring." },
      { id: "v8", label: "opinion", weight: "excl.", color: "#6b7280", excluded: true, body: "Editorial or opinion content. Excluded from outlet scoring." },
    ],
  },
  formula: {
    title: "Scoring Formula",
    steps: [
      { id: "f1", label: "weighted_sum", expr: "\u2211 (verdict_weight \u00d7 verdict_count)" },
      { id: "f2", label: "scoreable", expr: "count of verdicts excluding not_verifiable and opinion" },
      { id: "f3", label: "normalised", expr: "(weighted_sum / scoreable + 1.5) / 2.5", highlight: true },
      { id: "f4", label: "score", expr: "min(max( normalised \u00d7 100, 0), 100)" },
    ],
    note: "Divisor fixed at 2.5 (v1.5). All not_supported scores 0; all supported scores 100.",
    bands: [
      { label: "High", range: "70\u2013100", color: "#34d399", desc: "Claims consistently well-supported by independent sources." },
      { label: "Medium", range: "40\u201369", color: "#fbbf24", desc: "Mixed record -- some well-supported claims, some contested or overstated." },
      { label: "Low", range: "0\u201339", color: "#f87171", desc: "Frequent disputes or unsupported claims in the verified sample." },
    ],
  },
  tiers: {
    title: "Outlet Tiers",
    subtitle: "Outlets are assigned to one of four tiers based on total scoreable verdicts.",
    items: [
      { id: "t1", name: "Excluded", range: "< 20", color: "#6b7280", desc: "Insufficient data. No score displayed." },
      { id: "t2", name: "Limited Data", range: "20\u201349", color: "#fbbf24", desc: "Score shown with wide confidence interval caveat." },
      { id: "t3", name: "Stabilizing", range: "50\u201399", color: "#60a5fa", desc: "Score becoming statistically meaningful." },
      { id: "t4", name: "Published", range: "100+", color: "#34d399", desc: "Full confidence interval computed and displayed." },
    ],
  },
  pipeline: {
    title: "Verification Pipeline",
    steps: [
      { id: "s1", title: "Ingestion & deduplication", body: "Articles are pulled from monitored RSS feeds. Wire reprints are detected via byline analysis and excluded from outlet scoring." },
      { id: "s2", title: "Claim extraction", body: "Factual claims are extracted from article text. Opinion and editorial content is classified and excluded at this stage." },
      { id: "s3", title: "Priority scoring", body: "Claims are scored for verification priority. A 30-point threshold filters out low-signal claims." },
      { id: "s4", title: "Breaking news gate", body: "Claims about fast-moving events are held for a minimum 6-hour window before verification begins." },
      { id: "s5", title: "Evidence search & triangulation", body: "The pipeline queries multiple independent sources. Anonymous sources cannot produce a supported verdict." },
      { id: "s6", title: "Verdict assignment", body: "One of eight verdict labels is assigned based on the evidence gathered." },
      { id: "s7", title: "Score computation", body: "Verdicts are aggregated into an outlet score. not_verifiable and opinion are excluded from the denominator." },
    ],
  },
  attribution: {
    title: "Quote & Attribution Rules",
    items: [
      { id: "a1", title: "Accurately reported false statement", body: "If an outlet accurately quotes a speaker who made a false claim, the outlet is not penalised." },
      { id: "a2", title: "Inaccurately reported statement", body: "If an outlet misrepresents what a speaker said, that is an outlet reporting failure and is scored accordingly." },
      { id: "a3", title: "Anonymous sources", body: "Claims sourced exclusively from anonymous sources cannot receive a supported verdict. Maximum achievable is plausible." },
      { id: "a4", title: "Wire reprints", body: "Articles identified as wire reprints via byline detection are excluded from outlet scoring." },
    ],
  },
  limitations: {
    title: "Known Limitations",
    items: [
      { id: "l1", title: "Data maturity -- most outlets below 100 verdicts", body: "At current verdict volume, confidence intervals are wide for most outlets." },
      { id: "l2", title: "Paywall exclusion", body: "Paywalled articles cannot be accessed for verification, creating systematic underrepresentation of paywalled outlets." },
      { id: "l3", title: "Priority threshold bias", body: "The 30-point threshold filters entertainment and lifestyle content. The verdict pool is intentionally biased toward political and policy claims." },
      { id: "l4", title: "RSS sample representativeness", body: "RSS feeds are a subset of total publishing volume. The verified sample may not be representative of an outlet's full output." },
      { id: "l5", title: "No per-article scores", body: "Verum Signal produces verdict distributions, not per-article scores." },
      { id: "l6", title: "No speaker accuracy records", body: "Speaker accuracy tracking is explicitly out of scope for v1." },
    ],
  },
  disputes: {
    title: "Dispute Process",
    sla: "10 business days",
    body: "Any outlet, journalist, or reader may submit a dispute on any verdict. Disputes are reviewed within 10 business days. If a dispute results in a verdict change, the change is logged in the verdict history with the reason.",
    include: "The specific claim you are disputing, the verdict assigned, and the evidence you believe was missed or misweighed. Links to primary sources are most useful.",
    url: "/disputes",
  },
};
