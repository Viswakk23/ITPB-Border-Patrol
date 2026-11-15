/* ITBP Border Command: top bar and small helpers shared by every page */
(function () {
  "use strict";

  // apply the saved theme at once (dark is the default)
  var theme = localStorage.getItem("theme") || "dark";
  document.documentElement.setAttribute("data-theme", theme);

  var SEV = { LOW: "#22c55e", MEDIUM: "#eab308", HIGH: "#f97316", CRITICAL: "#ef4444" };
  var TOAST = { info: "#38bdf8", ok: "#22c55e", warn: "#eab308", control: "#f97316" };

  var UI = {
    SEV: SEV,
    esc: function (t) {
      return String(t == null ? "" : t).replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
    },
    col: function (v) { return v > 60 ? "#22c55e" : v > 30 ? "#eab308" : "#ef4444"; },
    time: function (iso) { return (iso || "").split("T")[1] || ""; },
    jget: function (u) {
      return fetch(u).then(function (r) { if (!r.ok) throw new Error(u); return r.json(); });
    },
    // turns the status text of a vehicle into [label, chip colour]
    stateOf: function (s) {
      s = s || "";
      if (/^responding/.test(s)) return ["Responding", "bad"];
      if (/^on scene/.test(s)) return ["On scene", "bad"];
      if (/^returning/.test(s)) return ["Returning", "info"];
      if (/maintenance/.test(s)) return ["Maintenance", "crit"];
      if (/out of fuel/.test(s)) return ["Out of fuel", "crit"];
      if (/^patrol|^holding/.test(s)) return ["Patrol", "ok"];
      return ["Standby", "mut"];
    },
    toast: function (text, kind, ms) {
      var box = document.querySelector(".toasts");
      if (!box) {
        box = document.createElement("div");
        box.className = "toasts";
        document.body.appendChild(box);
      }
      var color = SEV[kind] || TOAST[kind] || TOAST.info;
      var title = SEV[kind] ? kind + " ALERT" : kind === "control" ? "Control centre" : kind === "warn" ? "Warning" : "";
      var el = document.createElement("div");
      el.className = "toast";
      el.style.borderLeftColor = color;
      el.innerHTML = (title ? '<b style="color:' + color + '">' + title + "</b>" : "") + UI.esc(text);
      var kill = function () { el.style.opacity = "0"; el.style.transition = "opacity .3s"; setTimeout(function () { el.remove(); }, 300); };
      el.onclick = kill;
      box.appendChild(el);
      setTimeout(kill, ms || 7000);
    },
  };
  window.UI = UI;

  var SHIELD =
    '<svg width="34" height="34" viewBox="0 0 34 34" aria-hidden="true"><defs><linearGradient id="gg" x1="0" y1="0" x2="1" y2="1">' +
    '<stop offset="0" stop-color="#60a5fa"/><stop offset="1" stop-color="#2563eb"/></linearGradient></defs>' +
    '<path d="M17 2 29 6.5v9.2c0 7.6-4.9 13.2-12 16.3C9.9 28.9 5 23.3 5 15.7V6.5z" fill="url(#gg)"/>' +
    '<circle cx="17" cy="16" r="6" stroke="#fff" stroke-width="1.8" fill="none"/><circle cx="17" cy="16" r="1.8" fill="#fff"/><path d="M17 7.5v3M17 21.5v3M8.5 16h3M22.5 16h3" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/></svg>';
  var SUN = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
  var MOON = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';

  var page = document.body.getAttribute("data-page") || "";
  var links = [
    ["map", "Command Map", "/map/"],
    ["cameras", "Camera Wall", "/map/cameras.html"],
    ["fleet", "Fleet & Maintenance", "/map/maintenance.html"],
    ["report", "Reports", "/map/report.html"],
    ["vehicle", "Vehicle Console", "/map/vehicle.html"],
  ];

  var bar = document.createElement("header");
  bar.className = "topbar";
  bar.innerHTML =
    '<div class="brand">' + SHIELD + '<div><b>ITBP Border Command</b><small id="sectorname">Surveillance and patrol management</small></div></div>' +
    '<nav class="nav">' +
    links.map(function (l) { return '<a href="' + l[2] + '"' + (l[0] === page ? ' class="on"' : "") + ">" + l[1] + "</a>"; }).join("") +
    "</nav>" +
    '<div class="tools"><span class="pill off" id="livepill"><i></i><span>CONNECTING</span></span>' +
    '<div class="clock" id="clock"></div>' +
    '<button class="iconbtn" id="themebtn" title="Switch between dark and light" aria-label="Switch between dark and light"></button></div>';
  document.body.insertBefore(bar, document.body.firstChild);

  var themeBtn = document.getElementById("themebtn");
  function paintTheme() {
    themeBtn.innerHTML = document.documentElement.getAttribute("data-theme") === "light" ? MOON : SUN;
  }
  paintTheme();
  themeBtn.onclick = function () {
    var next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
    paintTheme();
    window.dispatchEvent(new Event("themechange"));
  };

  function tickClock() {
    var d = new Date();
    document.getElementById("clock").innerHTML =
      d.toLocaleTimeString([], { hour12: false }) + "<small>" +
      d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short", year: "numeric" }) + "</small>";
  }
  tickClock();
  setInterval(tickClock, 1000);

  function tickStatus() {
    fetch("/vehicles").then(function (r) { return r.ok; }).catch(function () { return false; }).then(function (ok) {
      var p = document.getElementById("livepill");
      p.className = "pill " + (ok ? "live" : "off");
      p.lastChild.textContent = ok ? "LIVE" : "OFFLINE";
    });
  }
  tickStatus();
  setInterval(tickStatus, 4000);

  fetch("/config").then(function (r) { return r.json(); }).then(function (c) {
    document.getElementById("sectorname").textContent = c.sector + " \u00B7 simulation";
  }).catch(function () {});
})();