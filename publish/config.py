"""Fixed settings for the public site build. Topic and flag names come from the collector's own lists."""

from categories import CATEGORIES

# A post is political if it carries any of these (decided by Parker, 2026-11-02).
POLITICAL_TOPICS = (
    "Government/DOGE",
    "US politics & elections",
    "Immigration",
    "Race & ethnicity",
    "DEI policy",
    "Religion",
    "LGBTQ issues",
    "Trans issues",
    "Women & gender",
    "Media & journalists",
    "Free speech/censorship",
    "Foreign politics",
)
KNOWN_TOPICS = tuple(CATEGORIES) + ("UNCATEGORIZED",)
TYPES = ("Reply", "Retweet", "Quote", "Standalone")
WROTE = ("Reply", "Quote", "Standalone")
REQUIRED = ("post_id", "date_ct", "type", "link")

# Days the collector missed. Charts footnote them instead of showing a dip.
MISSING_DAYS = ("2026-04-10", "2026-04-11")

MAX_DROP = 0.01        # a post count more than 1% below the last snapshot stops the publish
MAX_STALE_HOURS = 48   # so does a newest post older than this
SAMPLE_SIZE = 100      # a fixture can override it with a top-level "sample_size"

# Filled in by hand once the sample's LOCK.md is captured on the Wayback Machine (see RUNBOOK.md).
LOCK_WAYBACK_URL = ""
REPO_URL = "https://github.com/pmolloychi/musk-post-tracker"

# Wording Parker can change.
LABELS = {
    "unreviewed": "AI-assigned topic, not reviewed by Parker",
    "sample_upheld": "Random sample #{n} of {size}: upheld",
    "sample_rejected": "Random sample #{n} of {size}: not upheld",
    "selected": "Selected example, not counted in the rate",
    "deleted": "Deleted after capture",
}

# The allow-list. Every post object written anywhere must use only these keys; the reviewed keys are allowed
# only on posts Parker reviewed.
PUBLIC_POST_KEYS = frozenset({
    "post_id", "date_ct", "type", "text", "context", "link", "their_link", "archive_link", "topics", "status",
})
REVIEWED_POST_KEYS = PUBLIC_POST_KEYS | {
    "group", "sample_no", "flag", "finding", "harm_type", "conduct", "rationale", "reviewed_at", "prior_finding",
}
FILE_KEYS = {
    "meta.json": {"snapshot_id", "data_through_ct", "built_at_utc", "total_posts", "political_posts",
                  "missing_days", "sample"},
    "meta.sample": {"drawn_at", "frame_size", "frame_sha256", "sample_sha256", "decided_count"},
    "monthly": {"month", "wrote_political", "retweeted_political", "other"},
    "topic": {"name", "slug", "monthly", "reviewed", "posts"},
    "topic.monthly": {"month", "wrote", "retweeted"},
}
