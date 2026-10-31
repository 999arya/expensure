/* Auth motion is decorative; credentials and validation never animate. */
(function () {
  "use strict";
  if (!window.gsap) return;
  var gsap = window.gsap;
  var media = gsap.matchMedia();
  var toggle = document.querySelector('.auth-story .motion-toggle');
  var paused = false;
  media.add('(prefers-reduced-motion: no-preference)', function () {
    gsap.from('[data-auth-intro]', { y: 22, opacity: 0, stagger: 0.12, duration: 0.85, ease: 'power3.out', clearProps: 'transform,opacity' });
    var orbit = gsap.timeline({ repeat: -1 });
    orbit.to('.auth-art .ring-a', { rotationZ: '+=360', duration: 26, ease: 'none' }, 0)
      .to('.auth-art .ring-b', { rotationZ: '-=360', duration: 26, ease: 'none' }, 0)
      .to('.auth-art .ring-c', { rotationZ: '+=360', duration: 26, ease: 'none' }, 0);
    var inView = true;
    function sync() { orbit.paused(paused || document.hidden || !inView); }
    function pause() {
      paused = !paused;
      toggle.setAttribute('aria-pressed', String(paused));
      toggle.textContent = paused ? 'Resume motion' : 'Pause motion';
      sync();
    }
    toggle.hidden = false;
    toggle.addEventListener('click', pause);
    document.addEventListener('visibilitychange', sync);
    var observer;
    if ('IntersectionObserver' in window) {
      observer = new IntersectionObserver(function (entries) { inView = entries[0].isIntersecting; sync(); });
      observer.observe(document.querySelector('.auth-story'));
    }
    sync();
    return function () {
      toggle.hidden = true;
      toggle.removeEventListener('click', pause);
      document.removeEventListener('visibilitychange', sync);
      if (observer) observer.disconnect();
    };
  });
})();
