/* A precise pointer with a softly trailing halo; native controls stay native. */
(function () {
  "use strict";
  var media = window.matchMedia('(hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference) and (forced-colors: none)');
  var root = document.documentElement;
  var dot = document.createElement('div');
  var halo = document.createElement('div');
  dot.className = 'cursor-dot';
  halo.className = 'cursor-halo';
  dot.setAttribute('aria-hidden', 'true');
  halo.setAttribute('aria-hidden', 'true');
  halo.innerHTML = '<span class="cursor-halo__shape"><span>↗</span></span>';
  document.body.append(dot, halo);
  var x = 0, y = 0, hx = 0, hy = 0, frame = 0, visible = false;
  var nativeControls = 'input, textarea, select, option, [contenteditable]:not([contenteditable="false"]), iframe, [disabled], [aria-disabled="true"]';

  function position(node, px, py) { node.style.transform = 'translate3d(' + px + 'px,' + py + 'px,0)'; }
  function hide() {
    visible = false;
    root.classList.remove('cursor-enabled', 'cursor-action', 'cursor-down');
    cancelAnimationFrame(frame);
    frame = 0;
  }
  function allowed() {
    return media.matches && !document.hidden && !document.querySelector('.motion-toggle[aria-pressed="true"]');
  }
  function tick() {
    hx += (x - hx) * 0.24;
    hy += (y - hy) * 0.24;
    position(halo, hx, hy);
    if (Math.abs(x - hx) + Math.abs(y - hy) > 0.1) frame = requestAnimationFrame(tick);
    else frame = 0;
  }
  function update(target) {
    if (!allowed() || !target || target.closest(nativeControls)) { hide(); return; }
    if (!visible) { hx = x; hy = y; position(halo, x, y); }
    visible = true;
    root.classList.add('cursor-enabled');
    root.classList.toggle('cursor-action', !!target.closest('a[href], button, [role="button"], summary, label[for]'));
    position(dot, x, y);
    if (!frame) frame = requestAnimationFrame(tick);
  }
  document.addEventListener('pointermove', function (event) {
    if (event.pointerType !== 'mouse') { hide(); return; }
    x = event.clientX; y = event.clientY;
    update(event.target);
  }, { passive: true });
  document.addEventListener('pointerover', function (event) {
    if (event.pointerType === 'mouse' && visible) update(event.target);
  }, { passive: true });
  document.addEventListener('pointerdown', function (event) {
    if (event.pointerType !== 'mouse') hide();
    else if (visible) root.classList.add('cursor-down');
  }, { passive: true });
  document.addEventListener('pointerup', function () { root.classList.remove('cursor-down'); }, { passive: true });
  document.addEventListener('pointerout', function (event) { if (!event.relatedTarget) hide(); });
  document.addEventListener('keydown', hide);
  document.addEventListener('visibilitychange', hide);
  document.addEventListener('click', function () { if (!allowed()) hide(); });
  window.addEventListener('blur', hide);
  window.addEventListener('scroll', function () { if (visible) update(document.elementFromPoint(x, y)); }, { passive: true });
  media.addEventListener('change', hide);
})();
