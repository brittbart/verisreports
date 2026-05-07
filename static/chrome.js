// VerumSignal — shared chrome behavior.
// Nav is now hardcoded into each page; this script only handles scroll behavior
// and mobile menu toggle so the existing nav inherits both.
(function() {
  const nav = document.querySelector('nav.nav');
  if (!nav) return;

  // Scroll: toggle .scrolled for blur-on-scroll effect
  const onScroll = () => nav.classList.toggle('scrolled', window.scrollY > 20);
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  // Mobile hamburger toggle
  const btn = nav.querySelector('.nav-toggle');
  const menu = nav.querySelector('#nav-menu') || nav.querySelector('.nav-links');
  if (btn && menu) {
    btn.addEventListener('click', () => {
      const open = menu.classList.toggle('is-open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    menu.addEventListener('click', (e) => {
      if (e.target.tagName === 'A') {
        menu.classList.remove('is-open');
        btn.setAttribute('aria-expanded', 'false');
      }
    });
  }
})();
