#!/usr/bin/env python3
"""Gate for the collector's duplicate handling. No network, no credentials.

    python tools/test_collector.py

Runs tools/collect_feedback.py against a LOCAL stand-in for the relay (an
in-process HTTP server that serves the same JSONL the ntfy topic serves) and a
throwaway archive, so the cases below are deterministic. The stand-in is a test
double: nothing about the real destination changes.

The fixture is the real defect, copied from the live archive on 2026-09-12: the
same report reaches the topic twice with two different relay ids, the two
payloads differing only in report_markdown's "Sent at:" line.

Exit 0 = every case behaved as specified.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
COLLECTOR = os.path.join(HERE, "collect_feedback.py")
TOPIC = "playtest-feedback-testtopic"

results = []


def make_notify_stub(tmp):
    """A --hermes stand-in: records that it was called, then fails (exit 7).

    The collector turns a non-zero 'hermes send' into exit 3, so the marker file
    is what proves whether the notification path was entered at all.
    """
    mark = os.path.join(tmp, "notify_called.txt")
    if os.name == "nt":
        path = os.path.join(tmp, "fake_hermes.cmd")
        body = '@echo off\r\necho called > "%s"\r\nexit /b 7\r\n' % mark
    else:
        path = os.path.join(tmp, "fake_hermes.sh")
        body = '#!/bin/sh\necho called > "%s"\nexit 7\n' % mark
        os.chmod(path, 0o755)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path, mark


def check(name, ok, detail=""):
    results.append(ok)
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, "  -- " + detail if detail else ""))


def payload(what, sent_at):
    return {
        "what_happened": what,
        "repro_steps": "1. Launch the build\n2. Open OPTIONS\n3. Press SEND PLAYTEST FEEDBACK",
        "device": "Windows 10/11 / 16 CPU threads / ~32 GB RAM / Win32 (auto-detected)",
        "build": "v0.2.0",
        "screenshot_url": "https://files.catbox.moe/2sbuhj.png",
        "severity": "Suggestion / Sugestao",
        "contact": "ops - automated end-to-end validation",
        "report_markdown": ("# Playtest feedback\n\n- **Build:** v0.2.0\n"
                            "- **Sent at:** " + sent_at + "\n\n## What happened\n\n" + what + "\n"),
    }


REPORT = "[E2E e2e-v020] live-page validation - sent from the live page by a real browser."
OTHER = "[OPS] a different report that must survive the dedupe."

# What the relay still caches. m1/m2 are the duplicate pair (different ids, same
# content, different "Sent at"); m3 is an unrelated report.
MESSAGES = [
    {"id": "r1aaaa", "time": 1789184224, "event": "message", "topic": TOPIC,
     "message": json.dumps(payload(REPORT, "2026-09-12T03:37:03.907Z"))},
    {"id": "r2bbbb", "time": 1789184260, "event": "message", "topic": TOPIC,
     "message": json.dumps(payload(REPORT, "2026-09-12T03:37:39.933Z"))},
    {"id": "r3cccc", "time": 1789185000, "event": "message", "topic": TOPIC,
     "message": json.dumps(payload(OTHER, "2026-09-12T03:50:00.000Z"))},
]


class Relay(BaseHTTPRequestHandler):
    """Serves GET /json?poll=1&since=all exactly like the topic does."""

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        if not self.path.startswith("/json"):
            self.send_error(404)
            return
        body = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in MESSAGES)
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # keep the gate output clean
        pass


def archived(msg):
    """The shape the collector writes into the archive."""
    return {"id": msg["id"], "time": msg["time"],
            "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "topic": msg.get("topic"), "payload": json.loads(msg["message"])}


def seed(path, msgs):
    with open(path, "w", encoding="utf-8") as fh:
        for m in msgs:
            fh.write(json.dumps(archived(m), ensure_ascii=False) + "\n")


def lines(path):
    with open(path, encoding="utf-8") as fh:
        return [ln for ln in fh.read().splitlines() if ln.strip()]


def run_collector(archive, endpoint, hermes):
    """Run the collector the way the scheduled task does, notify included."""
    cmd = [sys.executable, COLLECTOR, "--archive", archive, "--endpoint", endpoint,
           "--notify", "telegram", "--hermes", hermes]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main():
    srv = HTTPServer(("127.0.0.1", 0), Relay)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    endpoint = "http://127.0.0.1:%d" % srv.server_address[1]
    print("test relay on %s serving %d message(s)" % (endpoint, len(MESSAGES)))

    tmp = tempfile.mkdtemp(prefix="collector-gate-")
    hermes, notify_mark = make_notify_stub(tmp)

    def notified():
        if os.path.exists(notify_mark):
            os.remove(notify_mark)
            return True
        return False

    try:
        # 1. The duplicate pair, one of them already archived: nothing new, no digest.
        archive = os.path.join(tmp, "dup.jsonl")
        seed(archive, [MESSAGES[0], MESSAGES[2]])   # r1 and r3 archived, r2 (same report as r1) not
        before = len(lines(archive))
        code, out = run_collector(archive, endpoint, hermes)
        check("duplicate content is not archived again",
              len(lines(archive)) == before, "%d -> %d line(s)" % (before, len(lines(archive))))
        check("duplicate content does not emit a digest",
              "New playtest feedback" not in out, out.strip()[:120] or "(no output)")
        check("duplicate content exits 0 (silent run)", code == 0, "exit=%d" % code)
        check("the skipped duplicate is named in the log", "r2bbbb" in out, out.strip()[:120])
        check("a duplicate never reaches the notification step", not notified())

        # 2. The pair arrives with nothing archived: one record, one digest entry.
        archive = os.path.join(tmp, "batch.jsonl")
        seed(archive, [MESSAGES[2]])                # only r3 archived
        code, out = run_collector(archive, endpoint, hermes)
        check("two identical messages in one batch archive as one record",
              len(lines(archive)) == 2, "%d line(s)" % len(lines(archive)))
        check("the digest counts the duplicate once",
              "New playtest feedback: 1" in out, out.strip()[:120])
        check("the second copy of the pair is the one dropped",
              "r2bbbb" in out and "skipped 1 duplicate" in out, out.strip()[:160])
        notified()

        # 3. Control: with something genuinely new, the digest + notify path runs.
        archive = os.path.join(tmp, "fresh.jsonl")
        open(archive, "w").close()                  # empty archive
        code, out = run_collector(archive, endpoint, hermes)
        check("control: genuinely new reports are archived",
              len(lines(archive)) == 2, "%d line(s)" % len(lines(archive)))
        check("control: the digest covers both new reports",
              "New playtest feedback: 2" in out, out.strip()[:120])
        check("control: the notify step is really reached (stub fails -> exit 3)",
              code == 3 and notified() and "notification failed" in out, "exit=%d" % code)

        # 4. Same relay id, same content (a re-poll): still a no-op.
        archive = os.path.join(tmp, "repoll.jsonl")
        seed(archive, MESSAGES)
        before = len(lines(archive))
        code, out = run_collector(archive, endpoint, hermes)
        notified()
        check("re-polling an already archived topic adds nothing",
              len(lines(archive)) == before and code == 0, "exit=%d" % code)
    finally:
        srv.shutdown()

    failed = results.count(False)
    print("\n%d/%d checks passed" % (len(results) - failed, len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
