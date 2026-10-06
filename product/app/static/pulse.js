// Anonymous counts from the landing and sign-up pages: how far down people
// get, which button they tap, which step they leave on. Each is a fixed name
// added to a tally (see app/pulse.py); nothing identifies the reader, and a
// browser that blocks this changes nothing about how the page works.
(function () {
  var sent = {};
  function pulse(name) {
    if (sent[name]) return;
    sent[name] = true;
    var body = JSON.stringify({ e: name });
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon("/pulse", new Blob([body], { type: "application/json" }));
      } else {
        fetch("/pulse", { method: "POST", body: body, keepalive: true,
                          headers: { "Content-Type": "application/json" } });
      }
    } catch (e) {}
  }
  window.rcPulse = pulse;

  var page = document.body.getAttribute("data-pulse-page") || "";

  // Buttons and links marked data-pulse="land:cta:hero" and so on.
  document.addEventListener("click", function (ev) {
    var el = ev.target.closest && ev.target.closest("[data-pulse]");
    if (el) pulse(el.getAttribute("data-pulse"));
  }, true);

  if (page === "landing") {
    var marks = [25, 50, 75, 100];
    var onScroll = function () {
      var doc = document.documentElement;
      var seen = (window.scrollY + window.innerHeight) / Math.max(1, doc.scrollHeight);
      marks.forEach(function (m) {
        if (seen * 100 >= m - 2) pulse("land:scroll:" + m);
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    var finder = document.querySelector("form[action='/find']");
    if (finder) finder.addEventListener("focusin", function () { pulse("land:try"); });
  }

  if (page === "start-describe" || page === "start-check") {
    var submitted = false;
    document.querySelectorAll("form").forEach(function (f) {
      f.addEventListener("submit", function () {
        submitted = true;
        if (f.getAttribute("action") === "/start/read") pulse("start:next");
      });
    });
    var about = document.getElementById("about");
    if (about) about.addEventListener("input", function () { pulse("start:typing"); });
    // Leaving without sending it: on a phone this is the tab being switched
    // or closed, which is exactly when pagehide fires.
    window.addEventListener("pagehide", function () {
      if (submitted) return;
      if (page === "start-check") return pulse("start:left:check");
      pulse(about && about.value.trim() ? "start:left:describe-typed"
                                        : "start:left:describe-empty");
    });
  }
})();
