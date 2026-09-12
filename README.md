# Playtest feedback form

Static, login-free feedback form published on GitHub Pages. Testers open the URL, fill it in and
send the generated report to the team through the playtest chat.

- **Public URL:** https://edilsondjr.github.io/playtest-feedback/
- **Source:** this repo (`index.html`, single self-contained file, no build step, no dependencies)
- **Login required:** no. No cookies, no analytics, no external requests while `FEEDBACK_ENDPOINT`
  is empty. Safe to open in an anonymous/private browser window.

## Required fields (acceptance criteria)

| Field | Type | Required |
|---|---|---|
| What happened (problem / suggestion) | long text | yes |
| Steps to reproduce | long text | yes |
| Device / model | short text | yes |
| Game version / build | short text | yes |
| Screenshot | link (static hosting cannot accept file uploads) | no |
| Severity, name/contact | optional extras | no |

Client-side validation blocks the submit button path when any required field is empty and names the
missing fields. The build number can be prefilled by the link that sends testers here:
`https://edilsondjr.github.io/playtest-feedback/?v=0.2.0`.

## Why not Google Forms

Google Forms needs an interactive Google OAuth session in a browser; the automated browser on the
build host cannot start (local Chromium launch failure) and the Drive/Forms API credentials are not
available. This static form is the "equivalent" allowed by the card. Migrating to Google Forms later
is a content copy: same five required fields.

## How responses reach the team (two knobs, top of `<script>` in index.html)

1. `FEEDBACK_ENDPOINT` — optional JSON POST endpoint. Set it to a no-code form relay, e.g.
   `https://formsubmit.co/ajax/<team-address>` (first submission sends an activation e-mail to that
   address), a Formspree form URL, a Google Apps Script web app, or a Supabase REST insert. When set,
   the page submits automatically and shows the real result.
2. `TEAM_EMAIL` — address used by the "Open in e-mail" button (mailto with the full report).

With both empty (current state) the page still works offline: the tester gets a structured Markdown
report with **Copy**, **Download .md** and the instruction to send it through the playtest chat
(Discord / WhatsApp) — the same channel used for recruitment.

**Never put a token or API secret in this file: the page is public.**

## Publish / republish

```sh
cd playtest-feedback
node tools/test_form.mjs        # gate: 20 checks, must exit 0
git add -A && git commit -m "form: <change>" && git push
```

GitHub Pages rebuilds `main` / root automatically (1-2 min).

`tools/test_form.mjs` loads `index.html` in jsdom (`npm i` installs it) and exercises the required-field
validation, the report generation and the `?v=` prefill, and fails on any uncaught page error. Run it
before every push: a broken `index.html` is otherwise only visible to a tester mid-playtest.

## Verify it is actually live (run from any machine, no auth)

```sh
curl -sS -o /dev/null -w '%{http_code}\n' https://edilsondjr.github.io/playtest-feedback/     # expect 200
curl -sS https://edilsondjr.github.io/playtest-feedback/ | grep -c 'required'                  # expect >= 4
```

## What to do when it breaks

- **404 on the URL:** Pages disabled or the branch/path changed — check
  `gh api repos/edilsondjr/playtest-feedback/pages`; re-enable with
  `gh api -X POST repos/edilsondjr/playtest-feedback/pages -f 'source[branch]=main' -f 'source[path]=/'`.
- **Old content still served:** Pages/CDN cache; check the last run with
  `gh api repos/edilsondjr/playtest-feedback/pages/builds/latest` and wait for `built`.
- **Reports not arriving:** the page never silently drops data — if `FEEDBACK_ENDPOINT` is empty the
  tester always gets the copy/download buttons. A relay that stops working surfaces as the red
  "Send failed (HTTP xxx)" message plus the copy/download fallback.
