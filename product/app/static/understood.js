// Say each answer back as it is typed, so a form feels like it is listening.
//
// Any input with data-echo="<kind>" gets a line under it: "✓ £12.50 an hour
// or more", "✓ Jobs within 25 miles of Leeds". Nothing here decides
// anything - the server checks every answer again with the same rules - so
// with JavaScript off the forms work exactly the same, just quieter.
//
// The pay wording mirrors app/understood.py's pay_words, so what a person
// sees while typing is what the next screen says back.
(function () {
  var HOURLY_BELOW = 200;

  function payValue(raw) {
    var t = (raw || "").toLowerCase().replace(/£/g, "").replace(/,/g, "").trim();
    ["per hour", "an hour", "/hr", "/h", "p/h", "ph", "per year", "a year",
     "p.a.", "pa"].forEach(function (s) { t = t.split(s).join("").trim(); });
    var k = /k$/.test(t);
    t = t.replace(/k+$/, "").trim();
    if (!/^\d+(\.\d+)?$/.test(t)) return 0;
    var v = parseFloat(t) * (k ? 1000 : 1);
    return v > 0 ? v : 0;
  }

  function payWords(raw) {
    var v = payValue(raw);
    if (!v) return "";
    if (v < HOURLY_BELOW) {
      return "£" + v.toFixed(2).replace(/\.00$/, "") + " an hour or more";
    }
    return "£" + Math.round(v).toLocaleString("en-GB") + " a year or more";
  }

  function val(form, name) {
    var el = form && form.querySelector('[name="' + name + '"]');
    return el ? el.value.trim() : "";
  }

  // [text, ok]. ok false is a gentle nudge, never an error: the server is
  // the one that refuses things.
  var SAY = {
    role: function (v) {
      return v.length < 3 ? null : ["Searching for: " + v, true];
    },
    place: function (v) {
      return v.length < 2 ? null : ["Jobs within 25 miles of " + v, true];
    },
    title: function (v, form) {
      var org = val(form, "last_org");
      if (v.length < 2) return null;
      return org ? ["Proof you've worked: " + v + " at " + org, true]
                 : ["Got it. Now who it was with", true];
    },
    org: function (v, form) {
      var title = val(form, "last_title");
      if (v.length < 2) return null;
      return title ? ["Proof you've worked: " + title + " at " + v, true]
                   : ["Got it", true];
    },
    pay: function (v) {
      if (!v) return null;
      var words = payWords(v);
      return words ? [words, true]
                   : ["Just the number, like 24000 or 12.50", false];
    },
    // The two separate pay boxes on the longer forms, where the box itself
    // says which it is.
    "pay-year": function (v) {
      var n = parseFloat(v);
      return n > 0 ? ["£" + Math.round(n).toLocaleString("en-GB") +
                      " a year or more", true] : null;
    },
    "pay-hour": function (v) {
      var n = parseFloat(v);
      return n > 0 ? ["£" + n.toFixed(2).replace(/\.00$/, "") +
                      " an hour or more", true] : null;
    },
    name: function (v) {
      return v.length < 2 ? null : ["Every letter is signed " + v, true];
    },
    phone: function (v) {
      var digits = v.replace(/\D/g, "").length;
      if (!digits) return null;
      return digits >= 10 ? ["Employers can ring you on this", true]
                          : ["Keep going…", false];
    },
    email: function (v) {
      if (!v) return null;
      return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)
        ? ["Your link goes here", true]
        : ["Needs to look like you@example.com", false];
    }
  };

  function lineFor(input) {
    var line = input.parentNode.querySelector('.echo[data-for="' + input.name + '"]');
    if (!line) {
      line = document.createElement("small");
      line.className = "echo";
      line.setAttribute("data-for", input.name);
      line.setAttribute("aria-live", "polite");
      input.insertAdjacentElement("afterend", line);
    }
    return line;
  }

  function update(input) {
    var say = SAY[input.getAttribute("data-echo")];
    if (!say) return;
    var out = say(input.value.trim(), input.form);
    var line = lineFor(input);
    if (!out) { line.textContent = ""; line.className = "echo"; return; }
    line.textContent = (out[1] ? "✓ " : "") + out[0];
    line.className = out[1] ? "echo" : "echo wait";
  }

  function all() {
    var inputs = document.querySelectorAll("[data-echo]");
    for (var i = 0; i < inputs.length; i++) {
      (function (input) {
        input.addEventListener("input", function () {
          update(input);
          // The two halves of "your last job" read as one line.
          var kind = input.getAttribute("data-echo");
          if ((kind === "title" || kind === "org") && input.form) {
            var other = input.form.querySelector(
              '[data-echo="' + (kind === "title" ? "org" : "title") + '"]');
            if (other && other.value.trim()) update(other);
          }
        });
        if (input.value.trim()) update(input);
      })(inputs[i]);
    }

    // A button that says what is happening once pressed, rather than a
    // screen that looks frozen while a model reads a sentence.
    var forms = document.querySelectorAll("form[data-busy]");
    for (var j = 0; j < forms.length; j++) {
      (function (form) {
        form.addEventListener("submit", function (ev) {
          var btn = ev.submitter || form.querySelector("button.btn, button[type=submit]");
          if (!btn || btn.name === "action") return;
          btn.disabled = true;
          btn.textContent = form.getAttribute("data-busy").replace("&hellip;", "…");
        });
      })(forms[j]);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", all);
  } else {
    all();
  }
})();
