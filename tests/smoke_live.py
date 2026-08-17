# -*- coding: utf-8 -*-
"""End-to-end smoke test against the DEPLOYED site, with synthetic data.

    python tests/smoke_live.py https://lead-scoring-ten.vercel.app

Why synthetic and not a real room: a Zoom attendee CSV carries real names, emails
and phone numbers, and a smoke test does not need any of them. These six people are
invented, and they exercise every branch that could plausibly break in a serverless
function rather than on a laptop:

  routing        vercel.json rewrites every path to api/index.py
  function boot  fastapi + xlsxwriter actually import in the lambda
  /tmp           tempfile.mkdtemp() and the xlsx write land on a writable disk
  job state      app.py keeps jobs in a process dict, so the poll and the download
                 must reach the SAME warm instance as the upload. This is the one
                 that can fail intermittently on Vercel and never fails locally.
  the audit gate bad_chars / repeats / vocab_leaks must come back 0/0/0

The chat lines copy the real Zoom "saved chat" shape exactly - `YYYY-MM-DD HH:MM:SS
From <name> to Everyone:` then tab-indented message lines - because parse_chat.py
keys on it.
"""
import sys
import time
import urllib.request
import urllib.error
import json
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010").rstrip("/")

ATT = """Attendee Report
Report generated time,08/09/2026 02:31:08 PM
Topic,Webinar ID,Actual Start Time,Actual Duration (minutes),# Registrants,Unique Viewers,Total Users
Smoke Test Workshop,000 0000 0000,"08/09/2026 10:45:00 AM",226,6,5,6
Host Details
Attended,User Name (Original Name),Email,Join Time,Leave Time,Time in Session (minutes),Is Guest,Country/Region Name
Yes,Smoke Host,host@example.invalid,"08/09/2026 10:45:00 AM","08/09/2026 02:30:00 PM",225,No,India
Attendee Details
Attended,User Name (Original Name),First Name,Last Name,Email,Phone,Registration Time,Approval Status,Join Time,Leave Time,Time in Session (minutes),Is Guest,Country/Region Name
Yes,Buyer Person,Buyer,Person,buyer@example.invalid,9000000001,"08/08/2026 09:00:00 PM",approved,"08/09/2026 10:50:00 AM","08/09/2026 02:29:00 PM",219,No,India
Yes,Asker Person,Asker,Person,asker@example.invalid,9000000002,"08/08/2026 09:00:00 PM",approved,"08/09/2026 10:52:00 AM","08/09/2026 02:00:00 PM",188,No,India
Yes,Quiet Person,Quiet,Person,quiet@example.invalid,9000000003,"08/08/2026 09:00:00 PM",approved,"08/09/2026 10:55:00 AM","08/09/2026 01:30:00 PM",155,No,India
Yes,Grumpy Person,Grumpy,Person,grumpy@example.invalid,9000000004,"08/08/2026 09:00:00 PM",approved,"08/09/2026 11:00:00 AM","08/09/2026 11:20:00 AM",20,No,India
Yes,Token Person,Token,Person,token@example.invalid,9000000005,"08/08/2026 09:00:00 PM",approved,"08/09/2026 11:05:00 AM","08/09/2026 02:25:00 PM",200,No,India
No,Noshow Person,Noshow,Person,noshow@example.invalid,9000000006,"08/08/2026 09:00:00 PM",approved,,,0,No,India
"""

CHAT = """2026-08-09 10:51:00 From Buyer Person to Everyone:
\tI want to join, please send me the payment link
2026-08-09 10:52:30 From Asker Person to Everyone:
\tI am a school teacher, will this help me make question papers?
2026-08-09 11:06:00 From Token Person to Everyone:
\t10x
2026-08-09 11:07:00 From Token Person to Everyone:
\t10x
2026-08-09 11:08:00 From Token Person to Everyone:
\tbonus
2026-08-09 11:10:00 From Grumpy Person to Everyone:
\tthis is a waste of time, useless
2026-08-09 13:40:00 From Buyer Person to Everyone:
\twhat is the fees and is there an emi option?
"""


def post(url, fields, files):
    b = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += (f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                 f"{v}\r\n").encode()
    for k, name, data, ctype in files:
        body += (f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                 f"filename=\"{name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
        body += data + b"\r\n"
    body += f"--{b}--\r\n".encode()
    r = urllib.request.Request(url, data=body, method="POST",
                               headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(r, timeout=120) as fh:
        return fh.status, fh.read()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=120) as fh:
            return fh.status, fh.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


fails = []


def check(label, ok, detail=""):
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not ok:
        fails.append(label)


print(f"smoke test against {BASE}\n")
st, page = get(BASE + "/")
check("GET / serves the page", st == 200 and b"<" in page, f"{st}, {len(page):,} bytes")

t0 = time.time()
st, raw = post(BASE + "/api/process",
               {"pricing": "", "cta": "", "extras": "on"},
               [("att_files", "attendee_smoke.csv", ATT.encode(), "text/csv"),
                ("chat_files", "meeting_saved_chat.txt", CHAT.encode("utf-8"), "text/plain")])
check("POST /api/process accepted", st == 200, str(st))
if st != 200:
    print(raw[:400]);  sys.exit(1)
jid = json.loads(raw)["job"]

state, s = "running", {}
for _ in range(90):
    st, raw = get(f"{BASE}/api/status/{jid}")
    if st != 200:
        check("poll reached the same instance", False, f"status {st} - cold instance")
        break
    s = json.loads(raw)
    state = s["state"]
    if state != "running":
        break
    time.sleep(1)
check("job finished", state == "done", f"state={state} in {time.time()-t0:.1f}s")
if s.get("error"):
    print("     error:", s["error"][:300])

su = s.get("summary") or {}
check("rows produced", su.get("rows") == 6, f"rows={su.get('rows')}")
check("chat-clean audit is 0/0/0", su.get("audit") == {"bad_chars": 0, "repeats": 0,
                                                       "vocab_leaks": 0},
      str(su.get("audit")))
cc = su.get("category_counts") or {}
check("the buyer reached a top bucket",
      any(cc.get(k) for k in ("purchase intent high", "strong interest")), str(cc))
check("the no-show is non attended", cc.get("non attended") == 1, str(cc.get("non attended")))

st, xl = get(f"{BASE}/api/download/{jid}")
check("xlsx downloads", st == 200 and xl[:2] == b"PK", f"{st}, {len(xl):,} bytes")
st, cs = get(f"{BASE}/api/csv/{jid}")
check("csv downloads", st == 200 and len(cs) > 100, f"{st}, {len(cs):,} bytes")

print(f"\n{'ALL CHECKS PASS' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
