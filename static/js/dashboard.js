/* Dashboard motion layer.
 *
 * CSS owns layout, colour and instant feedback. GSAP owns timing only.
 * If GSAP is missing, or the user prefers reduced motion, every element is
 * shown immediately and this file does nothing else.
 */
(function () {
  "use strict";

  var root = document.documentElement;

  function revealAll() {
    root.classList.remove("js");
  }

  if (!window.gsap) {
    revealAll();
    return;
  }

  // From here on, the head script's timeout will not reveal the page.
  // Anything that throws is caught below and revealed instead.
  window.__financeMotion = true;

  try {
    var gsap = window.gsap;
    var mm = gsap.matchMedia();

    mm.add("(prefers-reduced-motion: reduce)", function () {
      revealAll();
    });

    mm.add("(prefers-reduced-motion: no-preference)", function () {
      var notice = gsap.utils.toArray("[data-animate='notice']");
      var cards = gsap.utils.toArray("[data-animate='card']");
      var rows = gsap.utils.toArray("[data-animate='txn']");
      var goal = gsap.utils.toArray("[data-animate='goal']");
      var bar = document.querySelector("[data-progress]");

      if (!notice.length && !cards.length && !rows.length && !goal.length && !bar) {
        revealAll();
        return;
      }

      var tl = gsap.timeline({
        defaults: { duration: 0.35, ease: "power2.out" },
        onComplete: revealAll,
      });

      if (notice.length) {
        tl.fromTo(
          notice,
          { opacity: 0, y: 10 },
          { opacity: 1, y: 0, stagger: 0.05 }
        );
      }

      if (cards.length) {
        tl.fromTo(
          cards,
          { opacity: 0, y: 12 },
          { opacity: 1, y: 0, stagger: 0.06 },
          notice.length ? "-=0.2" : 0
        );
      }

      if (rows.length) {
        tl.fromTo(
          rows,
          { opacity: 0, y: 10 },
          { opacity: 1, y: 0, stagger: 0.04 },
          "-=0.2"
        );
      }

      if (goal.length) {
        tl.fromTo(goal, { opacity: 0, y: 10 }, { opacity: 1, y: 0 }, "-=0.15");
      }

      if (bar) {
        var target = Number(bar.getAttribute("value")) || 0;
        var counter = { value: 0 };
        bar.value = 0;

        tl.to(
          counter,
          {
            value: target,
            duration: 0.6,
            ease: "power2.out",
            onUpdate: function () {
              bar.value = counter.value;
            },
          },
          0.2
        );
      }
    });
  } catch (err) {
    revealAll();
    throw err;
  }
})();
