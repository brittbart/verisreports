const { useState, useEffect } = React;

function HeroSection() {
  const data = window.REPORT_DATA || window.VS_DATA;
  const meta = data?.meta || {};
  return (
    <header className="vs-hero">
      <div className="vs-hero-inner">
        <div className="vs-hero-badge">
          <svg width="28" height="20" viewBox="0 0 54 40" fill="none">
            <path d="M3 20 Q 11 4 18 20 T 33 20" stroke="var(--accent)" strokeWidth="3.2" fill="none" strokeLinecap="round"/>
            <circle cx="37" cy="18" r="4.2" fill="var(--accent)"/>
          </svg>
          <span className="vs-hero-wordmark">VERUM <em>SIGNAL</em></span>
        </div>
        <h1 className="vs-hero-title">{meta.title || "Article Analysis Methodology"}</h1>
        <p className="vs-hero-sub">{meta.subtitle || "How Verum Signal Evaluates a Single Article"}</p>
        <div className="vs-hero-meta">
          <span>Version <strong>{meta.version || "v1.5"}</strong></span>
          <span>Status <strong>{meta.status || "Locked"}</strong></span>
          <span>{(meta.lastUpdated || meta.date) && `Updated ${meta.lastUpdated || meta.date}`}</span>
        </div>
      </div>
    </header>
  );
}

function PrinciplesSection() {
  const data = window.REPORT_DATA;
  if (!data?.principles?.items?.length) return null;
  return (
    <section className="vs-section">
      <div className="vs-section-head">
        <div className="vs-label">01</div>
        <h2 className="vs-h2">{data.principles.title}</h2>
      </div>
      <div className="vs-card-stack">
        {data.principles.items.map((item) => (
          <div key={item.id} className="vs-card">
            <h3 className="vs-card-title">{item.title}</h3>
            <p className="vs-card-body">{item.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function VerdictSection() {
  const data = window.REPORT_DATA;
  if (!data?.verdicts?.items?.length) return null;
  return (
    <section className="vs-section">
      <div className="vs-section-head">
        <div className="vs-label">02</div>
        <h2 className="vs-h2">{data.verdicts.title}</h2>
        {data.verdicts.subtitle && <p className="vs-lead">{data.verdicts.subtitle}</p>}
      </div>
      <div className="vs-verdict-grid">
        {data.verdicts.items.map((item) => (
          <div key={item.id || item.label} className={item.excluded ? 'vs-verdict-card vs-verdict-card--excluded' : 'vs-verdict-card'}>
            {item.excluded && <div className="vs-excluded-cap">Excluded from scoring</div>}
            <div className="vs-verdict-row">
              <span className="vs-verdict-dot" style={{backgroundColor: item.color}} />
              <span className="vs-verdict-weight">{item.weight}</span>
              <span className="vs-verdict-pill" style={{borderColor: item.color, color: item.color}}>{item.label}</span>
            </div>
            <h4 className="vs-verdict-name">{item.label}</h4>
            <p className="vs-verdict-body">{item.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function FormulaSection() {
  const data = window.REPORT_DATA;
  if (!data?.formula) return null;
  return (
    <section className="vs-section">
      <div className="vs-section-head">
        <div className="vs-label">03</div>
        <h2 className="vs-h2">{data.formula.title}</h2>
      </div>
      <div className="vs-formula-block">
        {data.formula.steps.map((step) => (
          <div key={step.id || step.label} className={step.highlight ? 'vs-formula-line vs-formula-line--highlight' : 'vs-formula-line'}>
            <span className="vs-formula-label">{step.label}</span>
            <span className="vs-formula-expr">{step.expr}</span>
          </div>
        ))}
        <p className="vs-formula-note">{data.formula.note}</p>
      </div>
      <div className="vs-band-stack">
        {data.formula.bands.map((b) => (
          <div key={b.label} className="vs-band-row">
            <span className="vs-band-dot" style={{backgroundColor: b.color}} />
            <span className="vs-band-label" style={{color: b.color}}>{b.label}</span>
            <span className="vs-band-range">{b.range}</span>
            <span className="vs-band-desc">{b.desc}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function TiersSection() {
  const data = window.REPORT_DATA;
  if (!data?.tiers?.items?.length) return null;
  return (
    <section className="vs-section">
      <div className="vs-section-head">
        <div className="vs-label">04</div>
        <h2 className="vs-h2">{data.tiers.title}</h2>
        {data.tiers.subtitle && <p className="vs-lead">{data.tiers.subtitle}</p>}
      </div>
      <div className="vs-tier-grid">
        {data.tiers.items.map((item) => (
          <div key={item.id} className="vs-tier-card">
            <div className="vs-tier-name">{item.name}</div>
            <div className="vs-tier-range" style={{color: item.color}}>{item.range}</div>
            <p className="vs-tier-desc">{item.desc}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function PipelineSection() {
  const data = window.REPORT_DATA;
  if (!data?.pipeline?.steps?.length) return null;
  return (
    <section className="vs-section">
      <div className="vs-section-head">
        <div className="vs-label">05</div>
        <h2 className="vs-h2">{data.pipeline.title}</h2>
      </div>
      <div className="vs-pipeline-wrap">
        {data.pipeline.steps.map((step, i) => (
          <div key={step.id} className="vs-pipeline-step">
            <div className="vs-pipeline-num">{String(i + 1).padStart(2, '0')}</div>
            <div>
              <h4 className="vs-pipeline-title">{step.title}</h4>
              <p className="vs-pipeline-desc">{step.body || step.desc}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function DisputesSection() {
  const data = window.REPORT_DATA;
  if (!data?.disputes) return null;
  return (
    <section className="vs-section">
      <div className="vs-section-head">
        <div className="vs-label">06</div>
        <h2 className="vs-h2">{data.disputes.title}</h2>
      </div>
      <div className="vs-card">
        <p className="vs-card-body">{data.disputes.body}</p>
        {data.disputes.include && (
          <div className="vs-callout" style={{marginTop: '1rem'}}>
            <div className="vs-callout-title">What to include</div>
            <p className="vs-callout-body">{data.disputes.include}</p>
          </div>
        )}
        <p style={{marginTop: '1rem', fontSize: '13px', color: 'var(--fg-dim)'}}>
          Submit via the correction form on any report page, or at{' '}
          <a href={data.disputes.url || '/disputes'} style={{color: 'var(--accent)'}}>verumsignal.com/disputes</a>.
        </p>
      </div>
    </section>
  );
}

function Report() {
  return (
    <main className="vs-main">
      <HeroSection />
      <PrinciplesSection />
      <VerdictSection />
      <FormulaSection />
      <TiersSection />
      <PipelineSection />
      <DisputesSection />
    </main>
  );
}

Object.assign(window, { Report });
