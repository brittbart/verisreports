import React, { useState } from 'react';

const data = window.REPORT_DATA;

function SectionHeader({ label, title, subtitle }) {
  return (
    <div className="vs-section-head">
      <div className="vs-label">{label}</div>
      <h2 className="vs-h2">{title}</h2>
      {subtitle && <p className="vs-lead">{subtitle}</p>}
    </div>
  );
}

function HeroSection() {
  return (
    <header className="vs-hero">
      <div className="vs-hero-inner">
        <div className="vs-hero-badge">
          <svg width="28" height="20" viewBox="0 0 54 40" fill="none">
            <path d="M3 20 Q 11 4 18 20 T 33 20" stroke="var(--accent)" strokeWidth="3.2" fill="none" strokeLinecap="round"/>
            <circle cx="37" cy="18" r="4.2" fill="var(--accent)"/>
          </svg>
          <span className="vs-hero-wordmark">VERUM {'\u00a0'} <em>SIGNAL</em></span>
        </div>
        <h1 className="vs-hero-title">{data.meta.title}</h1>
        <p className="vs-hero-sub">{data.meta.subtitle}</p>
        <div className="vs-hero-meta">
          <span>Version <strong>{data.meta.version}</strong></span>
          <span>Status <strong>{data.meta.status}</strong></span>
          <span>Last updated <strong>{data.meta.lastUpdated}</strong></span>
        </div>
      </div>
    </header>
  );
}

function PrinciplesSection() {
  return (
    <section className="vs-section">
      <SectionHeader label="01" title={data.principles.title} />
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

function VerdictsSection() {
  return (
    <section className="vs-section">
      <SectionHeader label="02" title={data.verdicts.title} subtitle={data.verdicts.subtitle} />
      <div className="vs-verdict-grid">
        {data.verdicts.items.map((item) => (
          <div key={item.id} className={item.excluded ? 'vs-verdict-card vs-verdict-card--excluded' : 'vs-verdict-card'}>
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
  return (
    <section className="vs-section">
      <SectionHeader label="03" title={data.formula.title} />
      <div className="vs-formula-block">
        {data.formula.steps.map((step) => (
          <div key={step.id} className={step.highlight ? 'vs-formula-line vs-formula-line--highlight' : 'vs-formula-line'}>
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
  return (
    <section className="vs-section">
      <SectionHeader label="04" title={data.tiers.title} subtitle={data.tiers.subtitle} />
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
  return (
    <section className="vs-section">
      <SectionHeader label="05" title={data.pipeline.title} />
      <div className="vs-pipeline">
        {data.pipeline.steps.map((step, i) => (
          <div key={step.id} className="vs-pipeline-step">
            <div className="vs-pipeline-num">{String(i + 1).padStart(2, '0')}</div>
            <div className="vs-pipeline-body">
              <h4 className="vs-pipeline-title">{step.title}</h4>
              <p className="vs-pipeline-desc">{step.body}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function AttributionSection() {
  return (
    <section className="vs-section">
      <SectionHeader label="06" title={data.attribution.title} />
      <div className="vs-card-stack">
        {data.attribution.items.map((item) => (
          <div key={item.id} className="vs-card">
            <h3 className="vs-card-title">{item.title}</h3>
            <p className="vs-card-body">{item.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function LimitationsSection() {
  return (
    <section className="vs-section">
      <SectionHeader label="07" title={data.limitations.title} />
      <div className="vs-card-stack">
        {data.limitations.items.map((item) => (
          <div key={item.id} className="vs-card">
            <h3 className="vs-card-title">{item.title}</h3>
            <p className="vs-card-body">{item.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function DisputesSection() {
  return (
    <section className="vs-section">
      <SectionHeader label="08" title={data.disputes.title} />
      <div className="vs-card">
        <p className="vs-card-body">{data.disputes.body}</p>
        <div className="vs-callout" style={{marginTop: '1rem'}}>
          <div className="vs-callout-title">What to include</div>
          <p className="vs-callout-body">{data.disputes.include}</p>
        </div>
        <p style={{marginTop: '1rem', fontSize: '13px', color: 'var(--text-dim)'}}>
          Submit via the correction form on any report page, or at {'h\u2026'}
          <a href={data.disputes.url} style={{color: 'var(--accent)'}}>verumsignal.com/disputes</a>.
        </p>
      </div>
    </section>
  );
}

function Report() {
  return (
    <main className="vs-main">
      <div className="vs-wrap">
        <HeroSection />
        <PrinciplesSection />
        <VerdictsSection />
        <FormulaSection />
        <TiersSection />
        <PipelineSection />
        <AttributionSection />
        <LimitationsSection />
        <DisputesSection />
      </div>
    </main>
  );
}

Object.assign(window, { Report });
