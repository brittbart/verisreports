const { useState, useEffect, useRef } = React;

function cx(...parts) { return parts.filter(Boolean).join(' '); }

const VERDICT_META = {
  supported:      { label: "SUPPORTED",      weight: "+1.0",  tone: "pos",     blurb: "Confirmed by two independent sources." },
  plausible:      { label: "PLAUSIBLE",      weight: "+0.5",  tone: "pos",     blurb: "Consistent with evidence; only one credible source found." },
  corroborated:   { label: "CORROBORATED",   weight: "+0.5",  tone: "pos",     blurb: "5+ outlets report consistently without contradiction." },
  overstated:     { label: "OVERSTATED",     weight: "-0.5",  tone: "neg",     blurb: "Core fact is real but exaggerated or framed misleadingly." },
  disputed:       { label: "DISPUTED",       weight: "-1.0",  tone: "neg",     blurb: "At least one credible source directly contradicts the claim." },
  not_supported:  { label: "NOT_SUPPORTED",  weight: "-1.5",  tone: "neg",     blurb: "Evidence actively contradicts the claim." },
  not_verifiable: { label: "NOT_VERIFIABLE", weight: "excl.", tone: "neutral", blurb: "Cannot confirm or deny -- sources unavailable." },
  opinion:        { label: "OPINION",        weight: "excl.", tone: "neutral", blurb: "Editorial or opinion content -- not a factual signal." },
};

function TOC({ sections, activeId, onJump }) {
  return (
    <nav className="vs-toc" aria-label="Table of contents">
      <div className="vs-toc__label">CONTENTS</div>
      <ol>
        {sections.map((s) => (
          <li key={s.id}>
            <a href={"#" + s.id}
              className={cx("vs-toc__link", activeId === s.id && "is-active")}
              onClick={(e) => { e.preventDefault(); onJump(s.id); }}>
              <span className="vs-toc__num">{s.num}</span>
              <span className="vs-toc__title">{s.title.replace(/^Stage \d+ \u2014 /, "")}</span>
            </a>
          </li>
        ))}
      </ol>
      <div className="vs-toc__footer">
        <div className="vs-toc__brand">VERUM SIGNAL</div>
        <div className="vs-toc__meta">Methodology \u00b7 v1.5</div>
        <div className="vs-toc__meta">April 25, 2026</div>
      </div>
    </nav>
  );
}

function Masthead({ meta }) {
  return (
    <header className="vs-masthead">
      <div className="vs-masthead__row">
        <div className="vs-masthead__brand">
          <span className="vs-logo__verum">VERUM </span><span className="vs-logo__signal">SIGNAL</span>
        </div>
        <div className="vs-masthead__meta">
          <span>METHODOLOGY</span><span className="vs-dot" />
          <span>{meta.version}</span><span className="vs-dot" />
          <span>{meta.date}</span>
        </div>
      </div>
      <div className="vs-masthead__hero">
        <h1 className="vs-display">{meta.title}</h1>
        <p className="vs-lede">{meta.subtitle}</p>
        <p className="vs-principle">{meta.principle}</p>
      </div>
    </header>
  );
}

function PipelineDiagram() {
  const stages = [
    { n: "01", title: "Ingestion",    note: "URL \u2192 article text" },
    { n: "02", title: "Extraction",   note: "Sonnet \u2192 claim list" },
    { n: "03", title: "Priority",     note: "score \u2265 30 \u2192 queue" },
    { n: "04", title: "Verification", note: "cache \u00b7 consensus \u00b7 web" },
  ];
  const [active, setActive] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setActive(a => (a + 1) % stages.length), 1800);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="vs-pipeline">
      {stages.map((s, i) => (
        <React.Fragment key={s.n}>
          <div className={cx("vs-pipeline__node", active === i && "is-active")} onMouseEnter={() => setActive(i)}>
            <div className="vs-pipeline__num">{s.n}</div>
            <div className="vs-pipeline__title">{s.title}</div>
            <div className="vs-pipeline__note">{s.note}</div>
            <div className="vs-pipeline__pulse" />
          </div>
          {i < stages.length - 1 && (
            <div className={cx("vs-pipeline__edge", active > i && "is-past", active === i + 1 && "is-live")}>
              <span className="vs-pipeline__spark" />
            </div>
          )}
        </React.Fragment>
      ))}
    </div>
  );
}

function SectionRenderer({ section }) {
  const s = section;
  return (
    <section id={s.id} className="vs-section">
      <div className="vs-section__head">
        <span className="vs-section__num">{s.num}</span>
        <h2 className="vs-section__title">{s.title}</h2>
      </div>
      <div className="vs-section__body">
        {(s.body || []).map((p, i) => <p key={i} className="vs-p">{p}</p>)}
        {s.kind === "pipeline" && <PipelineDiagram />}
        {(s.sub || []).map((sub, i) => (
          <div key={i} className="vs-sub">
            <div className="vs-sub__title">{sub.title}</div>
            {sub.intro && <p className="vs-p">{sub.intro}</p>}
            {(sub.items || []).length > 0 && (
              <ul className="vs-checks">
                {sub.items.map(([title, desc], j) => (
                  <li key={j}><b>{title}</b>{desc && " -- " + desc}</li>
                ))}
              </ul>
            )}
          </div>
        ))}
        {(s.checks || []).length > 0 && (
          <ul className="vs-checks">
            {s.checks.map(([title, desc], i) => (
              <li key={i}><b>{title}</b>{desc && " -- " + desc}</li>
            ))}
          </ul>
        )}
        {s.kind === "verdicts" && s.verdicts && (
          <div className="vs-legend">
            <div className="vs-legend__header"><span>VERDICT</span><span>WEIGHT</span><span>MEANING</span></div>
            {s.verdicts.map(v => (
              <div key={v.key} className={cx("vs-legend__row", "is-" + v.tone)}>
                <span className={cx("vs-verdict", "is-" + v.tone)}>{v.key.toUpperCase().replace("_", " ")}</span>
                <span className="vs-legend__weight">{v.weight}</span>
                <span className="vs-legend__blurb">{v.meaning}</span>
              </div>
            ))}
          </div>
        )}
        {s.kind === "score" && s.formula && (
          <div>
            {s.formula.steps.map((step, i) => (
              <div key={i} className={step.highlight ? "vs-formula-line vs-formula-line--highlight" : "vs-formula-line"}>
                <span className="vs-formula-label">{step.label}</span>
                <span>{step.expr}</span>
              </div>
            ))}
            {s.formula.note && <p className="vs-formula-note">{s.formula.note}</p>}
          </div>
        )}
        {s.callout && (
          <aside className="vs-callout">
            <div className="vs-callout__label">{s.callout.label}</div>
            <div className="vs-callout__text">{s.callout.text}</div>
          </aside>
        )}
        {s.bodyAfter && <p className="vs-p">{s.bodyAfter}</p>}
      </div>
    </section>
  );
}

function Report() {
  const data = window.VS_DATA;
  if (!data) return <div>Loading...</div>;
  const [activeId, setActiveId] = useState(data.sections[0].id);
  const mainRef = useRef(null);

  useEffect(() => {
    const main = mainRef.current;
    if (!main) return;
    const handleScroll = () => {
      const ids = data.sections.map(s => s.id);
      for (const id of ids) {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top < main.clientHeight / 2) {
          setActiveId(id);
        }
      }
    };
    main.addEventListener("scroll", handleScroll);
    return () => main.removeEventListener("scroll", handleScroll);
  }, [data]);

  const onJump = (id) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: "smooth" });
    setActiveId(id);
  };

  return (
    <div className="vs-root">
      <aside className="vs-sidebar">
        <TOC sections={data.sections} activeId={activeId} onJump={onJump} />
      </aside>
      <main className="vs-main" ref={mainRef}>
        <Masthead meta={data.meta} />
        {data.sections.map(s => <SectionRenderer key={s.id} section={s} />)}
      </main>
    </div>
  );
}

Object.assign(window, { Report });
