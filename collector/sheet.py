"""Calls to the Musk Post Tracker sheet endpoint (the Apps Script web app). Used by the collector and the public site build."""

import os
import random
import time

import requests


def sheet_call(action, payload=None):
    body = {"secret": os.environ["SHEET_SECRET"], "action": action, **(payload or {})}
    # Apps Script answers with an HTML error page when it times out or can't get the sheet lock in 30s, which
    # happens when several backfills run at once. Every collector action is safe to repeat (append skips IDs it
    # already has), so keep retrying for about 15 minutes, with jitter so parallel runs don't retry in step.
    for wait in (15, 45, 90, 180, 300, 300, None):
        wait = wait and wait + random.uniform(0, wait / 2)
        try:
            r = requests.post(os.environ["SHEET_ENDPOINT"], json=body, timeout=300)
            r.raise_for_status()
            data = r.json()
            break
        except (requests.RequestException, ValueError) as e:
            if wait is None:
                raise
            snippet = getattr(e, "response", None) is not None and e.response.text[:200] or str(e)[:200]
            print(f"Sheet endpoint hiccup on {action} ({snippet!r}); retrying in {wait:.0f}s")
            time.sleep(wait)
    if not data.get("ok"):
        raise RuntimeError(f"Sheet endpoint error on {action}: {data}")
    return data
