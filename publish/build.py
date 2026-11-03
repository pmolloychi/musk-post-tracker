"""Build the public site into _site/.

    python publish/build.py                         # fetch the live export (needs SHEET_ENDPOINT, SHEET_SECRET)
    python publish/build.py --fixture FILE          # build from a saved export, no network
    python publish/build.py --previous meta.json    # compare against the last published snapshot

Exits 1 with one FAILED line when a check fails. Never prints post data.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "collector"))
sys.path.insert(0, HERE)

import config  # noqa: E402
from export import PublishError, compute, ct_to_utc, fetch, load_previous, validate  # noqa: E402
from render import render  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture")
    ap.add_argument("--previous")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "_site"))
    args = ap.parse_args(argv)
    try:
        if args.fixture:
            with open(args.fixture, encoding="utf-8") as f:
                data = json.load(f)
            size = data.get("sample_size") or config.SAMPLE_SIZE
        else:
            data, size = fetch(), config.SAMPLE_SIZE
        # A saved export is judged as of when it was made, so an old fixture isn't "stale".
        now = ct_to_utc(data["generated_at"]) if args.fixture else None
        validate(data, load_previous(args.previous), now=now)
        site = compute(data, size)
        render(site, args.out)
    except PublishError as e:
        line = e.line()
        print(line)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return 1
    m = site.meta
    print(f"Built {m['total_posts']} posts ({m['political_posts']} political), data through {m['data_through_ct']} CT.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
