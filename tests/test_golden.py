# -*- coding: utf-8 -*-
"""Golden + known-answer tests against the REAL seven weeks of workshop data.
Run:  python -m tests.test_golden          (takes several minutes)

Skips cleanly when the Lead scoring folder is not present on this machine.
Expected numbers were established 2026-08-05 against Lead Score_2026-08-02.xlsx:
  category alignment >= 99.4%, relevant/deleted chat >= 99.9% exact.
Every known divergence class is documented in tests/GOLDEN_NOTES.md.
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

LS = r"C:\Users\bgbik\OneDrive\Desktop\Lead scoring"
RAW = os.path.join(LS, "RAW FILES")
GOLD = os.path.join(LS, "output folder", "Lead Score_2026-08-02.xlsx")


def _have_data():
    return os.path.isdir(RAW) and os.path.exists(GOLD)


def _load(zname):
    from core import pipeline
    zp = os.path.join(RAW, zname)
    return pipeline.load_session([(zname, open(zp, 'rb').read())])


def test_preflight_expected_flags():
    """The validator must flag exactly the five known-bad rooms across seven zips."""
    if not _have_data():
        print("SKIP (no real data on this machine)"); return
    import glob
    from core import pipeline, preflight
    expected = {
        ("12 July Workshop Data.zip", "Ops 6"): "WARN",
        ("19 July Workshop Data.zip", "OPS 3"): "FAIL",
        ("19 July Workshop Data.zip", "OPS 5"): "FAIL",
        ("2 August Workshop Data.zip", "Zoom 11"): "FAIL",
        ("21 June Workshop Data.zip", "Evening"): "FAIL",
    }
    for zp in sorted(glob.glob(os.path.join(RAW, "*.zip"))):
        zname = os.path.basename(zp)
        rooms, _ = pipeline.load_session([(zname, open(zp, 'rb').read())])
        for v in preflight.check_rooms(rooms):
            want = expected.get((zname, v['room']), "ok")
            assert v['level'] == want, "%s/%s: got %s want %s (%s)" % (
                zname, v['room'], v['level'], want, v['messages'])


def test_12jul_ops6_nul_rows():
    """48 NUL bytes must cost 0 rows: 14,377 parsed, warning raised."""
    if not _have_data():
        print("SKIP"); return
    import io, zipfile
    from core import parse_attendee as PA
    z = zipfile.ZipFile(os.path.join(RAW, "12 July Workshop Data.zip"))
    n = next(x for x in z.namelist() if 'Ops 6' in x and x.endswith('.csv'))
    recs, meta, st = PA.aggregate_text(PA.decode_csv_bytes(z.read(n)))
    assert st['kept'] == 14377, "Ops 6 must parse 14,377 rows, got %s" % st.get('kept')
    assert st.get('nul', 0) > 0, "the NUL repair counter must fire so the UI can warn"


def test_19jul_no_chat_rooms():
    """OPS 3 / OPS 5 have no chat export: attendees publish as non chatted, no error.
    (A handful of cross-identity phone-fallback matches may still carry chat -- that is
    the engine's own behaviour and those people genuinely typed in another room.)"""
    if not _have_data():
        print("SKIP"); return
    from core import pipeline
    rooms, _ = _load("19 July Workshop Data.zip")
    res = pipeline.process_session(rooms)
    ops = [r for r in res['rows'] if r['room'] in ('OPS 3', 'OPS 5')]
    assert len(ops) > 8000
    withchat = [r for r in ops if r['chatted'] == 'Yes']
    assert len(withchat) <= 10, "no-chat rooms must read (almost) entirely non-chatted"
    assert any("NO chat export" in w for w in res['warnings'])
    assert not res.get('gate_failed')


def test_21jun_poll_only_room_fails_cleanly():
    if not _have_data():
        print("SKIP"); return
    from core import pipeline
    rooms, notes = _load("21 June Workshop Data.zip")
    res = pipeline.process_session(rooms)          # must not raise
    assert res['room_stats'].get('Evening', {}).get('lost'), "Evening must be reported LOST"
    assert len(res['rows']) > 30000, "the other rooms must still publish"


def test_2aug_zoom11_truncation_warns():
    if not _have_data():
        print("SKIP"); return
    from core import pipeline
    rooms, _ = _load("2 August Workshop Data.zip")
    res = pipeline.process_session(rooms)
    assert any('Zoom 11' in w and 'TRUNCATED' in w for w in res['warnings'])


def test_golden_2aug():
    """Category / Relevant / Deleted vs the published file, per person by email key."""
    if not _have_data():
        print("SKIP"); return
    import openpyxl
    from core import pipeline
    from core import contact_norm as CN

    gold = {}
    wb = openpyxl.load_workbook(GOLD, read_only=True)
    ws = wb["All Leads (tagged)"]
    it = ws.iter_rows(values_only=True)
    hdr = list(next(it))
    ix = {h: i for i, h in enumerate(hdr)}
    for r in it:
        if r[ix["Tag"]] not in ("Invited - Attended", "Attended - Not invited"):
            continue
        ke = CN.key_email(r[ix["Email"]]) or CN.key_email(r[ix["All Sheet Email"]])
        if ke:
            gold[ke] = (r[ix["Engagement Category"]], r[ix["Relevant Chat"]] or "", r[ix["Deleted Chat"]] or "")
    wb.close()

    rooms, _ = _load("2 August Workshop Data.zip")
    # production-equivalent windows for the mm>=1 & cta & pri branch (see GOLDEN_NOTES)
    res = pipeline.process_session(rooms, pricing_offset=(164.57, 172.98), cta_offset=(202.78, 211.95))
    mine = {}
    for r in res['rows']:
        ke = CN.key_email(r['email'])
        if ke:
            mine[ke] = r

    both = set(gold) & set(mine)
    assert len(both) > 45000
    cat = rel = dele = 0
    for k in both:
        g, m = gold[k], mine[k]
        if g[0] == m['category'] or (g[0] == 'non chatted' and m['category'] == 'non attended'
                                     and m['attended'] == 'No'):
            cat += 1
        if g[1] == (m['relevant'] or ""):
            rel += 1
        if g[2] == (m['deleted'] or ""):
            dele += 1
    n = float(len(both))
    print("aligned: cat %.2f%% rel %.2f%% del %.2f%%" % (100 * cat / n, 100 * rel / n, 100 * dele / n))
    assert cat / n >= 0.994, "category alignment regressed: %.4f" % (cat / n)
    assert rel / n >= 0.999, "relevant chat match regressed: %.4f" % (rel / n)
    assert dele / n >= 0.999, "deleted chat match regressed: %.4f" % (dele / n)


if __name__ == '__main__':
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, "--", e)
    print("%s" % ("ALL PASS" if not fails else "%d FAILURE(S)" % fails))
    raise SystemExit(1 if fails else 0)
