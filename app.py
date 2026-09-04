# -*- coding: utf-8 -*-
"""Zoom Chat Categoriser -- local web app.

Everything runs on THIS machine; nothing is uploaded anywhere. Start with run.bat
(or: python -m uvicorn app:app --host 127.0.0.1 --port 8010) and open
http://127.0.0.1:8010

Flow: upload session zip(s) or room files -> pre-flight verdicts -> process ->
summary + download. A failed chat-clean audit BLOCKS the download by design.
"""
import os
import tempfile
import threading
import uuid

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from core import build_xlsx, pipeline, preflight
from core.pipeline import GateError

HERE = os.path.dirname(os.path.abspath(__file__))

# Vercel sets VERCEL=1 in the function environment. Nothing else in this file cares
# where it is running - only whether a background thread will survive the response,
# which on a serverless platform it does not. Render does, so this is false there
# and the threaded path below is used unchanged.
ON_SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))

app = FastAPI(title="Zoom Chat Categoriser", docs_url=None, redoc_url=None)

JOBS = {}
_LOCK = threading.Lock()


def _job(jid):
    with _LOCK:
        return JOBS.get(jid)


def _run_job(jid, inputs, att_inputs, chat_inputs, pricing, cta, extras,
             activity_date="", session_name=""):
    job = _job(jid)

    def tick(msg):
        job['progress'].append(msg)

    try:
        if att_inputs or chat_inputs:
            rooms, notes = pipeline.load_session_from_pairs(att_inputs, chat_inputs)
        else:
            rooms, notes = pipeline.load_session(inputs)   # zip flow (API / power users)
        job['notes'] = list(job.get('pre_notes') or []) + list(notes)
        if not rooms:
            job['state'] = 'error'
            job['error'] = ("No usable files found. The Attendee Report block needs the Zoom "
                            "attendee CSV (it must contain an 'Attendee Details' section); the "
                            "Save Chat block needs the meeting_saved_chat .txt.")
            return
        tick("pre-flight checks")
        job['preflight'] = preflight.check_rooms(rooms)
        if not any(d['attendee_texts'] for d in rooms.values()):
            job['state'] = 'error'
            job['error'] = ("None of the uploaded files contains a Zoom ATTENDEE report "
                            "(a CSV with an 'Attendee Details' section). Nothing to categorise.")
            return
        res = pipeline.process_session(rooms, pricing_offset=pricing, cta_offset=cta,
                                       progress=tick, activity_date=activity_date,
                                       session_name=session_name)
        job['rows'] = res['rows']            # kept for the results-table API
        job['summary'] = {
            'rows': len(res['rows']),
            'session_date': res.get('session_date'),
            'category_counts': res['category_counts'],
            'attribution': res['attribution'],
            'room_stats': res['room_stats'],
            'warnings': res['warnings'],
            'audit': res['audit'],
            'freq_suspects': res['freq_suspects'][:12],
            'phone_col_missing': [r for r, st in res['room_stats'].items()
                                  if st.get('people') and not st.get('has_phone_col')],
            'chatted': sum(1 for r in res['rows'] if r['chatted'] == 'Yes'),
            'pricing_yes': sum(1 for r in res['rows'] if r.get('pricing') is True),
            'cta_yes': sum(1 for r in res['rows'] if r.get('cta') is True),
            'have_offsets': pricing is not None or cta is not None,
            # per-marker, so a session with only one offset entered does not publish a
            # confident '0' for the tile whose marker was never given
            'have_pricing': pricing is not None,
            'have_cta': cta is not None,
            'files_line': " + ".join(sorted({f for d in rooms.values() for f in d.get('files', [])})[:4]),
            'activity_date': res.get('activity_date'),
            'activity_date_derived': res.get('activity_date_derived'),
            'activity_date_valid': res.get('activity_date_valid'),
            'session_name': res.get('session_name'),
        }
        if res.get('gate_failed'):
            job['state'] = 'blocked'
            job['error'] = ("BLOCKED -- " + res['gate_failed'] + ". Junk leaked into Relevant Chat; "
                            "the deliverable must not ship. Details: %s" % res['audit_detail'])
            return
        if not res['rows']:
            # Every other endpoint already refuses a row-less job, and a header-only
            # spreadsheet is not a deliverable -- say so instead of publishing 'done'.
            job['error'] = ("No attendees could be read from these files, so there is "
                            "nothing to score. Check the pre-flight findings: the usual "
                            "cause is an attendee report whose columns were renamed, or a "
                            "registration export uploaded in place of an attendee report.")
            job['state'] = 'error'
            return
        tick("writing xlsx")
        name = "Chat Categories_%s.xlsx" % (res.get('session_date') or "session")
        path = os.path.join(job['dir'], name)
        build_xlsx.write_xlsx(res, path, extras=extras)
        job['xlsx'] = path
        job['state'] = 'done'
    # In every handler below the MESSAGE is stored before the STATE. The poller reacts to
    # state, so setting state first left a window where it read a terminal state with
    # error=None and reported 'unknown error'.
    except GateError as e:
        job['error'] = "BLOCKED -- %s" % e
        job['state'] = 'blocked'
    except ValueError as e:
        job['error'] = str(e)
        job['state'] = 'error'
    except Exception as e:
        import traceback
        job['trace'] = traceback.format_exc()
        job['error'] = "%s: %s" % (type(e).__name__, e)
        job['state'] = 'error'


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(HERE, 'templates', 'index.html'), encoding='utf-8') as fh:
        return fh.read()


KEEP_JOBS = 4          # finished runs whose downloads stay available
ORPHAN_AGE_S = 6 * 3600    # a dir older than this cannot belong to a live session


def _sweep_orphan_dirs():
    """Delete zcc_* job dirs left behind by earlier runs of this app.

    _prune_jobs only knows about jobs in THIS process, so every previous run's directory
    (an xlsx + csv + removed-chat each) stayed in %TEMP% forever. Only dirs older than
    ORPHAN_AGE_S are touched, so a second instance running right now keeps its own.
    """
    import glob
    import shutil
    import time
    now = time.time()
    freed = 0
    for d in glob.glob(os.path.join(tempfile.gettempdir(), 'zcc_*')):
        try:
            if not os.path.isdir(d) or now - os.path.getmtime(d) < ORPHAN_AGE_S:
                continue
            shutil.rmtree(d, ignore_errors=True)
            freed += 1
        except OSError:
            pass
    return freed


def _prune_jobs():
    """Drop all but the KEEP_JOBS most recent finished jobs, deleting their temp dirs.

    Called with _LOCK held, just before a new job is registered. Without this every run
    left a zcc_* directory behind (an xlsx + csv + removed-chat per run) and held its full
    row list in memory for the lifetime of the process.
    """
    import shutil
    finished = [k for k, v in JOBS.items() if v.get('state') != 'running']
    for jid in finished[:max(0, len(finished) - KEEP_JOBS)]:
        d = JOBS[jid].get('dir')
        JOBS.pop(jid, None)
        if d and os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)


_sweep_orphan_dirs()


@app.post("/api/process")
async def process(files: list[UploadFile] | None = File(None),
                  att_files: list[UploadFile] | None = File(None),
                  chat_files: list[UploadFile] | None = File(None),
                  pricing: str = Form(""), cta: str = Form(""),
                  extras: str = Form(""),
                  activity_date: str = Form(""), session_name: str = Form("")):
    async def _read(fl):
        out = []
        for f in fl or []:
            out.append((f.filename or "upload", await f.read()))
        return out

    inputs = await _read(files)
    att_inputs = await _read(att_files)
    chat_inputs = await _read(chat_files)
    if not (inputs or att_inputs or chat_inputs):
        return JSONResponse({"error": "no files uploaded"}, status_code=422)
    # The loose-file flow wins when both are present (see _run_job); say so rather than
    # dropping the zip in silence.
    pre_notes = []
    if inputs and (att_inputs or chat_inputs):
        pre_notes.append("Ignored the uploaded zip (%s) -- the individually chosen "
                         "attendee/chat files were used instead."
                         % ", ".join(n for n, _b in inputs)[:120])

    def _num(s):
        s = (s or "").strip()
        if not s:
            return None
        try:
            v = float(s)
            return v if 0 <= v <= 24 * 60 else None
        except ValueError:
            return None

    jid = uuid.uuid4().hex[:12]
    with _LOCK:
        _prune_jobs()
        JOBS[jid] = {'state': 'running', 'progress': [], 'preflight': None,
                     'summary': None, 'error': None, 'xlsx': None,
                     'notes': list(pre_notes), 'pre_notes': list(pre_notes),
                     'extras': extras == "on", 'dir': tempfile.mkdtemp(prefix='zcc_')}
    args = (jid, inputs, att_inputs, chat_inputs,
            _num(pricing), _num(cta), extras == "on",
            (activity_date or "").strip()[:40],
            (session_name or "").strip()[:120])
    if ON_SERVERLESS:
        # A serverless platform SUSPENDS the execution context as soon as the
        # response is sent, so a daemon thread stops dead the moment this function
        # returns. Measured on a full-size room deployed to Vercel: the job sat at
        # "scoring 16781 people" and had not moved 35 seconds later, while a
        # six-person job finished because it completed inside the first request
        # window. So on the hosted copy the work is done HERE, before responding,
        # and the first /api/status already reports 'done'.
        #
        # The cost is that the request now lasts as long as the processing, and the
        # platform stops it at 60 s. One room is comfortably inside that; a whole
        # multi-room session is not, and the local copy is the answer for those.
        # The progress panel stops animating - it jumps straight to the result -
        # which is a fair trade for a job that finishes at all.
        _run_job(*args)
    else:
        threading.Thread(target=_run_job, daemon=True, args=args).start()
    return {"job": jid}


@app.get("/api/status/{jid}")
def status(jid: str):
    job = _job(jid)
    if job is None:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    return {k: job.get(k) for k in ('state', 'progress', 'preflight', 'summary',
                                    'error', 'notes')} | {'has_xlsx': bool(job.get('xlsx'))}


@app.get("/api/rows/{jid}")
def rows(jid: str, cat: str = "", q: str = "", offset: int = 0, limit: int = 120):
    """Filtered slice of the result rows for the results table (never the full 50k)."""
    job = _job(jid)
    if job is None or not job.get('rows'):
        return JSONResponse({"error": "no rows for this job"}, status_code=404)
    ql = (q or "").strip().lower()
    out = []
    matched = 0
    limit = max(1, min(int(limit or 120), 500))
    offset = max(0, int(offset or 0))
    for r in job['rows']:
        if cat and r['category'] != cat:
            continue
        if ql and ql not in r['name'].lower() and ql not in r['email'].lower():
            continue
        matched += 1
        if matched <= offset or len(out) >= limit:
            continue
        out.append({
            'n': r['name'], 'e': r['email'],
            'p': r.get('phone_fmt', ''),
            'cat': r['category'], 'basis': r['basis'], 'conf': r.get('confidence', ''),
            'pr': r.get('pricing'), 'ct': r.get('cta'),
            'min': r['minutes'], 'room': r['room'], 'att': r['attended'],
            'msgs': r['msg_count'],
            'rel': [x for x in (r['relevant'] or '').split(' | ') if x],
            'del': [x for x in (r['deleted'] or '').split(' | ') if x],
        })
    return {'total': matched, 'offset': offset, 'rows': out}


@app.get("/api/removed/{jid}")
def removed(jid: str):
    """Everything the cleaner removed, one line per message, as a .txt download."""
    job = _job(jid)
    if job is None or not job.get('rows'):
        return JSONResponse({"error": "no rows for this job"}, status_code=404)
    path = os.path.join(job['dir'], "removed-chat.txt")
    if not os.path.exists(path):
        # Build under a private name, then rename into place: the old code reopened the
        # final path with mode 'w' on every request, truncating a file that a previous
        # FileResponse could still be streaming.
        tmp = "%s.part%d" % (path, os.getpid() ^ threading.get_ident())
        with open(tmp, 'w', encoding='utf-8') as fh:
            for r in job['rows']:
                if not r['deleted']:
                    continue
                who = r['name'] + (" <%s>" % r['email'] if r['email'] else "")
                for msg in r['deleted'].split(' | '):
                    if msg:
                        fh.write("%s: %s\n" % (who, msg))
        os.replace(tmp, path)
    return FileResponse(path, filename="removed-chat.txt", media_type="text/plain")


@app.get("/api/download/{jid}")
def download(jid: str):
    job = _job(jid)
    if job is None or job.get('state') != 'done' or not job.get('xlsx'):
        return JSONResponse({"error": "no downloadable output for this job"}, status_code=409)
    return FileResponse(job['xlsx'], filename=os.path.basename(job['xlsx']),
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.get("/api/csv/{jid}")
def download_csv(jid: str):
    """CSV twin of the XLSX: same columns, same order, same audit gate. Written with a
    UTF-8 BOM so Excel decodes Indic scripts correctly. NOTE: only the XLSX can force
    phone cells to text -- Excel shows long numbers from a CSV in scientific notation."""
    job = _job(jid)
    if job is None or job.get('state') != 'done' or not job.get('rows'):
        return JSONResponse({"error": "no downloadable output for this job"}, status_code=409)
    import csv as _csv
    from core.build_xlsx import BASE_COLS, EXTRA_COLS, clean
    path = os.path.join(job['dir'], os.path.splitext(os.path.basename(job['xlsx']))[0] + ".csv")
    if not os.path.exists(path):
        # Build under a per-request temp name and rename into place. Writing directly to
        # `path` made os.path.exists() true the instant the file was created, so a second
        # click during the write served a half-finished CSV.
        cols = BASE_COLS + (EXTRA_COLS if job.get('extras') else [])
        tmp = "%s.part%d" % (path, os.getpid() ^ threading.get_ident())
        with open(tmp, 'w', encoding='utf-8-sig', newline='') as fh:
            w = _csv.writer(fh)
            w.writerow([h for h, _k, _w in cols])
            for r in job['rows']:
                w.writerow([clean(r.get(k, "")) for _h, k, _w in cols])
        os.replace(tmp, path)          # atomic: readers see either nothing or the whole file
    return FileResponse(path, filename=os.path.basename(path), media_type='text/csv')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8010)
