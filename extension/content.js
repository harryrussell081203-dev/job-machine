// Recruited: who's hiring?
//
// On a job advert, add one button. Tapping it opens recruited.org.uk/find
// with the employer's name filled in; the person presses "Find" there.
//
// What this does NOT do, on purpose:
//   - send anything anywhere. Nothing leaves the page until the button is
//     tapped, and then only the employer's name, in the address of the tab
//     it opens.
//   - read anything but the employer's name.
//   - run on any site but the job boards listed in manifest.json.
(function () {
  "use strict";

  var SITE = "https://recruited.org.uk";
  var BUTTON_ID = "recruited-whos-hiring";

  function clean(text) {
    return (text || "").replace(/\s+/g, " ").trim().slice(0, 160);
  }

  // Most job boards describe the advert in schema.org JobPosting markup, for
  // Google for Jobs. It names the employer more reliably than any page
  // layout, and it does not move when a board redesigns.
  function fromStructuredData() {
    var scripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (var i = 0; i < scripts.length; i++) {
      var data;
      try { data = JSON.parse(scripts[i].textContent); } catch (e) { continue; }
      var items = [].concat(data && data["@graph"] ? data["@graph"] : data);
      for (var j = 0; j < items.length; j++) {
        var item = items[j];
        if (!item || typeof item !== "object") continue;
        var type = [].concat(item["@type"] || []).join(" ");
        if (type.indexOf("JobPosting") < 0) continue;
        var org = item.hiringOrganization;
        var name = typeof org === "string" ? org : org && org.name;
        if (clean(name)) return clean(name);
      }
    }
    return "";
  }

  // Where structured data is missing, the boards' own employer element.
  var SELECTORS = [
    '[data-testid="inlineHeader-companyName"]',          // Indeed
    '[data-company-name="true"]',                         // Indeed
    '.jobsearch-InlineCompanyRating div:first-child',     // Indeed, older
    '[data-qa="recruiterName"]',                          // Reed
    '[data-at="metadata-company-name"]',                  // Totaljobs, Jobsite
    '.job-details-jobs-unified-top-card__company-name',   // LinkedIn
    '.jobs-unified-top-card__company-name',               // LinkedIn, older
    '.topcard__org-name-link',                            // LinkedIn, logged out
    '.job__company',                                      // CV-Library
    '[data-test="employer-name"]',                        // Glassdoor
    '.govuk-body .govuk-\\!-font-weight-bold'             // Find a job
  ];

  function fromPage() {
    for (var i = 0; i < SELECTORS.length; i++) {
      var el;
      try { el = document.querySelector(SELECTORS[i]); } catch (e) { continue; }
      if (el && clean(el.textContent)) return clean(el.textContent);
    }
    return "";
  }

  function employer() {
    return fromStructuredData() || fromPage();
  }

  function link(name) {
    var url = SITE + "/find?c=extension";
    if (name) url += "&company=" + encodeURIComponent(name);
    return url;
  }

  function place() {
    var name = employer();
    var button = document.getElementById(BUTTON_ID);
    if (!button) {
      button = document.createElement("a");
      button.id = BUTTON_ID;
      button.target = "_blank";
      button.rel = "noopener";
      document.body.appendChild(button);
    }
    button.href = link(name);
    button.textContent = name
      ? "Who's hiring at " + (name.length > 28 ? name.slice(0, 27) + "…" : name) + "? Get their email"
      : "Who's hiring? Get their email";
    button.title = "Opens Recruited with the employer filled in. Finds the address they publish; never a guessed one.";
  }

  // LinkedIn and Indeed change the advert without loading a new page, so
  // the employer is read again whenever the address changes.
  var last = "";
  function check() {
    if (location.href !== last) {
      last = location.href;
      setTimeout(place, 600);
    }
  }
  place();
  setInterval(check, 1000);
})();
