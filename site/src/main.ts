import "./styles.css";
import type { Article, ArticlesFile } from "./types";
import { CATEGORY_LABELS, COUNTRY_LABELS, FEATURED_REGIONS } from "./types";

const STOP_WORDS = new Set([
  "a",
  "an",
  "the",
  "and",
  "or",
  "of",
  "to",
  "in",
  "for",
  "on",
  "at",
  "by",
  "with",
  "from",
  "as",
  "is",
  "are",
  "was",
  "were",
  "be",
  "been",
  "its",
  "it",
  "this",
  "that",
  "these",
  "those",
  "into",
  "over",
  "after",
  "before",
  "about",
  "new",
  "how",
  "why",
  "what",
  "when",
  "who",
  "will",
  "can",
  "may",
  "not",
  "has",
  "have",
  "had",
  "their",
  "our",
  "your",
  "his",
  "her",
  "than",
  "via",
  "amid",
  "says",
  "said",
  "report",
  "reports",
  "news",
]);

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) {
  throw new Error("#app missing");
}

type TimeWindow = "24h" | "7d" | "30d" | "all";

const TIME_WINDOWS: { id: TimeWindow; label: string; hours: number | null }[] = [
  { id: "24h", label: "24 hours", hours: 24 },
  { id: "7d", label: "This week", hours: 24 * 7 },
  { id: "30d", label: "This month", hours: 24 * 30 },
  { id: "all", label: "All time", hours: null },
];

let activeRegion = "all";
let activeCategory = "all";
let activeTime: TimeWindow = "7d";
let searchQuery = "";
let activeTrend = "";

function withinTimeWindow(iso: string, window: TimeWindow): boolean {
  const hours = TIME_WINDOWS.find((t) => t.id === window)?.hours ?? null;
  if (hours === null) return true;
  const published = new Date(iso).getTime();
  if (Number.isNaN(published)) return false;
  return Date.now() - published <= hours * 3600_000;
}

function formatRelative(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const diffMs = Date.now() - d.getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} minute${mins === 1 ? "" : "s"} ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} day${days === 1 ? "" : "s"} ago`;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatAbsolute(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    hour: "numeric",
    minute: "2-digit",
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZoneName: "short",
  });
}

function formatUpdated(iso: string): string {
  const absolute = formatAbsolute(iso);
  if (!absolute) return "";
  const relative = formatRelative(iso);
  return relative ? `Updated ${absolute} (${relative})` : `Updated ${absolute}`;
}

function uniqueSorted(values: (string | null | undefined)[]): string[] {
  return [...new Set(values.filter((v): v is string => Boolean(v)))].sort((a, b) =>
    a.localeCompare(b),
  );
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function countryLabel(code: string | null | undefined): string {
  if (!code) return "";
  return COUNTRY_LABELS[code] ?? code;
}

function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, " ")
    .split(/[\s-]+/)
    .filter((w) => w.length > 2 && !STOP_WORDS.has(w) && !/^\d+$/.test(w));
}

/** Top keywords — size scales with how often they appear. */
function buildTrending(
  articles: Article[],
  limit = 6,
): { word: string; size: number }[] {
  const counts = new Map<string, number>();
  for (const a of articles.slice(0, 80)) {
    for (const w of tokenize(a.title)) {
      counts.set(w, (counts.get(w) ?? 0) + 1);
    }
  }
  const sorted = [...counts.entries()]
    .filter(([, n]) => n >= 2)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit);

  if (sorted.length === 0) return [];

  const max = sorted[0][1];
  const min = sorted[sorted.length - 1][1];
  return sorted.map(([word, n]) => {
    const t = max === min ? 1 : (n - min) / (max - min);
    // ~13px quiet → ~26px loud (Pulse-style cloud)
    const size = Math.round(13 + t * 13);
    return { word, size };
  });
}

function renderShell(): void {
  const timeOptions = TIME_WINDOWS.map(
    (t) =>
      `<option value="${t.id}"${t.id === activeTime ? " selected" : ""}>${t.label}</option>`,
  ).join("");

  const regionOptions = [
    `<option value="all" selected>All regions</option>`,
    ...FEATURED_REGIONS.map((r) => `<option value="${r.code}">${r.label}</option>`),
  ].join("");

  app!.innerHTML = `
    <div class="wrap">
      <header class="site-header">
        <div class="brand-row">
          <h1 class="logo">
            <a href="./" aria-label="HTC News — Health Tech Circle's News">
              <span class="logo-mark">Health Tech Circle's <span>News</span></span>
            </a>
          </h1>
          <div class="search">
            <input type="search" id="q" placeholder="Search stories…" autocomplete="off" aria-label="Search stories" />
          </div>
        </div>
        <p class="intro">
          HTC News — digital health, medtech, AI-in-healthcare &amp; wellness-tech headlines from trusted sources, aggregated in one place.
        </p>
      </header>

      <div class="toolbar" role="search" aria-label="Filter stories">
        <label class="toolbar-field">
          <span class="toolbar-label">Time</span>
          <select id="time" aria-label="Time range">${timeOptions}</select>
        </label>
        <label class="toolbar-field">
          <span class="toolbar-label">Region</span>
          <select id="region" aria-label="Region">${regionOptions}</select>
        </label>
        <label class="toolbar-field">
          <span class="toolbar-label">Category</span>
          <select id="category" aria-label="Category">
            <option value="all">All categories</option>
          </select>
        </label>
      </div>

      <div class="trending" id="trending" hidden></div>

      <div class="feed-head">
        <h2 class="feed-label">Health tech news</h2>
        <p class="meta-bar" id="meta" hidden></p>
      </div>
      <ul class="feed" id="feed" aria-live="polite"></ul>
    </div>
    <footer class="site-footer">
      <nav class="footer-links" aria-label="Follow and contact">
        <a href="https://whatsapp.com/channel/0029VbDBdm75kg6ylzr4rr1U" rel="noopener noreferrer" target="_blank">WhatsApp Channel</a>
        <a href="https://t.me/healthtechcircle" rel="noopener noreferrer" target="_blank">Telegram Channel</a>
        <a href="mailto:drpatelakshat@gmail.com?subject=HTC%20News%20%E2%80%94%20Suggestion%20/%20Sponsorship">Suggestions &amp; Sponsorship</a>
      </nav>
      <p>Summaries are original. Every headline links to the publisher.</p>
      <p class="disclaimer">
        Stories are aggregated from public RSS sources. We do not own, edit, or independently verify
        the underlying reporting — always confirm details on the publisher’s site.
      </p>
    </footer>
  `;
}

function populateCategoryFilter(articles: Article[]): void {
  const catSelect = document.querySelector<HTMLSelectElement>("#category");
  if (!catSelect) return;

  const categories = uniqueSorted(articles.map((a) => a.category));
  for (const c of categories) {
    const opt = document.createElement("option");
    opt.value = c;
    opt.textContent = CATEGORY_LABELS[c] ?? c;
    catSelect.append(opt);
  }
}

function syncTrendUi(): void {
  const strip = document.querySelector<HTMLElement>("#trending");
  if (!strip) return;
  strip.querySelectorAll<HTMLAnchorElement>(".trend-word").forEach((el) => {
    el.classList.toggle("is-active", el.dataset.word === activeTrend);
  });
  const clearBtn = strip.querySelector<HTMLButtonElement>(".trend-clear");
  if (clearBtn) clearBtn.hidden = !activeTrend;
}

function renderTrending(articles: Article[]): void {
  const strip = document.querySelector<HTMLElement>("#trending");
  if (!strip) return;

  const words = buildTrending(articles, 6);
  if (words.length === 0) {
    strip.hidden = true;
    strip.innerHTML = "";
    return;
  }

  strip.hidden = false;
  strip.innerHTML = `
    <span class="trending-label">Trending</span>
    <div class="trending-words">
      ${words
        .map(
          (w) =>
            `<a href="#${escapeHtml(w.word)}" data-word="${escapeHtml(w.word)}" class="trend-word${
              activeTrend === w.word ? " is-active" : ""
            }" style="font-size: ${w.size}px">${escapeHtml(w.word)}</a>`,
        )
        .join("")}
    </div>
    <button type="button" class="trend-clear" hidden>Clear</button>
  `;
  syncTrendUi();
}

function filterArticles(articles: Article[]): Article[] {
  const q = searchQuery.trim().toLowerCase();
  return articles.filter((a) => {
    if (!withinTimeWindow(a.published_at, activeTime)) return false;
    if (activeCategory !== "all" && a.category !== activeCategory) return false;
    if (activeRegion !== "all" && a.country !== activeRegion) return false;
    if (activeTrend) {
      const hay = `${a.title} ${a.summary}`.toLowerCase();
      if (!hay.includes(activeTrend)) return false;
    }
    if (q) {
      const hay = `${a.title} ${a.summary} ${a.source}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function renderFeed(articles: Article[], updatedAt: string | null): void {
  const feed = document.querySelector<HTMLUListElement>("#feed");
  const meta = document.querySelector<HTMLElement>("#meta");
  if (!feed || !meta) return;

  const regionName =
    activeRegion === "all" ? "All regions" : countryLabel(activeRegion) || activeRegion;
  const timeLabel = TIME_WINDOWS.find((t) => t.id === activeTime)?.label ?? activeTime;

  meta.hidden = false;
  meta.textContent = `${articles.length} stor${articles.length === 1 ? "y" : "ies"} · ${timeLabel} · ${regionName}${
    updatedAt ? ` · ${formatUpdated(updatedAt)}` : ""
  }`;

  if (articles.length === 0) {
    feed.innerHTML = `<li class="empty">No stories match. Try another time range, region, or clear trending / search.</li>`;
    return;
  }

  feed.innerHTML = articles
    .map((a) => {
      const cat = CATEGORY_LABELS[a.category] ?? a.category;
      const country = a.country ? countryLabel(a.country) : "";
      const desc = a.summary?.trim()
        ? `<div class="item-desc">${escapeHtml(a.summary)}</div>`
        : "";
      return `
        <li class="item">
          <h2 class="item-title">
            <a href="${escapeHtml(a.url)}" rel="noopener noreferrer" target="_blank">${escapeHtml(a.title)}</a>
          </h2>
          ${desc}
          <div class="item-foot">
            <span class="date" title="${escapeHtml(formatAbsolute(a.published_at))}">${escapeHtml(
              formatRelative(a.published_at),
            )}</span>
            <span class="feed-src">${escapeHtml(a.source)}</span>
          </div>
          <div class="item-tags">
            <span class="tag">${escapeHtml(cat)}</span>
            ${country ? `<span class="tag">${escapeHtml(country)}</span>` : ""}
          </div>
        </li>
      `;
    })
    .join("");
}

async function loadArticles(): Promise<ArticlesFile> {
  const res = await fetch("./articles.json", { cache: "no-cache" });
  if (!res.ok) {
    throw new Error(`Could not load articles (${res.status})`);
  }
  return (await res.json()) as ArticlesFile;
}

async function boot(): Promise<void> {
  renderShell();
  const feed = document.querySelector<HTMLUListElement>("#feed");
  try {
    const data = await loadArticles();
    const articles = [...(data.articles ?? [])].sort((a, b) =>
      (b.published_at || "").localeCompare(a.published_at || ""),
    );

    populateCategoryFilter(articles);
    renderTrending(articles);

    const timeSelect = document.querySelector<HTMLSelectElement>("#time");
    const regionSelect = document.querySelector<HTMLSelectElement>("#region");
    const catSelect = document.querySelector<HTMLSelectElement>("#category");
    const search = document.querySelector<HTMLInputElement>("#q");
    const trending = document.querySelector<HTMLElement>("#trending");

    const refresh = () => {
      renderFeed(filterArticles(articles), data.updated_at);
      syncTrendUi();
    };

    timeSelect?.addEventListener("change", () => {
      const id = timeSelect.value as TimeWindow;
      if (!TIME_WINDOWS.some((t) => t.id === id)) return;
      activeTime = id;
      refresh();
    });

    regionSelect?.addEventListener("change", () => {
      activeRegion = regionSelect.value;
      refresh();
    });

    catSelect?.addEventListener("change", () => {
      activeCategory = catSelect.value;
      refresh();
    });

    search?.addEventListener("input", () => {
      searchQuery = search.value;
      refresh();
    });

    trending?.addEventListener("click", (ev) => {
      const clear = (ev.target as HTMLElement).closest<HTMLButtonElement>(".trend-clear");
      if (clear) {
        activeTrend = "";
        refresh();
        return;
      }
      const word = (ev.target as HTMLElement).closest<HTMLAnchorElement>(".trend-word");
      if (!word?.dataset.word) return;
      ev.preventDefault();
      activeTrend = activeTrend === word.dataset.word ? "" : word.dataset.word;
      refresh();
    });

    refresh();
  } catch (err) {
    if (feed) {
      feed.innerHTML = `<li class="error">Feed unavailable. Run the ingest script and rebuild, or check that articles.json is deployed.</li>`;
    }
    console.error(err);
  }
}

void boot();
