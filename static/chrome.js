// VerumSignal — shared chrome (nav + footer) injected into each page
(function() {
  const currentPage = document.body.dataset.page || '';

  const logoSVG = `<svg width="280" height="40" viewBox="0 0 185 28" xmlns="http://www.w3.org/2000/svg" aria-label="Verum Signal">
    <path d="M4 14 Q7 6 10 14 Q13 22 16 14 Q19 6 22 14"
          fill="none" stroke="#a855f7" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="25" cy="14" r="2.5" fill="#ec4899"/>
    <text x="32" y="19" font-family="Trebuchet MS,sans-serif"
          font-size="13" font-weight="700" fill="#ffffff" letter-spacing="1.5">VERUM</text>
    <text x="88" y="19" font-family="Trebuchet MS,sans-serif"
          font-size="13" font-weight="400" font-style="italic"
          fill="#c084fc" letter-spacing="1.5" transform="skewX(-6)">SIGNAL</text>
  </svg>`;

  const links = [
    { href: 'index.html',        label: 'Home',          key: 'home' },
    { href: 'how-it-works.html', label: 'How it works',  key: 'how' },
    { href: 'pricing.html',      label: 'Pricing',       key: 'pricing' },
    { href: 'leaderboard.html',  label: 'Leaderboard',   key: 'leaderboard' },
  ];

  const nav = document.createElement('nav');
  nav.className = 'nav';
  nav.innerHTML = `
    <div class="nav-inner">
      <a href="index.html" class="nav-logo">${logoSVG}</a>
      <div class="nav-links">
        ${links.map(l => `<a href="${l.href}" class="${l.key === currentPage ? 'active' : ''}">${l.label}</a>`).join('')}
        <a href="index.html#analyze" class="nav-cta">Analyze</a>
      </div>
    </div>
  `;
  document.body.insertBefore(nav, document.body.firstChild);

  // Scroll behavior
  const onScroll = () => nav.classList.toggle('scrolled', window.scrollY > 20);  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();


})();