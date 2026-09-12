#!/usr/bin/env python3
"""Collect playtest feedback published to the shared ntfy topic.

Where things live
  index.html            -> FEEDBACK_ENDPOINT is the single source of truth for the
                           destination URL. This script reads it from there, so the
                           page and the collector can never disagree.
  ~/PlaytestFeedback/inbox.jsonl
                        -> store of record (append-only, one JSON object per line).

ntfy.sh keeps a topic's messages for ~12h, so the poller must run more often than
that; the JSONL archive is what makes the data durable. Nothing here needs an
account, a key or a mailbox - the publish URL is write-only.

Usage
  python tools/collect_feedback.py                  # archive new reports, silent if none
  python tools/collect_feedback.py --notify telegram
  python tools/collect_feedback.py --selftest       # real end-to-end check of the destination
  python tools/collect_feedback.py --dump           # print everything in the archive

Exit codes
  0  ok (including "nothing new")
  1  --selftest failed
  2  network / protocol error while reading the destination
  3  reports were archived but the notification could not be sent
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.normpath(os.path.join(HERE, os.pardir, "index.html"))
DEFAULT_ARCHIVE = os.path.join(os.path.expanduser("~"), "PlaytestFeedback", "inbox.jsonl")
HERMES_CANDIDATES = [
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "hermes-agent",
                 "venv", "Scripts", "hermes.exe"),
]
HTTP_TIMEOUT = 30
MSG_LIMIT = 3400  # keep the notification under Telegram's 4096-char cap

# The exact payload the page posts (index.html -> submit handler). Keep in sync.
PAGE_FIELDS = ["what_happened", "repro_steps", "device", "build",
               "screenshot_url", "severity", "contact", "report_markdown"]

# What the tester actually wrote. report_markdown is excluded on purpose: it is a
# rendering of these same fields plus the moment of submission ("Sent at:"), so
# two POSTs of the same report from the same page differ only in that line.
CONTENT_FIELDS = [f for f in PAGE_FIELDS if f != "report_markdown"]


def log(msg):
    print(msg, flush=True)


def endpoint_from_index(path=INDEX_HTML):
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    m = re.search(r'var\s+FEEDBACK_ENDPOINT\s*=\s*"([^"]*)"', html)
    if not m or not m.group(1).strip():
        sys.exit("FEEDBACK_ENDPOINT is empty in %s" % path)
    return m.group(1).strip().rstrip("/")


def http(method, url, payload=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def fetch_messages(endpoint):
    """Every message the destination still caches, oldest first."""
    _, body = http("GET", endpoint + "/json?poll=1&since=all")
    out = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("event") == "message":
            out.append(rec)
    out.sort(key=lambda r: r.get("time", 0))
    return out


def load_archive(path):
    """(records, set_of_seen_ids). A missing archive is an empty archive."""
    records, ids = [], set()
    if not os.path.exists(path):
        return records, ids
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # torn last line from a killed process - ignore it
            records.append(rec)
            if rec.get("id"):
                ids.add(rec["id"])
    return records, ids


def as_payload(rec):
    if isinstance(rec.get("payload"), dict):  # archive shape
        return rec["payload"]
    raw = rec.get("message", "")
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    return {"what_happened": raw}


def content_key(payload):
    """Fingerprint of the report's content, order-independent.

    Whitespace is collapsed so a re-submit with a stray newline is still the
    same report. Used *in addition to* the relay id: a duplicate POST gets a
    fresh id every time, so ids alone can never catch it.
    """
    norm = {k: " ".join(str(payload.get(k) or "").split()) for k in CONTENT_FIELDS}
    blob = json.dumps(norm, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def one_line(value, width=400):
    text = " ".join(str(value or "").split())
    return text[:width] + ("..." if len(text) > width else "")


def digest(new):
    lines = ["New playtest feedback: %d" % len(new)]
    for i, rec in enumerate(new, 1):
        p = as_payload(rec)
        when = datetime.fromtimestamp(rec.get("time", 0), timezone.utc).astimezone()
        lines += [
            "",
            "%d) %s  [%s]  build %s  |  %s" % (
                i, when.strftime("%Y-%m-%d %H:%M"), p.get("severity") or "-",
                p.get("build") or "-", one_line(p.get("device"), 80)),
            "   what: %s" % one_line(p.get("what_happened")),
            "   steps: %s" % one_line(p.get("repro_steps")),
        ]
        if p.get("screenshot_url"):
            lines.append("   shot: %s" % one_line(p.get("screenshot_url"), 200))
        if p.get("contact"):
            lines.append("   contact: %s" % one_line(p.get("contact"), 120))
        lines.append("   id: %s" % rec.get("id", "?"))
    text = "\n".join(lines)
    if len(text) > MSG_LIMIT:
        text = text[:MSG_LIMIT] + "\n... (truncated - full text in the archive)"
    return text


def hermes_send(target, text, hermes_path=None):
    exe = hermes_path or shutil.which("hermes")
    if not exe:
        exe = next((p for p in HERMES_CANDIDATES if os.path.exists(p)), None)
    if not exe:
        raise RuntimeError("hermes CLI not found - pass --hermes /path/to/hermes")
    proc = subprocess.run([exe, "send", "--to", target, "-q", text],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("hermes send --to %s failed (exit %d): %s"
                           % (target, proc.returncode,
                              (proc.stderr or proc.stdout or "").strip()[:300]))


def cmd_selftest(endpoint):
    """Post the exact payload the page posts, then read it back from the topic."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "what_happened": "[SELFTEST] destination check from collect_feedback.py at " + stamp,
        "repro_steps": "1. run python tools/collect_feedback.py --selftest",
        "device": "ops CLI (destination selftest)",
        "build": "selftest",
        "screenshot_url": "",
        "severity": "minor",
        "contact": "",
        "report_markdown": "# SELFTEST\n\nDestination round-trip check at %s\n" % stamp,
    }
    ok = True
    try:
        status, body = http("POST", endpoint, payload)
        accepted = 200 <= status < 300
    except urllib.error.HTTPError as err:  # still a real answer from the destination
        status, body, accepted = err.code, err.read().decode("utf-8", "replace"), False
    log("%s  POST %s -> HTTP %s" % ("PASS" if accepted else "FAIL", endpoint, status))
    ok = ok and accepted
    if not accepted:
        return ok
    try:
        record = json.loads(body)
        posted_id = record.get("id")
    except ValueError:
        log("FAIL  destination answered with something that is not JSON: %r" % body[:120])
        return False
    # The relay commits the message a moment after answering the POST; poll for it.
    found = None
    for attempt in range(6):
        try:
            messages = fetch_messages(endpoint)
        except Exception as err:  # noqa: BLE001 - report whatever went wrong
            log("FAIL  could not read the destination back: %s" % err)
            return False
        found = next((m for m in messages if m.get("id") == posted_id), None)
        if found:
            break
        time.sleep(2)
    if not found:
        log("FAIL  posted message %s did not come back from the topic after 12s" % posted_id)
        return False
    back = as_payload(found)
    same = all(back.get(k) == payload[k] for k in PAGE_FIELDS)
    log("%s  read back message %s (all %d fields intact: %s)"
        % ("PASS" if same else "FAIL", posted_id, len(PAGE_FIELDS), "yes" if same else "no"))
    return ok and same


def cmd_dump(archive):
    records, _ = load_archive(archive)
    for rec in records:
        p = as_payload(rec)
        when = datetime.fromtimestamp(rec.get("time", 0), timezone.utc).astimezone()
        log("%s  [%s]  %s" % (when.strftime("%Y-%m-%d %H:%M"), p.get("severity") or "-",
                              one_line(p.get("what_happened"), 120)))
    log("%d report(s) in %s" % (len(records), archive))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Archive and announce playtest feedback.")
    ap.add_argument("--endpoint", help="destination URL (default: FEEDBACK_ENDPOINT in index.html)")
    ap.add_argument("--archive", default=DEFAULT_ARCHIVE, help="JSONL store (default: %(default)s)")
    ap.add_argument("--notify", metavar="TARGET",
                    help="send the digest with 'hermes send --to TARGET' (e.g. telegram)")
    ap.add_argument("--hermes", help="path to the hermes CLI (for --notify)")
    ap.add_argument("--selftest", action="store_true", help="post a probe and read it back")
    ap.add_argument("--dump", action="store_true", help="print the archive and exit")
    args = ap.parse_args(argv)

    if args.dump:
        cmd_dump(args.archive)
        return 0

    try:
        endpoint = args.endpoint or endpoint_from_index()
    except OSError as err:
        sys.exit("cannot read index.html: %s" % err)

    if args.selftest:
        return 0 if cmd_selftest(endpoint) else 1

    try:
        messages = fetch_messages(endpoint)
    except Exception as err:  # noqa: BLE001 - network, DNS, HTTP status, bad JSON
        sys.exit("destination unreachable (%s): %s" % (endpoint, err))

    records, seen = load_archive(args.archive)
    seen_content = set(content_key(as_payload(r)) for r in records)
    new, dupes = [], []
    for m in messages:
        if not m.get("id") or m["id"] in seen:
            continue  # already archived under this relay id
        key = content_key(as_payload(m))
        if key in seen_content:
            dupes.append(m)
            continue  # same report, new relay id: a repeat of something archived
        seen_content.add(key)  # also collapses duplicates inside this batch
        new.append(m)
    if dupes:
        log("skipped %d duplicate report(s) already archived: %s"
            % (len(dupes), ", ".join(str(d.get("id")) for d in dupes)))
    if not new:
        return 0  # silent: watchdog-style, the team hears nothing when there is nothing

    os.makedirs(os.path.dirname(args.archive), exist_ok=True)
    with open(args.archive, "a", encoding="utf-8") as fh:
        for rec in new:
            fh.write(json.dumps({
                "id": rec.get("id"),
                "time": rec.get("time"),
                "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "topic": rec.get("topic"),
                "payload": as_payload(rec),
            }, ensure_ascii=False) + "\n")

    text = digest(new)
    log(text)
    if args.notify:
        try:
            hermes_send(args.notify, text, args.hermes)
        except RuntimeError as err:
            log("archived %d report(s) but notification failed: %s" % (len(new), err))
            return 3
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):  # Windows console default is cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
