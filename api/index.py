# -*- coding: utf-8 -*-
"""The Vercel entry point. It adds nothing and changes nothing.

app.py and core/ are untouched: this file only puts the repository root on
sys.path and re-exports the FastAPI app so Vercel's ASGI runtime can find it.
`app.py` resolves templates/ from its own directory, which is that same root, so
the page still loads from templates/index.html.

THREE THINGS TO KNOW ABOUT THE DEPLOYED COPY. They are limits of a serverless
function, not faults in the tool, and the local run has none of them:

  UPLOAD    A Vercel request body is capped at 4.5 MB. Measured on the 9 August
            session (7 rooms, 25.9 MB of CSV and chat): gzipped to 4.77 MB, so a
            full seven-room session is about 6% too big to post in one request.
            Up to roughly five rooms fits. The tool refuses cleanly rather than
            truncating, and the local copy has no limit at all.

  TIME      A Hobby function is stopped at 60 s. The same seven-room session
            takes 34 s of processing here, scaling at about 4.9 s per room, so a
            large session lands inside the window but without much room to
            spare. (An earlier reading of 301 s was my own measurement error -
            tracemalloc was tracing the run.)

  MEMORY    Peak traced heap on that session was 199 MB against a 1024 MB
            function, so this is the one limit with real headroom.

The job dict in app.py lives in the process, and a serverless process is not
guaranteed to be the same one on the next request. In practice Vercel keeps a
warm instance and the upload -> poll -> download sequence stays on it, but if a
download ever 409s with "no downloadable output for this job", that is why:
process again, and it will come from the same instance. Fixing that properly
means parking job state outside the process, which is exactly the change this
deployment was asked not to make.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  - the real app, imported unchanged

__all__ = ["app"]
