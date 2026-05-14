// Mobile nav toggle — shared across pages
(function () {
  const toggle = document.querySelector(".nav-toggle");
  const links = document.querySelector(".nav-links");
  if (!toggle || !links) return;
  toggle.addEventListener("click", () => {
    const isOpen = links.classList.toggle("is-open");
    toggle.setAttribute("aria-expanded", String(isOpen));
  });
})();

// Nav: blur on scroll (matches homepage behavior)
(function() {
  var nav = document.querySelector('nav.nav');
  if (!nav) return;
  var onScroll = function() { nav.classList.toggle('scrolled', window.scrollY > 20); };
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
})();
