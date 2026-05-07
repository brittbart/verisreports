// VerumSignal — shared chrome behavior.
// Hardened version: works across all pages, defensive about timing/quirks.
(function() {
  function init() {
    var nav = document.querySelector('nav.nav');
    if (!nav) return;

    // Scroll: toggle .scrolled for blur-on-scroll effect
    var onScroll = function() { nav.classList.toggle('scrolled', window.scrollY > 20); };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();

    // Mobile hamburger toggle
    var btn = nav.querySelector('.nav-toggle');
    var menu = document.getElementById('nav-menu') || nav.querySelector('.nav-links');
    if (!btn || !menu) return;

    // Force the button to be type=button so it can't accidentally submit
    if (btn.tagName === 'BUTTON' && !btn.type) btn.type = 'button';
    btn.setAttribute('type', 'button');

    function toggleMenu(e) {
      if (e) { e.preventDefault(); e.stopPropagation(); }
      var open = menu.classList.toggle('is-open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    // Bind both click and touchstart so iOS/Android both work
    btn.addEventListener('click', toggleMenu);
    btn.addEventListener('touchend', function(e) {
      e.preventDefault();
      toggleMenu(e);
    });

    // Tap a link: close menu
    menu.addEventListener('click', function(e) {
      if (e.target.tagName === 'A') {
        menu.classList.remove('is-open');
        btn.setAttribute('aria-expanded', 'false');
      }
    });

    // Tap outside: close menu
    document.addEventListener('click', function(e) {
      if (!nav.contains(e.target) && menu.classList.contains('is-open')) {
        menu.classList.remove('is-open');
        btn.setAttribute('aria-expanded', 'false');
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
