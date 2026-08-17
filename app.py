# -*- coding: utf-8 -*-
"""Zoom Chat Categoriser -- local web app.

Everything runs on THIS machine; nothing is uploaded anywhere. Start with run.bat
(or: python -m uvicorn app:app --host 127.0.0.1 --port 8010) and open
http://127.0.0.1:8010

Flow: upload session zip(s) or room files -> pre-flight verdicts -> process ->
summary + download. A failed chat-clean audit BLOCKS the download by design.
"""
import gzip
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
# which on a serverless platform it does not.
ON_SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))

app = FastAPI(title="Zoom Chat Categoriser", docs_url=None, redoc_url=None)

JOBS = {}
_LOCK = threading.Lock()


def _job(jid):
    with _LOCK:
        return JOBS.get(jid)


def _run_job(jid, inputs, att_inputs, chat_inputs, pricing, cta, extras):
    job = _job(jid)

    def tick(msg):
        job['progress'].append(msg)

    try:
        if att_inputs or chat_inputs:
            rooms, notes = pipeline.load_session_from_pairs(att_inputs, chat_inputs)
        else:
            rooms, notes = pipeline.load_session(inputs)   # zip flow (API / power users)
        job['notes'] = notes
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
                                       progress=tick)
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
            'files_line': " + ".join(sorted({f for d in rooms.values() for f in d.get('files', [])})[:4]),
        }
        if res.get('gate_failed'):
            job['state'] = 'blocked'
            job['error'] = ("BLOCKED -- " + res['gate_failed'] + ". Junk leaked into Relevant Chat; "
                            "the deliverable must not ship. Details: %s" % res['audit_detail'])
            return
        tick("writing xlsx")
        name = "Chat Categories_%s.xlsx" % (res.get('session_date') or "session")
        path = os.path.join(job['dir'], name)
        build_xlsx.write_xlsx(res, path, extras=extras)
        job['xlsx'] = path
        job['state'] = 'done'
    except GateError as e:
        job['state'] = 'blocked'
        job['error'] = "BLOCKED -- %s" % e
    except ValueError as e:
        job['state'] = 'error'
        job['error'] = str(e)
    except Exception as e:
        import traceback
        job['state'] = 'error'
        job['error'] = "%s: %s" % (type(e).__name__, e)
        job['trace'] = traceback.format_exc()


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(HERE, 'templates', 'index.html'), encoding='utf-8') as fh:
        return fh.read()


@app.post("/api/process")
async def process(files: list[UploadFile] | None = File(None),
                  att_files: list[UploadFile] | None = File(None),
                  chat_files: list[UploadFile] | None = File(None),
                  pricing: str = Form(""), cta: str = Form(""),
                  extras: str = Form("")):
    async def _read(fl):
        """Read each upload, inflating it if the browser gzipped it.

        The page compresses before sending because a hosted request body is capped
        at 4.5 MB and one real room is 7.4 MB of plain text. Detection is by the
        gzip magic number rather than by the filename, so an uncompressed upload --
        from an API caller, an older browser, or curl -- still works untouched."""
        out = []
        for f in fl or []:
            name = f.filename or "upload"
            data = await f.read()
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
                if name.endswith(".gz"):
                    name = name[:-3]
            out.append((name, data))
        return out

    inputs = await _read(files)
    att_inputs = await _read(att_files)
    chat_inputs = await _read(chat_files)
    if not (inputs or att_inputs or chat_inputs):
        return JSONResponse({"error": "no files uploaded"}, status_code=422)

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
        JOBS[jid] = {'state': 'running', 'progress': [], 'preflight': None,
                     'summary': None, 'error': None, 'xlsx': None, 'notes': [],
                     'extras': extras == "on", 'dir': tempfile.mkdtemp(prefix='zcc_')}
    args = (jid, inputs, att_inputs, chat_inputs, _num(pricing), _num(cta),
            extras == "on")
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
            'p': (r['cc'] + ' ' + r['phone']).strip() if r['phone'] else '',
            'cat': r['category'], 'basis': r['basis'],
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
    with open(path, 'w', encoding='utf-8') as fh:
        for r in job['rows']:
            if not r['deleted']:
                continue
            who = r['name'] + (" <%s>" % r['email'] if r['email'] else "")
            for msg in r['deleted'].split(' | '):
                if msg:
                    fh.write("%s: %s\n" % (who, msg))
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
        cols = BASE_COLS + (EXTRA_COLS if job.get('extras') else [])
        with open(path, 'w', encoding='utf-8-sig', newline='') as fh:
            w = _csv.writer(fh)
            w.writerow([h for h, _k, _w in cols])
            for r in job['rows']:
                w.writerow([clean(r.get(k, "")) for _h, k, _w in cols])
    return FileResponse(path, filename=os.path.basename(path), media_type='text/csv')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8010)
