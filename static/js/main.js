/* SecureTrust Bank — shared vanilla JS */
(function () {
  "use strict";

  /* Mobile sidebar toggle */
  var hamburger = document.querySelector(".hamburger");
  var sidebar = document.querySelector(".sidebar");
  if (hamburger && sidebar) {
    hamburger.addEventListener("click", function () {
      sidebar.classList.toggle("open");
    });
    document.addEventListener("click", function (e) {
      if (
        sidebar.classList.contains("open") &&
        !sidebar.contains(e.target) &&
        !hamburger.contains(e.target)
      ) {
        sidebar.classList.remove("open");
      }
    });
  }

  /* Auto-dismiss flash messages */
  document.querySelectorAll(".messages .alert").forEach(function (el) {
    setTimeout(function () {
      el.style.transition = "opacity .4s";
      el.style.opacity = "0";
      setTimeout(function () { el.remove(); }, 400);
    }, 6000);
  });

  /* Modals: any [data-modal-open="#id"] opens, [data-modal-close] closes */
  document.querySelectorAll("[data-modal-open]").forEach(function (trigger) {
    trigger.addEventListener("click", function (e) {
      e.preventDefault();
      var target = document.querySelector(trigger.getAttribute("data-modal-open"));
      if (target) target.classList.add("open");
    });
  });
  document.querySelectorAll(".modal-backdrop").forEach(function (backdrop) {
    backdrop.addEventListener("click", function (e) {
      if (e.target === backdrop) backdrop.classList.remove("open");
    });
    backdrop.querySelectorAll("[data-modal-close]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        backdrop.classList.remove("open");
      });
    });
  });

  /* Confirm-before-submit forms */
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) e.preventDefault();
    });
  });

  /* Transfer form: live destination account lookup */
  var destInput = document.getElementById("id_destination_account");
  var lookupResult = document.getElementById("account-lookup-result");
  if (destInput && lookupResult) {
    var lookupUrl = lookupResult.getAttribute("data-lookup-url");
    var timer = null;
    var runLookup = function () {
      var value = destInput.value.trim();
      if (value.length !== 10) {
        lookupResult.textContent = "";
        return;
      }
      lookupResult.textContent = "Looking up account…";
      lookupResult.style.color = "";
      fetch(lookupUrl + "?account_number=" + encodeURIComponent(value), {
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.found) {
            lookupResult.textContent = "✓ " + data.name;
            lookupResult.style.color = "#0e9f6e";
          } else {
            lookupResult.textContent = "✗ " + (data.error || "Account not found.");
            lookupResult.style.color = "#dc2626";
          }
        })
        .catch(function () {
          lookupResult.textContent = "Could not verify the account right now.";
          lookupResult.style.color = "#d97706";
        });
    };
    destInput.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(runLookup, 350);
    });
    if (destInput.value.trim().length === 10) runLookup();
  }

  /* Beneficiary picker fills the destination field */
  document.querySelectorAll("[data-beneficiary]").forEach(function (el) {
    el.addEventListener("click", function (e) {
      e.preventDefault();
      if (destInput) {
        destInput.value = el.getAttribute("data-beneficiary");
        destInput.dispatchEvent(new Event("input"));
        destInput.focus();
      }
    });
  });

  /* Staff dashboard: 7-day volume bar chart on <canvas> */
  var chart = document.getElementById("volume-chart");
  if (chart && chart.getContext) {
    var labels = JSON.parse(chart.getAttribute("data-labels") || "[]");
    var values = JSON.parse(chart.getAttribute("data-values") || "[]");
    var ctx = chart.getContext("2d");
    var dpr = window.devicePixelRatio || 1;
    var cssW = chart.clientWidth || chart.parentElement.clientWidth || 600;
    var cssH = 260;
    chart.width = cssW * dpr;
    chart.height = cssH * dpr;
    chart.style.height = cssH + "px";
    ctx.scale(dpr, dpr);

    var pad = { top: 16, right: 12, bottom: 28, left: 12 };
    var w = cssW - pad.left - pad.right;
    var h = cssH - pad.top - pad.bottom;
    var max = Math.max.apply(null, values.concat([1]));
    var barW = (w / values.length) * 0.55;
    var gap = w / values.length;

    ctx.font = "12px sans-serif";
    for (var i = 0; i < values.length; i++) {
      var x = pad.left + gap * i + (gap - barW) / 2;
      var barH = (values[i] / max) * (h - 14);
      var y = pad.top + (h - barH);
      ctx.fillStyle = "#0e9f6e";
      ctx.beginPath();
      if (ctx.roundRect) {
        ctx.roundRect(x, y, barW, barH, [4, 4, 0, 0]);
        ctx.fill();
      } else {
        ctx.fillRect(x, y, barW, barH);
      }
      ctx.fillStyle = "#64748b";
      ctx.textAlign = "center";
      ctx.fillText(labels[i] || "", x + barW / 2, cssH - 8);
      if (values[i] > 0) {
        ctx.fillStyle = "#1e293b";
        ctx.fillText(
          "₦" + Number(values[i]).toLocaleString(undefined, { maximumFractionDigits: 0 }),
          x + barW / 2,
          y - 4
        );
      }
    }
  }
})();
