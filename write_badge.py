content = r"""function getDomain() {
  return window.location.hostname.replace('www.', '');
}

function createBadge(data, domain) {
  const existing = document.getElementById('verum-badge');
  if (existing) existing.remove();

  const score = Math.round(data.score ?? 0);
  let tier = 'low';
  if (score >= 75) tier = 'high';
  else if (score >= 45) tier = 'medium';

  const colors = {
    high:   { score: '#4ADE80', pill: 'rgba(74,222,128,0.12)', pillBorder: 'rgba(74,222,128,0.25)', label: 'High' },
    medium: { score: '#FBBF24', pill: 'rgba(251,191,36,0.12)', pillBorder: 'rgba(251,191,36,0.25)', label: 'Medium' },
    low:    { score: '#F87171', pill: 'rgba(248,113,113,0.12)', pillBorder: 'rgba(248,113,113,0.25)', label: 'Low' },
  };
  const c = colors[tier];

  const badge = document.createElement('div');
  badge.id = 'verum-badge';

  const earlyWarning = data.total_claims < 50
    ? `<div style="margin-top:10px;padding-top:10px;border-top:0.5px solid rgba(255,255,255,0.07);font-size:10px;color:rgba(255,255,255,0.35);">Early data \u00b7 score may shift</div>`
    : '';

  badge.innerHTML = `
    <style>
      @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500&family=DM+Serif+Display:ital@0;1&display=swap');
      #verum-badge {
        position: fixed;
        bottom: 24px;
        right: 24px;
        z-index: 2147483647;
        width: 220px;
        background: #0A0A0F;
        border: 0.5px solid rgba(255,255,255,0.1);
        border-radius: 14px;
        padding: 14px 16px;
        font-family: 'DM Sans', -apple-system, sans-serif;
        cursor: pointer;
        box-shadow: 0 8px 32px rgba(0,0,0,0.5), 0 0 0 0.5px rgba(168,85,247,0.15);
        animation: verum-up 0.35s cubic-bezier(0.2,0.8,0.2,1) forwards;
        overflow: hidden;
      }
      #verum-badge::before {
        content: '';
        position: absolute;
        inset: 0;
        background-image: linear-gradient(rgba(168,85,247,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(168,85,247,0.03) 1px, transparent 1px);
        background-size: 20px 20px;
        pointer-events: none;
      }
      #verum-badge:hover { border-color: rgba(168,85,247,0.35); }
      @keyframes verum-up {
        from { opacity: 0; transform: translateY(12px); }
        to   { opacity: 1; transform: translateY(0); }
      }
    </style>
    <div style="position:relative;z-index:1;">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
        <svg width="52" height="14" viewBox="0 0 52 14" xmlns="http://www.w3.org/2000/svg">
          <path d="M2 7 Q4 2 6 7 Q8 12 10 7 Q12 2 14 7" fill="none" stroke="#a855f7" stroke-width="1.5" stroke-linecap="round"/>
          <circle cx="16" cy="7" r="1.5" fill="#ec4899"/>
          <text x="20" y="11" font-family="Trebuchet MS,sans-serif" font-size="7" font-weight="700" fill="#ffffff" letter-spacing="0.8">VERUM</text>
          <text x="42" y="11" font-family="Trebuchet MS,sans-serif" font-size="7" font-style="italic" fill="#c084fc" letter-spacing="0.8">SIGNAL</text>
        </svg>
        <div style="font-size:10px;color:rgba(255,255,255,0.3);">${domain}</div>
      </div>
      <div style="display:flex;align-items:baseline;gap:6px;margin-bottom:8px;">
        <span style="font-family:'DM Serif Display',serif;font-size:44px;line-height:1;letter-spacing:-2px;color:${c.score};">${score}</span>
        <span style="font-family:'DM Serif Display',serif;font-size:18px;color:rgba(255,255,255,0.2);">/100</span>
        <span style="margin-left:auto;display:inline-flex;align-items:center;gap:5px;padding:3px 8px;border-radius:100px;font-size:10px;font-weight:500;letter-spacing:0.05em;text-transform:uppercase;background:${c.pill};border:0.5px solid ${c.pillBorder};color:${c.score};">
          <span style="width:4px;height:4px;border-radius:50%;background:${c.score};display:inline-block;"></span>
          ${c.label}
        </span>
      </div>
      <div style="width:100%;height:2px;background:rgba(255,255,255,0.05);border-radius:1px;overflow:hidden;margin-bottom:10px;">
        <div style="height:100%;width:${score}%;background:${c.score};border-radius:1px;opacity:0.8;"></div>
      </div>
      <div style="display:flex;justify-content:space-between;font-size:11px;color:rgba(255,255,255,0.4);">
        <span><span style="color:rgba(255,255,255,0.7);font-weight:500;">${data.verified ?? 0}</span> verified</span>
        <span><span style="color:#FBBF24;font-weight:500;">${data.overstated ?? 0}</span> overstated</span>
        <span><span style="color:#F87171;font-weight:500;">${data.disputed ?? 0}</span> disputed</span>
      </div>
      ${earlyWarning}
    </div>
  `;

  badge.addEventListener('click', () => badge.remove());
  document.body.appendChild(badge);
}

function checkSource() {
  const domain = getDomain();
  if (!domain || domain === 'newtab' || domain === '') return;
  fetch(`https://web-production-c94e7.up.railway.app/api/source?domain=${domain}`)
    .then(r => r.json())
    .then(data => { if (data.status === 'found') createBadge(data, domain); })
    .catch(() => {});
}

checkSource();
"""

paths = [
    '/home/veris/projects/veris-extension/content.js',
    '/mnt/c/Users/britt/OneDrive/Desktop/veris-extension/content.js',
]

for path in paths:
    try:
        with open(path, 'w') as f:
            f.write(content)
        print(f'Written: {path}')
    except Exception as e:
        print(f'Failed {path}: {e}')
