#!/usr/bin/env python3
"""
HTC News (Health Tech Circle's News) — RSS ingest, dedupe, LLM categorize/summarize, JSON store.

Usage:
  python scripts/ingest.py
  python scripts/ingest.py --dry-run
  python scripts/ingest.py --skip-llm

Env:
  GEMINI_API_KEY  — primary LLM (Google AI Studio)
  GROQ_API_KEY    — fallback LLM
  RETENTION_DAYS  — prune older than N days (default 120)
  MAX_NEW_PER_RUN — cap new LLM calls per run (default 80)
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import feedparser
import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
FEEDS_PATH = Path(__file__).resolve().parent / "feeds.yml"
ARTICLES_PATH = ROOT / "data" / "articles.json"

CATEGORIES = (
    "medical-device",
    "ai-in-health",
    "wellness-tech",
    "fitness-tech",
    "other",
)

# Featured regions: Global + India + top 5 digital-health markets
TOP_DIGITAL_HEALTH_COUNTRIES = ("US", "CN", "GB", "DE", "JP")
FEATURED_REGIONS = ("GLOBAL", "IN") + TOP_DIGITAL_HEALTH_COUNTRIES

# Feed / source name → default country when the story itself is ambiguous
SOURCE_COUNTRY_HINTS = {
    "fda": "US",
    "google news digital health us": "US",
    "google news digital health india": "IN",
    "google news india": "IN",
    "ethealthworld": "IN",
    "express healthcare": "IN",
    "the hindu": "IN",
    "google news digital health china": "CN",
    "google news digital health uk": "GB",
    "google news digital health germany": "DE",
    "google news digital health japan": "JP",
    "google news digital health global": "GLOBAL",
    "google news health us": "US",
    "google news health india": "IN",
    "google news health uk": "GB",
    "who news": "GLOBAL",
    "stat news": "US",
    "medtech dive": "US",
    "fierce biotech": "US",
    "fierce medtech": "US",
}

# Phrase → ISO / GLOBAL (longer phrases first via sorted match)
COUNTRY_PHRASES: list[tuple[str, str]] = [
    ("united states", "US"),
    ("united kingdom", "GB"),
    ("great britain", "GB"),
    ("saudi arabia", "SA"),
    ("south korea", "KR"),
    ("hong kong", "HK"),
    ("new zealand", "NZ"),
    ("worldwide", "GLOBAL"),
    ("global", "GLOBAL"),
    ("international", "GLOBAL"),
    ("europe", "GLOBAL"),
    ("european", "GLOBAL"),
    ("america", "US"),
    ("american", "US"),
    ("u.s.", "US"),
    ("u.s", "US"),
    ("usa", "US"),
    ("uk", "GB"),
    ("britain", "GB"),
    ("british", "GB"),
    ("england", "GB"),
    ("nhs", "GB"),
    ("india", "IN"),
    ("indian", "IN"),
    ("ayushman", "IN"),
    ("china", "CN"),
    ("chinese", "CN"),
    ("germany", "DE"),
    ("german", "DE"),
    ("diga", "DE"),
    ("japan", "JP"),
    ("japanese", "JP"),
    ("israel", "IL"),
    ("singapore", "SG"),
    ("australia", "AU"),
    ("canada", "CA"),
    ("france", "FR"),
    ("brazil", "BR"),
    ("who ", "GLOBAL"),
]

HEALTH_HINTS = re.compile(
    r"\b(health|healthcare|medical|medtech|biotech|biopharma|pharma|hospital|"
    r"clinical|patient|diagnos|therap|fda|wellness|fitness|"
    r"wearable|digital.?health|telehealth|telemedicine|genomic|"
    r"oncolog|cardio|implant|vaccine|drug|trial|med[- ]?device|"
    r"life.?sciences?|precision.?medicine)\b",
    re.I,
)

# Keep country feeds on tech/digital angle (avoids pure hospital/NHS workforce noise)
DIGITAL_HINTS = re.compile(
    r"\b(digital|telehealth|telemedicine|telecare|medtech|health.?tech|"
    r"e-?health|mhealth|wearable|app|software|platform|ai|artificial|"
    r"machine learning|data|cyber|ehr|emr|fhir|device|diagnostic|"
    r"startup|funding|ipo|fda|ce mark|diga|abdm|ayushman|"
    r"remote.?monitor|virtual.?care|online.?consult)\b",
    re.I,
)

TITLE_NOISE = re.compile(r"[^\w\s]", re.U)
HTML_TAG = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    text = HTML_TAG.sub(" ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def safe_print(msg: str) -> None:
    """Avoid Windows console UnicodeEncodeError on emojis / odd glyphs."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_entry_date(entry: dict[str, Any]) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime(*st[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass
    return None


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    # Drop tracking params and fragments
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    drop = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}
    query = [(k, v) for k, v in query if k.lower() not in drop]
    clean = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=parsed.path.rstrip("/") or "/",
        query=urllib.parse.urlencode(query),
        fragment="",
    )
    return urllib.parse.urlunparse(clean)


def normalize_title(title: str) -> str:
    t = TITLE_NOISE.sub(" ", (title or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def article_id(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()[:16]


def load_articles() -> dict[str, Any]:
    if not ARTICLES_PATH.exists():
        return {"updated_at": None, "articles": []}
    with ARTICLES_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    if "articles" not in data:
        data = {"updated_at": None, "articles": data if isinstance(data, list) else []}
    return data


def save_articles(data: dict[str, Any]) -> None:
    ARTICLES_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = iso(utc_now())
    with ARTICLES_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_feeds() -> list[dict[str, str]]:
    with FEEDS_PATH.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return list(raw.get("feeds") or [])


def looks_health_related(title: str, summary: str, group: str) -> bool:
    if group in ("trade", "research", "regulatory", "country"):
        return True
    text = f"{title} {summary}"
    return bool(HEALTH_HINTS.search(text))


def looks_country_story(title: str, summary: str, feed_country: str | None) -> bool:
    """Drop off-topic country-feed noise (e.g. NHS pay rounds without a tech angle)."""
    text = f"{title} {summary}"
    if feed_country == "IN":
        # India: keep health + light digital/policy (Ayushman, telemedicine, medtech)
        return bool(HEALTH_HINTS.search(text) or DIGITAL_HINTS.search(text))
    return bool(DIGITAL_HINTS.search(text))


def normalize_country_code(raw: Any) -> str | None:
    if raw in ("", "null", "None", None):
        return None
    if not isinstance(raw, str):
        return None
    code = raw.strip().upper()
    if code in ("GLOBAL", "WORLD", "INTL", "INTERNATIONAL"):
        return "GLOBAL"
    if len(code) == 2 and code.isalpha():
        return code
    return None


def detect_country_from_text(title: str, excerpt: str) -> str | None:
    text = f"{title} {excerpt}".lower()
    # Prefer India when clearly present (project focus)
    if re.search(r"\b(india|indian|ayushman|abdm|niti aayog)\b", text):
        return "IN"
    hits: list[str] = []
    for phrase, code in COUNTRY_PHRASES:
        if phrase in text or re.search(rf"\b{re.escape(phrase)}\b", text):
            if code not in hits:
                hits.append(code)
    if not hits:
        return None
    # Multiple distinct countries → global story
    non_global = [c for c in hits if c != "GLOBAL"]
    if len(non_global) >= 2:
        return "GLOBAL"
    if "GLOBAL" in hits and not non_global:
        return "GLOBAL"
    return non_global[0] if non_global else "GLOBAL"


def country_from_source(source: str) -> str | None:
    key = (source or "").strip().lower()
    if key in SOURCE_COUNTRY_HINTS:
        return SOURCE_COUNTRY_HINTS[key]
    for hint, code in SOURCE_COUNTRY_HINTS.items():
        if hint in key:
            return code
    return None


def resolve_country(
    title: str,
    excerpt: str,
    source: str,
    feed_country: str | None,
    llm_country: Any,
) -> str | None:
    """Prefer text mention → LLM → feed default → source hint."""
    from_text = detect_country_from_text(title, excerpt)
    if from_text:
        return from_text
    from_llm = normalize_country_code(llm_country)
    if from_llm:
        return from_llm
    feed = normalize_country_code(feed_country)
    if feed:
        return feed
    return country_from_source(source)


def fetch_feed(url: str) -> feedparser.FeedParserDict:
    return feedparser.parse(
        url,
        request_headers={"User-Agent": "HTCNewsBot/0.1 (+https://weblrsolutions.github.io/htcnews/)"},
    )


def collect_candidates(
    feeds: list[dict[str, str]],
    existing_urls: set[str],
    existing_titles: set[str],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_this_run: set[str] = set()

    for feed in feeds:
        name = feed.get("name") or "Unknown"
        url = feed.get("url") or ""
        group = feed.get("group") or "tech"
        feed_country = normalize_country_code(feed.get("country"))
        if not url:
            continue
        try:
            parsed = fetch_feed(url)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: failed to fetch {name}: {exc}", file=sys.stderr)
            continue
        if getattr(parsed, "bozo", False) and not parsed.entries:
            print(f"WARN: empty/broken feed {name}: {getattr(parsed, 'bozo_exception', '')}", file=sys.stderr)
            continue

        for entry in parsed.entries:
            link = normalize_url(entry.get("link") or entry.get("id") or "")
            title = strip_html(entry.get("title") or "")
            # Some feeds put the URL inside an <a> as the title text only
            if title.lower().startswith("http") and entry.get("link"):
                title = strip_html(entry.get("title_detail", {}).get("value") or title)
            if not link or not title:
                continue
            # Prefer canonical link when title was HTML-wrapped
            if link.startswith("http") is False:
                continue
            ntitle = normalize_title(title)
            if link in existing_urls or link in seen_this_run:
                continue
            if ntitle and ntitle in existing_titles:
                continue

            raw_summary = entry.get("summary") or entry.get("description") or ""
            raw_summary = strip_html(raw_summary)[:800]

            if not looks_health_related(title, raw_summary, group):
                continue
            if group == "country" and not looks_country_story(title, raw_summary, feed_country):
                continue

            seen_this_run.add(link)
            candidates.append(
                {
                    "id": article_id(link),
                    "url": link,
                    "title": title,
                    "source": name,
                    "published_at": iso(parse_entry_date(entry)) or iso(utc_now()),
                    "raw_excerpt": raw_summary,
                    "group": group,
                    "feed_country": feed_country,
                }
            )

    # Newest first
    candidates.sort(key=lambda a: a["published_at"] or "", reverse=True)
    return candidates


def balance_candidates(
    candidates: list[dict[str, Any]],
    max_new: int,
) -> list[dict[str, Any]]:
    """Round-robin across featured regions so one market cannot crowd out others."""
    priority = list(FEATURED_REGIONS) + ["OTHER"]
    buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in priority}
    for c in candidates:
        region = c.get("feed_country") or detect_country_from_text(
            c.get("title") or "",
            c.get("raw_excerpt") or "",
        )
        if region in buckets:
            buckets[region].append(c)
        else:
            buckets["OTHER"].append(c)

    # Featured regions get a guaranteed share; India & Global get a slight boost
    weights = {
        "GLOBAL": 2,
        "IN": 4,
        "US": 2,
        "CN": 2,
        "GB": 2,
        "DE": 2,
        "JP": 2,
        "OTHER": 1,
    }
    weight_sum = sum(weights.values())
    quotas = {
        k: max(1, int(max_new * weights[k] / weight_sum)) for k in priority
    }
    # Adjust to exact max_new
    while sum(quotas.values()) > max_new:
        for k in reversed(priority):
            if quotas[k] > 1 and sum(quotas.values()) > max_new:
                quotas[k] -= 1
    while sum(quotas.values()) < max_new:
        for k in priority:
            if sum(quotas.values()) >= max_new:
                break
            if buckets[k]:
                quotas[k] += 1

    picked: list[dict[str, Any]] = []
    used_urls: set[str] = set()
    for k in priority:
        take = 0
        for item in buckets[k]:
            if take >= quotas[k]:
                break
            u = item.get("url") or ""
            if u in used_urls:
                continue
            used_urls.add(u)
            picked.append(item)
            take += 1

    # Fill remaining slots newest-first from leftovers
    if len(picked) < max_new:
        for item in candidates:
            if len(picked) >= max_new:
                break
            u = item.get("url") or ""
            if u in used_urls:
                continue
            used_urls.add(u)
            picked.append(item)

    picked.sort(key=lambda a: a["published_at"] or "", reverse=True)
    return picked[:max_new]


def extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def build_prompt(title: str, source: str, excerpt: str) -> str:
    cats = ", ".join(CATEGORIES)
    featured = ", ".join(FEATURED_REGIONS)
    return f"""You categorize health & technology news headlines for a public aggregator.

Return ONLY a JSON object with keys:
- category: one of [{cats}]
- country: ISO 3166-1 alpha-2 code if the story is clearly about one country.
  Use "GLOBAL" for worldwide / multi-country / WHO / EU-wide stories.
  Prefer these featured codes when applicable: [{featured}]
  Use "IN" for India-focused stories. Else null if unknown.
- summary: 1-2 original sentences in your own words (never copy the source). Focus on the health/tech angle. Max 60 words.

Title: {title}
Source: {source}
Excerpt: {excerpt or "(none)"}
"""


def call_gemini(prompt: str, api_key: str) -> dict[str, Any] | None:
    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash-lite")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 256,
            "responseMimeType": "application/json",
        },
    }
    resp = requests.post(url, json=payload, timeout=45)
    if resp.status_code != 200:
        print(f"WARN: Gemini {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return None
    data = resp.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return None
    return extract_json_object(text)


def call_groq(prompt: str, api_key: str) -> dict[str, Any] | None:
    model = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "temperature": 0.3,
        "max_tokens": 256,
        "messages": [
            {"role": "system", "content": "Respond with valid JSON only."},
            {"role": "user", "content": prompt},
        ],
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=45)
    if resp.status_code != 200:
        print(f"WARN: Groq {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return None
    data = resp.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return extract_json_object(text)


def heuristic_enrich(title: str, excerpt: str, source: str = "", feed_country: str | None = None) -> dict[str, Any]:
    text = f"{title} {excerpt}".lower()
    if any(w in text for w in ("ai ", "artificial intelligence", "machine learning", "llm", "deep learning")):
        category = "ai-in-health"
    elif any(w in text for w in ("device", "implant", "medtech", "diagnostic", "fda", "wearable")):
        category = "medical-device"
    elif any(w in text for w in ("fitness", "workout", "athletic", "sport")):
        category = "fitness-tech"
    elif any(w in text for w in ("wellness", "mental health", "sleep", "meditation")):
        category = "wellness-tech"
    else:
        category = "other"

    summary = excerpt[:220].strip() if excerpt else title
    if summary and not summary.endswith("."):
        summary = summary.rstrip(".") + "."
    # Prefer a short original-ish line without copying long excerpts
    if len(summary) > 180 or not excerpt:
        summary = f"Coverage of {title.rstrip('.')} from the health-tech news cycle."

    country = resolve_country(title, excerpt, source, feed_country, None)
    return {"category": category, "country": country, "summary": summary}


def enrich_article(item: dict[str, Any], skip_llm: bool) -> dict[str, Any]:
    prompt = build_prompt(item["title"], item["source"], item.get("raw_excerpt") or "")
    result: dict[str, Any] | None = None

    if not skip_llm:
        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        if gemini_key:
            result = call_gemini(prompt, gemini_key)
        if result is None and groq_key:
            result = call_groq(prompt, groq_key)

    if result is None:
        result = heuristic_enrich(
            item["title"],
            item.get("raw_excerpt") or "",
            item.get("source") or "",
            item.get("feed_country"),
        )

    category = str(result.get("category") or "other").strip().lower().replace(" ", "-")
    if category not in CATEGORIES:
        category = "other"

    country = resolve_country(
        item["title"],
        item.get("raw_excerpt") or "",
        item.get("source") or "",
        item.get("feed_country"),
        result.get("country"),
    )

    summary = str(result.get("summary") or "").strip()
    if not summary:
        summary = heuristic_enrich(
            item["title"],
            item.get("raw_excerpt") or "",
            item.get("source") or "",
            item.get("feed_country"),
        )["summary"]

    return {
        "id": item["id"],
        "url": item["url"],
        "title": item["title"],
        "source": item["source"],
        "published_at": item["published_at"],
        "category": category,
        "country": country,
        "summary": summary,
        "ingested_at": iso(utc_now()),
    }


def backfill_countries(articles: list[dict[str, Any]]) -> int:
    """Retag missing/weak countries on existing rows using text + source hints."""
    updated = 0
    for a in articles:
        resolved = resolve_country(
            a.get("title") or "",
            a.get("summary") or "",
            a.get("source") or "",
            None,
            a.get("country"),
        )
        # Prefer India / featured text hits over a stale null
        from_text = detect_country_from_text(a.get("title") or "", a.get("summary") or "")
        if from_text and a.get("country") != from_text:
            a["country"] = from_text
            updated += 1
        elif not a.get("country") and resolved:
            a["country"] = resolved
            updated += 1
    return updated


def prune_articles(articles: list[dict[str, Any]], retention_days: int) -> list[dict[str, Any]]:
    cutoff = utc_now() - timedelta(days=retention_days)
    kept: list[dict[str, Any]] = []
    for a in articles:
        raw = a.get("published_at") or a.get("ingested_at")
        if not raw:
            kept.append(a)
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            kept.append(a)
            continue
        if dt >= cutoff:
            kept.append(a)
    return kept


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest health-tech RSS into data/articles.json")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and report without writing")
    parser.add_argument("--skip-llm", action="store_true", help="Use heuristics only")
    args = parser.parse_args()

    retention_days = int(os.environ.get("RETENTION_DAYS", "120"))
    max_new = int(os.environ.get("MAX_NEW_PER_RUN", "80"))

    store = load_articles()
    articles: list[dict[str, Any]] = list(store.get("articles") or [])
    backfilled = backfill_countries(articles)
    if backfilled:
        print(f"Backfilled country on {backfilled} existing articles")

    existing_urls = {normalize_url(a.get("url", "")) for a in articles}
    existing_titles = {normalize_title(a.get("title", "")) for a in articles if a.get("title")}

    feeds = load_feeds()
    print(f"Loaded {len(feeds)} feeds; {len(articles)} existing articles")

    candidates = collect_candidates(feeds, existing_urls, existing_titles)
    print(f"Found {len(candidates)} new candidates")
    batch = balance_candidates(candidates, max_new)
    if len(candidates) > len(batch):
        print(f"Balanced to {len(batch)} across Global / India / top-5 markets")

    new_articles: list[dict[str, Any]] = []
    for i, item in enumerate(batch, 1):
        safe_print(f"[{i}/{len(batch)}] {item['source']}: {item['title'][:70]}")
        enriched = enrich_article(item, skip_llm=args.skip_llm)
        new_articles.append(enriched)
        if not args.skip_llm and (os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY")):
            time.sleep(0.35)  # gentle free-tier pacing

    # Drop placeholder sample rows once real articles exist
    if new_articles:
        articles = [
            a
            for a in articles
            if "example.com" not in (a.get("url") or "")
            and (a.get("source") or "") != "Sample Feed"
        ]

    merged = new_articles + articles
    merged = prune_articles(merged, retention_days)
    # Stable newest-first
    merged.sort(key=lambda a: a.get("published_at") or a.get("ingested_at") or "", reverse=True)

    # Dedupe again by url after merge
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for a in merged:
        u = normalize_url(a.get("url", ""))
        if not u or u in seen:
            continue
        seen.add(u)
        unique.append(a)

    # Light country priority: India + featured markets first within same day is UI-side;
    # keep storage newest-first.
    print(f"Writing {len(unique)} articles ({len(new_articles)} new)")
    if args.dry_run:
        print("Dry run — not writing file")
        return 0

    store["articles"] = unique
    save_articles(store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
