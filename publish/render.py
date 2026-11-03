"""Write the static site: JSON through the allow-list, pages from templates, charts as inline SVG."""

import json
import os
import shutil
from html import escape

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

import config
from export import PublishError

HERE = os.path.dirname(os.path.abspath(__file__))
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---------- The allow-list ----------

def check_post(p, reviewed_ids):
    allowed = config.REVIEWED_POST_KEYS if p.get("post_id") in reviewed_ids else config.PUBLIC_POST_KEYS
    extra = set(p) - allowed
    if extra:
        raise PublishError("allow-list", f"post {p.get('post_id')} carries {', '.join(sorted(extra))}")


def check_keys(obj, kind):
    extra = set(obj) - config.FILE_KEYS[kind]
    if extra:
        raise PublishError("allow-list", f"{kind} carries {', '.join(sorted(extra))}")


def check_tree(obj, reviewed_ids):
    """Every dict with a post_id anywhere in the output is a post and must pass the allow-list."""
    if isinstance(obj, dict):
        if "post_id" in obj:
            check_post(obj, reviewed_ids)
        for v in obj.values():
            check_tree(v, reviewed_ids)
    elif isinstance(obj, list):
        for v in obj:
            check_tree(v, reviewed_ids)


def write_json(out, rel, obj, reviewed_ids, kind=None):
    """The only way JSON reaches the site."""
    if kind == "meta.json":
        check_keys(obj, "meta.json")
        if obj.get("sample"):
            check_keys(obj["sample"], "meta.sample")
    elif kind == "monthly":
        for m in obj:
            check_keys(m, "monthly")
    elif kind == "topic":
        check_keys(obj, "topic")
        for m in obj["monthly"]:
            check_keys(m, "topic.monthly")
    check_tree(obj, reviewed_ids)
    path = os.path.join(out, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def strip_post(p):
    """Drop the build's helper keys so only allow-listed data is written."""
    return {k: v for k, v in p.items() if k in config.REVIEWED_POST_KEYS}


# ---------- Charts ----------

def month_label(m):
    return MONTH_NAMES[int(m[5:7]) - 1]


def bar_chart(rows, series, title):
    """Stacked monthly bars as SVG. series: [(key, label, css class)]. A table under it carries the same numbers."""
    w, h, left, bottom, top = 640, 260, 56, 34, 12
    totals = [sum(r[k] for k, _, _ in series) for r in rows]
    peak = max(totals + [1])
    step = 10 ** max(0, len(str(peak)) - 1)
    ceil = -(-peak // step) * step
    bw = (w - left) / max(1, len(rows))
    parts = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{escape(title)}">']
    for frac in (0, 0.5, 1):
        y = top + (h - top - bottom) * (1 - frac)
        parts.append(f'<line class="grid" x1="{left}" x2="{w}" y1="{y:.1f}" y2="{y:.1f}"/>'
                     f'<text class="axis" x="{left - 8}" y="{y + 7:.1f}" text-anchor="end">{int(ceil * frac):,}</text>')
    for i, r in enumerate(rows):
        x, y = left + i * bw + bw * 0.15, h - bottom
        for k, label, cls in series:
            bh = (h - top - bottom) * r[k] / ceil
            y -= bh
            parts.append(f'<rect class="{cls}" x="{x:.1f}" y="{y:.1f}" width="{bw * 0.7:.1f}" height="{bh:.1f}">'
                         f'<title>{month_label(r["month"])}: {r[k]:,} {escape(label)}</title></rect>')
        parts.append(f'<text class="axis" x="{x + bw * 0.35:.1f}" y="{h - 6}" text-anchor="middle">'
                     f'{month_label(r["month"])}</text>')
    parts.append("</svg>")
    return Markup("".join(parts))


# ---------- Pages ----------

def pct(k, n):
    return f"{round(100 * k / n)}%" if n else "–"


def env():
    e = Environment(loader=FileSystemLoader(os.path.join(HERE, "templates")), autoescape=select_autoescape(["html"]),
                    trim_blocks=True, lstrip_blocks=True)
    e.globals.update(labels=config.LABELS, pct=pct, month_label=month_label, repo_url=config.REPO_URL,
                     lock_wayback_url=config.LOCK_WAYBACK_URL)
    return e


def content(name):
    with open(os.path.join(HERE, "content", name), encoding="utf-8") as f:
        return Markup(markdown.markdown(f.read()))


def render(site, out):
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    rid = site.reviewed_ids
    e = env()

    def page(rel, template, root, **ctx):
        path = os.path.join(out, rel, "index.html")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(e.get_template(template).render(site=site, root=root, **ctx))

    m = site.meta
    total_wrote = sum(r["wrote_political"] for r in site.monthly)
    total_rt = sum(r["retweeted_political"] for r in site.monthly)
    home_chart = bar_chart(site.monthly, [("wrote_political", "political, wrote", "s-wrote"),
                                          ("retweeted_political", "political, retweeted", "s-rt"),
                                          ("other", "everything else", "s-other")],
                           "Musk's posts by month: political posts he wrote, political posts he retweeted, everything else")
    topics = sorted(site.topics.values(), key=lambda t: -len(t["posts"]))
    page("", "home.html", "", chart=home_chart, total_wrote=total_wrote, total_rt=total_rt, topics=topics)

    for t in topics:
        chart = bar_chart(t["monthly"], [("wrote", "wrote", "s-wrote"), ("retweeted", "retweeted", "s-rt")],
                          f"{t['name']} posts by month")
        page(f"topics/{t['slug']}", "topic.html", "../../", topic=t, chart=chart)

    page("methods", "methods.html", "../", body=content("methods.md"))
    page("about", "page.html", "../", body=content("about.md"), title="About")
    page("corrections", "page.html", "../", body=content("corrections.md"), title="Corrections")

    write_json(out, "data/meta.json", m, rid, "meta.json")
    write_json(out, "data/monthly.json", site.monthly, rid, "monthly")
    for t in topics:
        obj = {"name": t["name"], "slug": t["slug"], "monthly": t["monthly"],
               "reviewed": [strip_post(p) for p in t["reviewed"]], "posts": t["posts"]}
        write_json(out, f"data/topics/{t['slug']}.json", obj, rid, "topic")
    reviewed = [strip_post(p) for p in site.sample.get("posts", [])] + [strip_post(p) for p in site.selected]
    write_json(out, "data/reviewed.json", reviewed, rid)
    if site.sample.get("complete"):
        with open(os.path.join(out, "data", "sample.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(site.sample["text"])

    os.makedirs(os.path.join(out, "assets"))
    shutil.copy(os.path.join(HERE, "static", "tpa.css"), os.path.join(out, "assets", "tpa.css"))
    open(os.path.join(out, ".nojekyll"), "w").close()
