content = """function getDomain() {
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
    high:   { score: '#4ADE80', pill: 'rgba(74,222,128,0.12)',  pillBorder: 'rgba(74,222,128,0.25)',  label: 'High' },
    medium: { score: '#FBBF24', pill: 'rgba(251,191,36,0.12)',  pillBorder: 'rgba(251,191,36,0.25)',  label: 'Med' },
    low:    { score: '#F87171', pill: 'rgba(248,113,113,0.12)', pillBorder: 'rgba(248,113,113,0.25)', label: 'Low' },
  };
  const c = colors[tier];

  const badge = document.createElement('div');
  badge.id = 'verum-badge';

  const earlyWarning = data.total_claims < 50
    ? '<div style="margin-top:8px;padding-top:8px;border-top:0.5px solid rgba(255,255,255,0.08);font-size:9px;color:rgba(255,255,255,0.5);font-family:sans-serif;">Early data \xb7 score may shift</div>'
    : '';

  badge.innerHTML = `
    <style>
      #verum-badge {
        position: fixed; bottom: 20px; right: 20px; z-index: 2147483647;
        width: 200px; background: #0A0A0F;
        border: 0.5px solid rgba(255,255,255,0.12); border-radius: 12px;
        padding: 10px 12px; font-family: sans-serif; cursor: default;
        box-shadow: 0 6px 24px rgba(0,0,0,0.6), 0 0 0 0.5px rgba(168,85,247,0.12);
        animation: verum-up 0.3s cubic-bezier(0.2,0.8,0.2,1) forwards; overflow: hidden;
      }
      #verum-badge::before {
        content: ''; position: absolute; inset: 0;
        background-image: linear-gradient(rgba(168,85,247,0.04) 1px, transparent 1px),
          linear-gradient(90deg, rgba(168,85,247,0.04) 1px, transparent 1px);
        background-size: 18px 18px; pointer-events: none;
      }
      #verum-badge:hover { border-color: rgba(168,85,247,0.3); }
      @keyframes verum-up { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:translateY(0); } }
      #verum-btn {
        margin-top: 8px; width: 100%; padding: 6px 10px;
        background: rgba(168,85,247,0.08); border: 0.5px solid rgba(168,85,247,0.25);
        border-radius: 6px; color: #C084FC; font-size: 10px; font-weight: 500;
        cursor: pointer; display: flex; align-items: center; justify-content: space-between;
        font-family: sans-serif; transition: background 0.15s;
      }
      #verum-btn:hover { background: rgba(168,85,247,0.15); border-color: rgba(168,85,247,0.45); }
    </style>
    <div style="position:relative;z-index:1;">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">
        <svg width="80" height="16" viewBox="0 0 80 16" xmlns="http://www.w3.org/2000/svg">
          <path d="M2 8 Q4.5 2 7 8 Q9.5 14 12 8 Q14.5 2 17 8" fill="none" stroke="#a855f7" stroke-width="1.8" stroke-linecap="round"/>
          <circle cx="20" cy="8" r="2" fill="#ec4899"/>
          <text x="25" y="12" font-family="Trebuchet MS,sans-serif" font-size="8.5" font-weight="700" fill="#ffffff" letter-spacing="1">VERUM</text>
          <text x="62" y="12" font-family="Trebuchet MS,sans-serif" font-size="8.5" font-style="italic" fill="#c084fc" letter-spacing="1">SIGNAL</text>
        </svg>
        <div style="font-size:9px;color:rgba(255,255,255,0.3);">${domain}</div>
      </div>
      <div style="display:flex;align-items:baseline;gap:4px;margin-bottom:6px;">
        <span style="font-size:26px;line-height:1;letter-spacing:-1px;color:${c.score};">${score}</span>
        <span style="font-size:13px;color:rgba(255,255,255,0.2);">/100</span>
        <span style="margin-left:auto;display:inline-flex;align-items:center;gap:4px;padding:2px 7px;border-radius:100px;font-size:9px;font-weight:500;letter-spacing:0.05em;text-transform:uppercase;background:${c.pill};border:0.5px solid ${c.pillBorder};color:${c.score};">
          <span style="width:3px;height:3px;border-radius:50%;background:${c.score};display:inline-block;"></span>${c.label}
        </span>
      </div>
      <div style="width:100%;height:2px;background:rgba(255,255,255,0.05);border-radius:1px;overflow:hidden;margin-bottom:8px;">
        <div style="height:100%;width:${score}%;background:${c.score};border-radius:1px;opacity:0.8;"></div>
      </div>
      <button id="verum-btn">
        View breakdown
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#C084FC" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="M12 5l7 7-7 7"/></svg>
      </button>
      ${earlyWarning}
    </div>
  `;

  badge.addEventListener('click', (e) => {
    if (!e.target.closest('#verum-btn')) badge.remove();
  });
  badge.querySelector('#verum-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    window.open('https://verumsignal.com/outlet/' + domain, '_blank');
  });

  document.body.appendChild(badge);
}

function checkSource() {
  const domain = getDomain();
  if (!domain || domain === 'newtab' || domain === '') return;
  fetch('https://web-production-c94e7.up.railway.app/api/source?domain=' + domain)
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
        print('Written: ' + path)
    except Exception as e:
        print('Failed: ' + path + ' — ' + str(e))
