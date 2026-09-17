/* ============================================================================
   YuE2 Studio — theme, tooltips, sheet music.
   Loaded into <head> by app.py, so the theme is decided before first paint.
   Expects window.YUE_TIPS (id -> text) to have been set already.
   ========================================================================== */
(function () {
  "use strict";

  /* ------------------------------------------------------------- theme --- */
  // Gradio re-mounts <gradio-app> after hydration and can drop classes set on
  // it, so the class is (re)asserted from the same observer that binds tips
  // rather than from a stack of setTimeouts racing the framework.
  var THEME_KEY = "yue2_theme";

  function stored() {
    try {
      return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
    } catch (e) {
      return "light";
    }
  }

  function applyTheme(theme) {
    var dark = theme === "dark";
    [document.documentElement, document.body,
     document.querySelector("gradio-app")].forEach(function (el) {
      if (el) el.classList.toggle("dark", dark);
    });
    var btn = document.getElementById("theme-toggle-btn");
    var want = dark ? "Light" : "Dark";
    if (btn && btn.textContent !== want) btn.textContent = want;
  }

  window.setAppTheme = function (theme) {
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* private mode */ }
    applyTheme(theme);
  };

  window.toggleTheme = function () {
    window.setAppTheme(stored() === "dark" ? "light" : "dark");
  };

  // Before paint: <html> is the only element that exists yet, and it carries
  // the custom properties, so this alone prevents the flash.
  applyTheme(stored());

  /* -------------------------------------------------------- sheet music --- */
  window.renderSheetMusic = function (abc) {
    var container = document.getElementById("sheet-music-paper");
    if (!container) return;
    if (!window.ABCJS) {
      // Previously this returned silently and the panel stayed blank forever.
      container.innerHTML =
        '<p class="sheet-placeholder">Notation renderer not loaded.<br>' +
        "Place <code>abcjs-basic-min.js</code> in <code>assets/</code> " +
        "and restart to see engraved staves. The ABC text on the left is " +
        "complete either way — synthesis does not need this panel.</p>";
      return;
    }
    if (!abc || !abc.trim()) {
      container.innerHTML =
        '<p class="sheet-placeholder">No score yet. Press ' +
        "<b>Write score</b> or paste ABC on the left.</p>";
      return;
    }
    window.ABCJS.renderAbc("sheet-music-paper", abc, {
      responsive: "resize",
      scale: 0.95,
      add_classes: true,
      staffwidth: 720
    });
  };

  /* ----------------------------------------------------------- tooltips --- */
  function initTips() {
    if (!document.body) { requestAnimationFrame(initTips); return; }

    var tip = document.getElementById("yue-tip");
    if (!tip) {
      tip = document.createElement("div");
      tip.id = "yue-tip";
      document.body.appendChild(tip);
    }

    function place(e) {
      var x = e.clientX + 16, y = e.clientY + 20;
      var w = tip.offsetWidth, h = tip.offsetHeight;
      if (x + w > window.innerWidth - 12) x = window.innerWidth - w - 12;
      if (y + h > window.innerHeight - 12) y = e.clientY - h - 14;
      tip.style.left = x + "px";
      tip.style.top = y + "px";
    }

    // Bind to the parameter NAME only. Binding the whole block means the
    // tooltip sits over the control while you are dragging its slider.
    function targetOf(el) {
      if (el.tagName === "BUTTON") return el;
      return el.querySelector('span[data-testid="block-info"]')
          || el.querySelector("label > span")
          || el.querySelector("label")
          || el.querySelector("button")
          || el;
    }

    function bind() {
      applyTheme(stored());
      Object.keys(window.YUE_TIPS || {}).forEach(function (id) {
        var el = document.getElementById(id);
        if (!el) return;
        var t = targetOf(el);
        if (!t || t.dataset.yueTipBound) return;
        t.dataset.yueTipBound = "1";
        t.classList.add("yue-tip-target");
        var text = window.YUE_TIPS[id];
        t.addEventListener("mouseenter", function (e) {
          tip.textContent = text;
          tip.classList.add("on");
          place(e);
        });
        t.addEventListener("mousemove", place);
        t.addEventListener("mouseleave", function () {
          tip.classList.remove("on");
        });
      });
    }

    bind();

    var queued = false;
    var observer = new MutationObserver(function () {
      if (queued) return;
      queued = true;
      requestAnimationFrame(function () {
        queued = false;
        observer.disconnect();
        try { bind(); } finally {
          observer.observe(document.body, { childList: true, subtree: true });
        }
      });
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  initTips();
})();
