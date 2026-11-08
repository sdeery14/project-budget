/* Timeline chart: closing balance per month, optimistic and conservative, plus purchases.
 * Data comes from data-* attributes on the canvas (no inline script; CSP script-src 'self').
 * Chart.js is vendored under static/vendor/chartjs/. Numbers here are for display only. */
(function () {
  "use strict";

  function parse(el, name) {
    try {
      return JSON.parse(el.getAttribute("data-" + name) || "[]").map(Number);
    } catch (err) {
      return [];
    }
  }

  function draw() {
    var canvas = document.getElementById("timeline-chart");
    if (!canvas || typeof window.Chart === "undefined") {
      return;
    }
    var labels = JSON.parse(canvas.getAttribute("data-labels") || "[]");
    var currency = canvas.getAttribute("data-currency") || "";
    var styles = getComputedStyle(document.documentElement);
    var accent = styles.getPropertyValue("--accent").trim() || "#1f6feb";
    var muted = styles.getPropertyValue("--muted").trim() || "#6b7280";
    var danger = styles.getPropertyValue("--danger").trim() || "#b42318";
    var text = styles.getPropertyValue("--text").trim() || "#1f2328";
    var border = styles.getPropertyValue("--border").trim() || "#d9dde3";

    new window.Chart(canvas, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            type: "line",
            label: "Closing balance (conservative)",
            data: parse(canvas, "conservative"),
            borderColor: accent,
            backgroundColor: accent,
            tension: 0.2,
            yAxisID: "y"
          },
          {
            type: "line",
            label: "Closing balance (optimistic)",
            data: parse(canvas, "optimistic"),
            borderColor: muted,
            backgroundColor: muted,
            borderDash: [6, 4],
            tension: 0.2,
            yAxisID: "y"
          },
          {
            type: "bar",
            label: "Purchases (conservative)",
            data: parse(canvas, "purchases"),
            backgroundColor: danger,
            yAxisID: "y"
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        color: text,
        scales: {
          x: { ticks: { color: text }, grid: { color: border } },
          y: {
            ticks: {
              color: text,
              callback: function (value) { return value + " " + currency; }
            },
            grid: { color: border }
          }
        },
        plugins: {
          legend: { labels: { color: text } },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                return ctx.dataset.label + ": " + Number(ctx.parsed.y).toFixed(2) + " " + currency;
              }
            }
          }
        }
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", draw);
  } else {
    draw();
  }
})();
