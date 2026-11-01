/* Shared section reveals and a continuous navigation indicator. No delayed links. */
(function () {
  'use strict';
  if (!window.gsap) return;
  var gsap = window.gsap;
  var main = document.getElementById('main');
  if (!main) return;
  var media = gsap.matchMedia();
  var toggle = document.querySelector('.motion-toggle');
  var paused = function () { return toggle && toggle.getAttribute('aria-pressed') === 'true'; };
  var nav = document.querySelector('.tabbar');
  var active = nav && nav.querySelector('[aria-current="page"]');
  var pill, pillTween;
  var key = 'expensure-nav-position';
  function alignPill() {
    if (!pill) return;
    if (pillTween) pillTween.kill();
    gsap.set(pill, { x: active.offsetLeft, y: active.offsetTop, width: active.offsetWidth, height: active.offsetHeight });
  }
  if (active) {
    pill = document.createElement('span');
    pill.className = 'nav-pill';
    pill.setAttribute('aria-hidden', 'true');
    nav.prepend(pill);
    nav.classList.add('has-nav-pill');
    alignPill();
    var previous;
    try { previous = JSON.parse(sessionStorage.getItem(key)); sessionStorage.removeItem(key); } catch (_) { /* Optional continuity. */ }
    if (previous && previous.to === location.pathname && Date.now() - previous.time < 10000 &&
        previous.viewport === innerWidth && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      pillTween = gsap.fromTo(pill, { x: previous.x, width: previous.width }, {
        x: active.offsetLeft, width: active.offsetWidth, duration: .48, ease: 'power3.out'
      });
    }
    nav.addEventListener('click', function (event) {
      var link = event.target.closest('a');
      if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      try { sessionStorage.setItem(key, JSON.stringify({ x: active.offsetLeft, width: active.offsetWidth,
        viewport: innerWidth, to: new URL(link.href).pathname, time: Date.now() })); } catch (_) { /* Links remain native. */ }
    });
    window.addEventListener('resize', alignPill, { passive: true });
  }
  var seen = new WeakSet();
  function start() {
    media.add('(prefers-reduced-motion: no-preference)', function (context) {
      if (paused()) return;
      var observer;
      var candidates = main.querySelectorAll(':scope > .page-head, :scope > .filters, :scope > .allowance, :scope > .section, :scope > .empty, :scope > .txn-list, :scope > .plan-list, :scope > .goal-list, :scope > .card-grid, :scope > .auth, .auth-form-panel .auth-card > h1, .auth-form-panel .auth-card > .muted');
      // Dashboard already owns its section choreography.
      if (main.querySelector('.overview-hero')) candidates = [];
      context.add('reveal', function (element, delay) {
        if (seen.has(element)) return;
        seen.add(element);
        if (element.contains(document.activeElement)) return;
        gsap.fromTo(element, { y: 20, opacity: .65 }, { y: 0, opacity: 1,
          delay: delay, duration: .65, ease: 'power3.out', clearProps: 'transform,opacity' });
      });
      if ('IntersectionObserver' in window) {
        observer = new IntersectionObserver(function (entries) {
          var order = 0;
          entries.forEach(function (entry) {
            if (!entry.isIntersecting) return;
            context.reveal(entry.target, Math.min(order++ * .065, .18));
            observer.unobserve(entry.target);
          });
        }, { threshold: 0, rootMargin: '0px 0px -24px 0px' });
        candidates.forEach(function (element) { observer.observe(element); });
      }
      return function () { if (observer) observer.disconnect(); };
    });
  }
  start();
  if (toggle) toggle.addEventListener('click', function () {
    media.revert();
    if (!paused()) start();
    else alignPill();
  });
  window.addEventListener('pageshow', function (event) {
    if (event.persisted) { media.revert(); alignPill(); start(); }
  });
})();
