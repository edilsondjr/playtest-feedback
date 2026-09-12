# Playtest feedback form

Static, login-free feedback form published on GitHub Pages. Testers open the URL, fill it in and
the page posts the structured report straight to the monitored destination.

- **Public URL:** https://edilsondjr.github.io/playtest-feedback/
- **Source:** this repo (`index.html`, single self-contained file, no build step)
- **Login required:** no. No cookies, no analytics, no third-party script.

## Required fields (acceptance criteria)

| Field | Type | Required |
|---|---|---|
| What happened (problem / suggestion) | long text | yes |
| Steps to reproduce | long text | yes |
| Device / model | short text | yes |
| Game version / build | short text | yes |
| Screenshot | link (static hosting cannot accept file uploads) | no |
| Severity, name/contact | optional extras | no |

Client-side validation blocks the submit path when any required field is empty and names the
missing fields. The build number can be prefilled by the link that sends testers here:
`https://edilsondjr.github.io/playtest-feedback/?v=0.2.0`.

## Where the responses go (configured 2026-09-11)

`FEEDBACK_ENDPOINT` (top of `<script>` in `index.html`) points at a shared **ntfy.sh topic**:

```
https://ntfy.sh/playtest-feedback-453f452c55eb4e77
```

- **Write side:** the tester's browser POSTs the report there (`Content-Type: application/json`).
  The URL is write-only: no account, no API key, no mailbox, nothing secret in a public page.
  The relay allows cross-origin POST from GitHub Pages (`Access-Control-Allow-Origin: *`).
- **Read side (any team member, no install):** open <https://ntfy.sh/playtest-feedback-453f452c55eb4e77>
  in a browser, or subscribe to that topic in the ntfy app for a push notification per report.
- **Store of record:** `%USERPROFILE%\PlaytestFeedback\inbox.jsonl` — append-only JSONL, written by
  `tools/collect_feedback.py`. One line per report, full payload, dedup by relay message id.
- **Notification:** the same collector pings the team on Telegram
  (`hermes send --to telegram`) with a compact digest whenever a new report lands.
- **Known limit:** ntfy.sh keeps a topic's messages for ~12h, so the collector must run more often
  than that. That is what the scheduled task below is for.

`TEAM_EMAIL` stays empty on purpose: the official support mailbox
(`support@coalgrave.game`, `docs/canal-suporte.md` in the game repo) is still a placeholder, and
inventing an address here would send reports into a void. When that mailbox is real, swap the
endpoint to `https://formsubmit.co/ajax/<mailbox>` (the first submission sends an activation e-mail
to it — a human must click that link) or a Formspree form URL; keep the collector as the archive by
rerunning it with `--endpoint https://ntfy.sh/<new-topic>` only if you also rotate the topic.

**Never put a token, key or personal mailbox in this file: the page is public, anyone can read the
endpoint URL. A publish-only URL is the only kind that may live here.**

## The routine (check + notify)

| Piece | Where |
|---|---|
| Runs the collector | Windows scheduled task `PlaytestFeedbackWatch`, every 15 min, while the user is logged on |
| Entry point | `tools\watch_feedback.cmd` (writes `%USERPROFILE%\PlaytestFeedback\watch.log`) |
| Install / reinstall on a clean machine | `tools\install_watch_task.cmd` |
| Run it by hand | `python tools/collect_feedback.py --notify telegram` |
| What did it do lately | `type %USERPROFILE%\PlaytestFeedback\watch.log` (last lines) |
| Remove the routine | `schtasks /Delete /F /TN PlaytestFeedbackWatch` |

The collector prints nothing when there is nothing new (watchdog style), so an empty log means
"quiet", not "broken". A failure is loud: exit code 2 (destination unreachable) or 3 (archived but
notification failed) plus the reason in the log.

## Publish / republish

```sh
cd playtest-feedback
npm i                            # jsdom, first time only
node tools/test_form.mjs         # gate: 33 checks, must exit 0
git add -A && git commit -m "form: <change>" && git push
```

GitHub Pages rebuilds `main` / root automatically (1-2 min).

## Tools

`tools/test_form.mjs` — headless gate. Loads `index.html` in jsdom and checks the required fields,
the blocked empty submit, the generated report, the `?v=` prefill, **that the page really POSTs the
full payload to the configured endpoint**, and that an HTTP/network failure is shown to the tester
instead of failing silently. `fetch` is stubbed, so the gate never touches the network.

`tools/collect_feedback.py` — the destination side (stdlib only, Python 3.8+):

```sh
python tools/collect_feedback.py                 # archive new reports, silent if there are none
python tools/collect_feedback.py --notify telegram
python tools/collect_feedback.py --selftest      # end-to-end proof of the destination (POST + read back)
python tools/collect_feedback.py --dump          # what is in the archive
```

It reads the endpoint from `index.html`, so the page and the collector can never disagree.
Exit codes: 0 ok, 1 selftest failed, 2 destination unreachable, 3 archived but notification failed.

`tools/e2e_submit.mjs` — end-to-end validation driver (Node 22+, no dependency). Opens the **live**
page in a real Chrome over CDP, fills the required fields the way a tester does (including the page's
own "detect device" button), publishes a screenshot so the report carries a real link, submits, waits
for the green confirmation and saves before/filled/sent screenshots. Prints a JSON report and exits 0
only when the page confirmed the send.

```sh
# one terminal: real Chrome with a debugging port and a throwaway profile
"/c/Program Files/Google/Chrome/Application/chrome.exe" --remote-debugging-port=9333 \
  --user-data-dir="C:/Users/<you>/AppData/Local/Temp/cdp-e2e" about:blank &
# another: drive it
node tools/e2e_submit.mjs 9333 "https://edilsondjr.github.io/playtest-feedback/?v=v0.2.0" ./out e2e
```

A green `Sent.` from the page only means the relay accepted the POST. Finish the loop by hand:

```sh
schtasks /Run /TN PlaytestFeedbackWatch    # or wait for the 15-min tick
type %USERPROFILE%\PlaytestFeedback\watch.log    # the new report must appear in the digest
```

## Last end-to-end validation

2026-09-12 — report `jBuf3VjTvpca` submitted from the live page in a real Chrome (all 8 fields
intact, `build v0.2.0`, device auto-detected, screenshot link present), delivered to
`%USERPROFILE%\PlaytestFeedback\inbox.jsonl` and to the Telegram digest with no failure line in
`watch.log`. The same run re-verified the build side: the exported `coalgrave_playtest_v0.2.0_win64`
pressed its own "SEND PLAYTEST FEEDBACK" button, which opened the live form in the default browser
(`FBL_PROBE=PASS`, `FBL_HTTP 200 / 15501 bytes` fetched from inside the game).

## Verify it is actually live (run from any machine, no auth)

```sh
curl -sS -o /dev/null -w '%{http_code}\n' https://edilsondjr.github.io/playtest-feedback/   # expect 200
curl -sS https://edilsondjr.github.io/playtest-feedback/ | grep -c 'required'                # expect >= 4
curl -sS https://edilsondjr.github.io/playtest-feedback/ | grep -o 'ntfy.sh/[a-z0-9-]*'      # expect the topic
python tools/collect_feedback.py --selftest                                                  # expect PASS PASS
```

## What to do when it breaks

- **404 on the URL:** Pages disabled or the branch/path changed — check
  `gh api repos/edilsondjr/playtest-feedback/pages`; re-enable with
  `gh api -X POST repos/edilsondjr/playtest-feedback/pages -f 'source[branch]=main' -f 'source[path]=/'`.
- **Old content still served:** Pages/CDN cache; check the last run with
  `gh api repos/edilsondjr/playtest-feedback/pages/builds/latest` and wait for `built`.
- **Tester sees "Send failed (HTTP xxx)":** the destination answered with an error — verify with
  `python tools/collect_feedback.py --selftest`; if the topic was reserved/rotated, update
  `FEEDBACK_ENDPOINT` and push. The tester is never stuck: the copy/download fallback is always
  shown, and the report stays on screen.
- **Reports reach ntfy but no Telegram ping:** look at `%USERPROFILE%\PlaytestFeedback\watch.log`;
  exit 3 means the collector archived the report but `hermes send --to telegram` failed — run it by
  hand, the data is already in the JSONL archive.
- **Machine offline for more than ~12h:** ntfy drops the cached messages. Whatever the testers sent
  in that window is not in the archive; ask in the playtest chat. Keep the machine on during a
  feedback wave, or move to the mailbox endpoint above.
