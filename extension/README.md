# Recruited: who's hiring? (Chrome extension)

On a UK job advert, it adds one green button: **"Who's hiring at <employer>?
Get their email"**. Tapping it opens `recruited.org.uk/find` with the
employer's name filled in. The person presses *Find*, and sees the address the
employer publishes, or a straight "there isn't one".

It meets people at the exact moment they were about to click Apply, which is
the moment the whole product is built around.

## What it does and does not do

- **Reads one thing:** the employer's name. It gets it from the advert's
  schema.org `JobPosting` markup, which boards publish for Google for Jobs,
  and falls back to the board's own employer element.
- **Sends nothing.** Until the button is tapped nothing leaves the page. Then
  only the employer's name travels, in the address of the tab it opens.
- **Runs only on the job boards in `manifest.json`.** No other site, and no
  permissions beyond those pages. No remote code, no analytics.
- Visits it brings are counted on `/admin` under the campaign name
  `extension` (`?c=extension`), like any other link.

## Publishing it (Harry: about 20 minutes, once)

1. Make the zip: `cd extension && ./build.sh`. That writes `recruited-extension.zip`.
2. Register as a Chrome Web Store developer with your Google account at
   <https://chrome.google.com/webstore/devconsole>. Google charges a one-off
   US$5 (about £4).
3. **New item**, then upload the zip.
4. Store listing:
   - **Name:** Recruited: who's hiring?
   - **Summary:** On a UK job advert, one tap finds the email address the
     employer publishes, so you write to a person, not a portal.
   - **Description:**

     > Clicking Apply puts your CV in a pile a computer sorts. Emailing the
     > person hiring gets it read by a human, but finding their address is
     > the hard part.
     >
     > Open any job advert on Indeed, Reed, Totaljobs, LinkedIn, CV-Library,
     > Adzuna, s1jobs or Find a job. A green button appears with the
     > employer's name on it. Tap it, and Recruited reads the advert and the
     > employer's own website for the address they publish. It never guesses
     > one, and it tells you straight when there isn't one.
     >
     > Free, no account needed. Nothing is collected and nothing is sent
     > anywhere until you tap the button.
     >
     > Want the emails written and sent for you, from your own email?
     > That's recruited.org.uk.

   - **Category:** Productivity (or Tools). **Language:** English (UK).
   - **Screenshots (1280×800):** a job advert with the green button, and
     `/find` showing a result.
5. **Privacy practices:** single purpose, as above. Data collected: *none*.
   Permissions: content scripts on the listed job boards only, to read the
   employer's name. Privacy policy URL: `https://recruited.org.uk/privacy`.
6. Submit for review. Reviews usually take a few days.
