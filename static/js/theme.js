(function () {
  "use strict";
  var key = 'expensure-theme';
  var theme = 'dark';
  try { if (localStorage.getItem(key) === 'light') theme = 'light'; } catch (_) { /* Storage is optional. */ }
  function apply(value) {
    theme = value === 'light' ? 'light' : 'dark';
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="color-scheme"]').content = theme;
    document.querySelector('meta[name="theme-color"]').content = theme === 'light' ? '#f7f3ed' : '#101010';
    var button = document.querySelector('.theme-toggle');
    if (button) {
      button.hidden = false;
      button.setAttribute('aria-label', theme === 'light' ? 'Switch to dark theme' : 'Switch to day theme');
      button.querySelector('[data-theme-label]').textContent = theme === 'light' ? 'Dark mode' : 'Day mode';
      button.querySelector('[data-theme-icon]').textContent = theme === 'light' ? '☾' : '☀';
    }
  }
  apply(theme); // Before CSS paints, so a saved preference never flashes the other theme.
  document.addEventListener('DOMContentLoaded', function () {
    apply(theme);
    document.querySelector('.theme-toggle').addEventListener('click', function () {
      apply(theme === 'light' ? 'dark' : 'light');
      try { localStorage.setItem(key, theme); } catch (_) { /* Keep working for this page. */ }
    });
  });
  window.addEventListener('storage', function (event) {
    if (event.key === key || event.key === null) apply(event.newValue);
  });
})();
