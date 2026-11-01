/* Progressive enhancement: financial values remain readable and unchanged. */
(function () {
  "use strict";
  document.documentElement.classList.remove("js");
  if (!window.gsap) return;
  window.__financeMotion = true;
  var gsap = window.gsap;
  if (window.ScrollTrigger) gsap.registerPlugin(window.ScrollTrigger);
  var media;
  function startMotion() {
  media = gsap.matchMedia();

  media.add("(prefers-reduced-motion: no-preference)", function (context) {
    var cleanups = [];
    var intro = gsap.timeline({ defaults: { ease: "power3.out", duration: 0.75 } });
    intro.from("[data-hero-line]", { yPercent: 100, stagger: 0.09 })
      .from("[data-hero]", { opacity: 0, y: 14, stagger: 0.1 }, 0.2)
      .from("[data-orbit]", { opacity: 0, scale: 0.96, duration: 0.9 }, 0.1);
    var ring = document.querySelector("[data-ring]");
    if (ring) intro.from(ring, { attr: { "stroke-dasharray": "0 100" }, duration: 1.6, ease: "power2.inOut" }, 0.35);
    intro.from(".finance-stage", { y: 24, opacity: 0.5, duration: 0.9, clearProps: "transform,opacity" }, 0)
      .from(".sculpture-core", { scale: 0.85, duration: 0.9 }, 0.2);
    var ambient = gsap.timeline({ repeat: -1 });
    ambient.to(".ring-a", { rotationZ: "+=360", duration: 24, ease: "none" }, 0)
      .to(".ring-b", { rotationZ: "-=360", duration: 24, ease: "none" }, 0)
      .to(".ring-c", { rotationZ: "+=360", duration: 24, ease: "none" }, 0)
      .to(".orbit-satellite", { rotation: 360, svgOrigin: "180 180", duration: 24, ease: "none" }, 0);
    var floats = gsap.to(".floating-tag", { y: -8, stagger: 0.5, duration: 3.5, repeat: -1, yoyo: true, ease: "sine.inOut" });
    var ribbon = gsap.to(".ribbon-track", { xPercent: -50, duration: 28, ease: "none", repeat: -1 });
    function visibility() {
      [ambient, floats, ribbon].forEach(function (animation) { animation.paused(document.hidden); });
    }
    document.addEventListener("visibilitychange", visibility);
    cleanups.push(function () { document.removeEventListener("visibilitychange", visibility); });
    if (window.ScrollTrigger) {
      gsap.to(".sculpture", { y: 45, rotation: 12, ease: "none", scrollTrigger: { trigger: ".overview-hero", start: "top top", end: "bottom top", scrub: 1 } });
      gsap.to(".reading-progress", { scaleX: 1, ease: "none", scrollTrigger: { start: 0, end: "max", scrub: 0.3 } });
    }

    context.add("reveal", function (element, delay) {
      gsap.from(element, { opacity: 0, y: 24, delay: delay, duration: 0.7, ease: "power3.out", clearProps: "opacity,transform" });
      var progress = element.querySelector("[data-progress]");
      if (progress) gsap.from(progress, { scaleX: 0, transformOrigin: "left", duration: 1.2, ease: "power3.out", clearProps: "transform" });
    });
    if ("IntersectionObserver" in window) {
      var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry, index) {
          if (!entry.isIntersecting) return;
          context.reveal(entry.target, Math.min(index * 0.07, 0.21));
          observer.unobserve(entry.target);
        });
      }, { threshold: 0.12 });
      document.querySelectorAll("[data-animate]").forEach(function (element) { observer.observe(element); });
      cleanups.push(function () { observer.disconnect(); });
    }
    return function () { cleanups.forEach(function (cleanup) { cleanup(); }); };
  });

  media.add("(prefers-reduced-motion: no-preference) and (hover: hover) and (pointer: fine)", function (context) {
    var cleanups = [];
    document.querySelectorAll(".account-card").forEach(function (card) {
      var rotateX = gsap.quickTo(card, "rotationX", { duration: 0.55, ease: "power3.out" });
      var rotateY = gsap.quickTo(card, "rotationY", { duration: 0.55, ease: "power3.out" });
      function move(event) {
        var rect = card.getBoundingClientRect();
        rotateX((0.5 - (event.clientY - rect.top) / rect.height) * 5);
        rotateY(((event.clientX - rect.left) / rect.width - 0.5) * 5);
      }
      function reset() { rotateX(0); rotateY(0); }
      card.addEventListener("pointermove", move);
      card.addEventListener("pointerleave", reset);
      cleanups.push(function () { card.removeEventListener("pointermove", move); card.removeEventListener("pointerleave", reset); });
    });
    context.add("highlight", function (row, active) {
      gsap.to(row, { x: active ? 5 : 0, backgroundColor: active ? "var(--accent-soft)" : "transparent", duration: 0.25, overwrite: "auto" });
    });
    document.querySelectorAll(".txn").forEach(function (row) {
      function enter() { context.highlight(row, true); }
      function leave() { context.highlight(row, false); }
      row.addEventListener("pointerenter", enter);
      row.addEventListener("pointerleave", leave);
      cleanups.push(function () { row.removeEventListener("pointerenter", enter); row.removeEventListener("pointerleave", leave); });
    });
    return function () { cleanups.forEach(function (cleanup) { cleanup(); }); };
  });
  }
  startMotion();
  var toggle = document.querySelector(".motion-toggle");
  if (toggle) {
    toggle.hidden = false;
    toggle.addEventListener("click", function () {
      var paused = toggle.getAttribute("aria-pressed") !== "true";
      toggle.setAttribute("aria-pressed", String(paused));
      toggle.textContent = paused ? "Resume motion" : "Pause motion";
      if (paused) media.revert();
      else startMotion();
    });
  }
})();
