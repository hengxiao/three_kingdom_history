/* 三國志知識庫 — vanilla JS SPA. */
"use strict";

const $ = (sel, ctx = document) => ctx.querySelector(sel);
const escapeHTML = (s) => s.replace(/[&<>"']/g, c => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
));
const escapeAttr = escapeHTML;

const DATA_BASE = "data";

async function loadJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${path}`);
  return res.json();
}

function setBreadcrumbs(parts) {
  const nav = $("#breadcrumbs");
  nav.innerHTML = parts.map((p, i) => {
    const span = i < parts.length - 1
      ? `<a href="${escapeAttr(p.href)}">${escapeHTML(p.label)}</a>`
      : `<span>${escapeHTML(p.label)}</span>`;
    return span;
  }).join('<span class="sep">›</span>');
}

/* ---------- INDEX PAGE ---------- */

// Cached at first load: book_id → { work_id, title, chapters[…with data_url] }.
// Used to derive the chapter-JSON URL for arbitrary `#/chapter/<book>/<juan>` links.
let INDEX_CACHE = null;

async function loadIndex() {
  if (!INDEX_CACHE) INDEX_CACHE = await loadJSON(`${DATA_BASE}/index.json`);
  return INDEX_CACHE;
}

async function renderIndex() {
  setBreadcrumbs([{ label: "目錄", href: "#/" }]);
  const main = $("#content");
  main.innerHTML = "載入目錄中…";
  try {
    const idx = await loadIndex();
    // Group books by work_id to render each work as its own section.
    const byWork = new Map();
    for (const book of idx.books) {
      const wid = book.work_id || "sanguozhi";
      if (!byWork.has(wid)) byWork.set(wid, { work_id: wid, work_title: book.work_title || wid, books: [] });
      byWork.get(wid).books.push(book);
    }
    const html = [...byWork.values()].map(work => `
      <section class="work-section">
        <h2 class="work-title">${escapeHTML(work.work_title)}</h2>
        ${work.books.map(book => {
          const items = book.chapters.map(ch => `
            <li>
              <a href="#/chapter/${book.id}/${ch.juan}">
                <span class="chapter-title">${escapeHTML(`卷${ch.juan}　${ch.title}`)}</span>
                <span class="chapter-meta">${ch.n_segments} 段 · 注 ${ch.n_pei} · 時間 ${ch.n_temporal}</span>
              </a>
            </li>`).join("");
          // For multi-book works (sanguozhi), title each book; for single-book (hhs), skip the
          // redundant book heading.
          const heading = work.books.length > 1
            ? `<h3 class="book-title">${escapeHTML(book.title)}（共 ${book.chapters.length} 卷）</h3>`
            : "";
          return `<div class="book-section">${heading}<ul class="chapter-list">${items}</ul></div>`;
        }).join("")}
      </section>`).join("");
    main.innerHTML = html || `<div class="error">目錄為空。請先生成數據。</div>`;
  } catch (err) {
    main.innerHTML = `<div class="error">載入失敗：${escapeHTML(err.message)}</div>`;
  }
}

/* ---------- CHAPTER PAGE ---------- */

async function renderChapter(book, juan) {
  // Look up the precomputed data_url from the index.
  const idx = await loadIndex();
  const bookEntry = idx.books.find(b => b.id === book);
  const chapterEntry = bookEntry && bookEntry.chapters.find(c => Number(c.juan) === Number(juan));
  const dataUrl = chapterEntry
    ? chapterEntry.data_url
    : `${DATA_BASE}/${bookEntry?.work_id || "sanguozhi"}/${book}/${String(juan).padStart(2, "0")}.json`;

  setBreadcrumbs([
    { label: "目錄", href: "#/" },
    { label: `${bookTitleFromId(book)}卷${juan}`, href: `#/chapter/${book}/${juan}` },
  ]);
  const main = $("#content");
  main.innerHTML = "載入章節中…";
  try {
    const ch = await loadJSON(dataUrl);
    main.innerHTML = renderChapterHTML(ch);
  } catch (err) {
    main.innerHTML = `<div class="error">載入章節失敗：${escapeHTML(err.message)}</div>`;
  }
}

function bookTitleFromId(id) {
  return { wei: "魏書", shu: "蜀書", wu: "吳書", hhs: "後漢書", zztj: "資治通鑑" }[id] || id;
}

function renderChapterHTML(ch) {
  const warns = (ch.parse_warnings && ch.parse_warnings.length) ? `
    <div class="parse-warnings">
      <strong>解析警告</strong>（多半因 Wikisource 標記不平衡，已盡力恢復）：
      <ul>${ch.parse_warnings.map(w => `<li>${escapeHTML(w)}</li>`).join("")}</ul>
    </div>` : "";

  const segments = ch.segments.map(s => {
    const r = renderSegmentText(s);
    return `
    <div class="segment" id="${s.id}">
      <div class="seg-id"><a href="#/chapter/${ch.book}/${ch.juan}#${s.id}" title="複製鏈接">${escapeHTML(s.id.split('.').slice(-1)[0])}</a></div>
      <div class="seg-text">${r.textHTML}</div>
      ${r.notesHTML}
    </div>`;
  }).join("");

  return `
    <div class="chapter-header">
      <h2>${escapeHTML(ch.title)}</h2>
      <div class="chapter-sub">
        ${escapeHTML(ch.book_title)}卷${ch.juan} ·
        ${escapeHTML(ch.author || "")} 撰 ·
        <a href="${escapeAttr(ch.source.url)}" target="_blank" rel="noopener">原始 ${escapeHTML(ch.source.id)} 頁</a>
        · ${ch.n_segments} 段
      </div>
    </div>
    ${warns}
    ${renderChapterNav(ch)}
    <article>${segments}</article>
    ${renderChapterNav(ch)}
  `;
}

function renderChapterNav(ch) {
  const prev = ch.juan > 1 ? `<a href="#/chapter/${ch.book}/${ch.juan - 1}">◄ 卷${ch.juan - 1}</a>` : "<span></span>";
  // We don't know the upper bound per book here; just show prev if any.
  // Next link rendered unconditionally — if it's invalid, the user lands on a load-fail page they can navigate back from.
  const next = `<a href="#/chapter/${ch.book}/${ch.juan + 1}">卷${ch.juan + 1} ►</a>`;
  const home = `<a href="#/">▲ 目錄</a>`;
  return `<div class="chapter-nav">${prev}${home}${next}</div>`;
}

/**
 * Render the segment as separated 正文 + 注解 block.
 * - 正文: clean text. Temporal surfaces are kept highlighted in-flow (they are
 *   short and contextual). 裴注 are NOT inlined — only a small numbered marker
 *   `[N]` appears at the anchor position.
 * - 注解块 (notes): a footnote-style list rendered below 正文, listing every
 *   annotation for the segment.
 *
 * Annotations carry `at` (offset into seg.text) and `length` (0 for point
 * insertions like 裴注; >0 for ranges like temporal surface highlights).
 *
 * Returns { textHTML, notesHTML }.
 */
function renderSegmentText(seg) {
  const text = seg.text;
  // Collect pei + temporal in document order to assign stable display numbers.
  const peis = seg.annotations.filter(a => a.type !== "temporal");
  const temporals = seg.annotations.filter(a => a.type === "temporal");
  const peiNum = new Map();
  peis.forEach((a, i) => peiNum.set(a.id, i + 1));
  const tempNum = new Map();
  temporals.forEach((a, i) => tempNum.set(a.id, i + 1));

  const events = []; // { pos, prio, kind, ann }
  for (const a of temporals) {
    events.push({ pos: a.at, prio: 0, kind: "temp_open", ann: a });
    events.push({ pos: a.at + a.length, prio: 1, kind: "temp_close", ann: a });
  }
  for (const a of peis) {
    events.push({ pos: a.at, prio: 2, kind: "pei_marker", ann: a });
  }
  events.sort((a, b) => a.pos - b.pos || a.prio - b.prio);

  let out = "";
  let pos = 0;
  for (const ev of events) {
    if (ev.pos > pos) {
      out += escapeHTML(text.slice(pos, ev.pos));
      pos = ev.pos;
    }
    if (ev.kind === "temp_open") {
      const isRel = ev.ann.kind === "relative";
      const cls = isRel ? "temporal temporal--relative" : "temporal";
      const tooltip = escapeAttr(temporalTitle(ev.ann) + " — 點擊跳到時間軸");
      const href = ev.ann.year_ad != null ? `#/timeline/${ev.ann.year_ad}` : "";
      // The wrapping anchor lets a click on either the highlighted surface OR
      // the trailing AD badge jump to that year on the timeline. Open emits
      // the anchor + the surface-wrapping span; close closes both, after
      // emitting the AD badge.
      out += `<a class="temporal-link" href="${href}" title="${tooltip}"><span class="${cls}" data-resolution="${escapeAttr(ev.ann.resolution || "absolute")}">`;
    } else if (ev.kind === "temp_close") {
      const isRel = ev.ann.kind === "relative";
      const adCls = isRel ? "temporal-ad temporal-ad--relative" : "temporal-ad";
      out += `</span><span class="${adCls}">${formatAD(ev.ann)}</span></a>`;
    } else if (ev.kind === "pei_marker") {
      const n = peiNum.get(ev.ann.id);
      out += `<sup class="pei-ref" data-ann="${escapeAttr(ev.ann.id)}"><a href="#${ev.ann.id}-note">[${n}]</a></sup>`;
    }
  }
  if (pos < text.length) out += escapeHTML(text.slice(pos));

  // 注解块: 注 列表 (pei + lixian) + temporal 列表（如有）
  let notesHTML = "";
  if (peis.length || temporals.length) {
    const peiItems = peis.map((a, i) => `
      <li class="note-item note-commentary note-${a.type}" id="${a.id}-note">
        <span class="note-marker">[${i + 1}]</span>
        <span class="note-text">${escapeHTML(a.text)}</span>
      </li>`).join("");
    const tempItems = temporals.map((a, i) => `
      <li class="note-item note-temporal">
        <span class="note-marker">時${i + 1}</span>
        <span class="note-text">
          ${escapeHTML(a.text)} = 公元 ${a.year_ad} 年${a.month_ordinal ? `（農曆 ${a.month_ordinal} 月）` : ""}
          ${a.reasoning ? `<span class="note-reasoning">推理：${escapeHTML(a.reasoning)}</span>` : ""}
        </span>
      </li>`).join("");
    notesHTML = `<ul class="notes">${peiItems}${tempItems}</ul>`;
  }

  return { textHTML: out, notesHTML };
}

function temporalTitle(a) {
  const month = a.month_chinese ? `（${a.month_chinese}）` : "";
  const eraStr = a.era ? `${a.era}${a.era_year === 1 ? "元" : a.era_year}年${month}` : a.text;
  const tail = a.kind === "relative" ? `（相對：${a.resolution || ""}）` : "";
  const head = `${eraStr} = 公元 ${a.year_ad} 年 ${tail}`.trim();
  return a.reasoning ? `${head}\n推理：${a.reasoning}` : head;
}

function formatAD(a) {
  const m = a.month_ordinal ? `/${a.month_ordinal}月` : "";
  return `AD${a.year_ad}${m}`;
}

/* ---------- TIMELINE PAGE ---------- */

let TIMELINE_CACHE = null;
async function loadTimeline() {
  if (!TIMELINE_CACHE) TIMELINE_CACHE = await loadJSON(`${DATA_BASE}/timeline.json`);
  return TIMELINE_CACHE;
}

async function renderTimelineIndex() {
  setBreadcrumbs([{ label: "目錄", href: "#/" }, { label: "時間軸", href: "#/timeline" }]);
  const main = $("#content");
  main.innerHTML = "載入時間軸中…";
  try {
    const tl = await loadTimeline();
    const html = `
      <div class="chapter-header">
        <h2>時間軸</h2>
        <div class="chapter-sub">${tl.years.length} 個年份 ·
          ${tl.years.reduce((s,y) => s + y.n_events, 0)} 個時間錨點，跨三國志與後漢書</div>
      </div>
      <div class="timeline-years">${
        tl.years.map(y => `
          <a class="timeline-year-card" href="#/timeline/${y.year_ad}">
            <div class="year-ad">公元 ${y.year_ad} 年</div>
            <div class="year-labels">${
              y.labels.length
                ? y.labels.map(l => escapeHTML(l.label)).join("、")
                : '<span class="muted">（無年號標籤）</span>'
            }</div>
            <div class="year-count">${y.n_events} 條</div>
          </a>`).join("")
      }</div>`;
    main.innerHTML = html;
  } catch (err) {
    main.innerHTML = `<div class="error">載入失敗：${escapeHTML(err.message)}</div>`;
  }
}

async function renderTimelineYear(year) {
  setBreadcrumbs([
    { label: "目錄", href: "#/" },
    { label: "時間軸", href: "#/timeline" },
    { label: `公元 ${year} 年`, href: `#/timeline/${year}` },
  ]);
  const main = $("#content");
  main.innerHTML = "載入年份中…";
  try {
    const tl = await loadTimeline();
    const y = tl.years.find(x => Number(x.year_ad) === Number(year));
    if (!y) {
      main.innerHTML = `<div class="error">公元 ${year} 年沒有時間錨點。</div>`;
      return;
    }
    // Group events by chapter for cleaner reading.
    const byChapter = new Map();
    for (const e of y.events) {
      if (!byChapter.has(e.chapter_id)) byChapter.set(e.chapter_id, { meta: e, events: [] });
      byChapter.get(e.chapter_id).events.push(e);
    }
    const labelStr = y.labels.length
      ? y.labels.map(l => escapeHTML(l.label)).join("、")
      : "（無年號標籤）";
    const groupsHTML = [...byChapter.values()].map(g => `
      <section class="timeline-chapter">
        <h3>
          <a class="chapter-link" href="#/chapter/${chapterIdToRoute(g.meta.chapter_id)}">${escapeHTML(g.meta.book_title)} · ${escapeHTML(g.meta.chapter_title)}</a>
          <span class="muted">(${escapeHTML(g.meta.chapter_id)})</span>
        </h3>
        <ul class="timeline-event-list">${
          g.events.map(e => `
            <li class="timeline-event ${e.kind === "relative" ? "relative" : "absolute"}">
              <span class="event-surface">${escapeHTML(e.surface)}</span>
              <button class="event-snippet"
                      type="button"
                      data-anchor="${escapeAttr(e.anchor)}"
                      data-data-url="${escapeAttr(e.data_url)}"
                      data-snippet-original="${escapeAttr(e.snippet)}"
                      aria-expanded="false">${escapeHTML(e.snippet)}</button>
            </li>`).join("")
        }</ul>
      </section>`).join("");
    main.innerHTML = `
      <div class="chapter-header">
        <h2>公元 ${year} 年 <span class="muted">${labelStr}</span></h2>
        <div class="chapter-sub">${y.n_events} 條 · 涉及 ${byChapter.size} 個章節</div>
      </div>
      ${renderYearNav(tl, year)}
      ${groupsHTML}
      ${renderYearNav(tl, year)}`;
  } catch (err) {
    main.innerHTML = `<div class="error">載入失敗：${escapeHTML(err.message)}</div>`;
  }
}

function renderYearNav(tl, year) {
  const idx = tl.years.findIndex(x => Number(x.year_ad) === Number(year));
  const prev = idx > 0 ? tl.years[idx - 1] : null;
  const next = idx >= 0 && idx < tl.years.length - 1 ? tl.years[idx + 1] : null;
  return `<div class="chapter-nav">
    ${prev ? `<a href="#/timeline/${prev.year_ad}">◄ 公元 ${prev.year_ad} 年</a>` : "<span></span>"}
    <a href="#/timeline">▲ 時間軸總覽</a>
    ${next ? `<a href="#/timeline/${next.year_ad}">公元 ${next.year_ad} 年 ►</a>` : "<span></span>"}
  </div>`;
}

function chapterIdToRoute(chapter_id) {
  // "wei.1" → "wei/1"; "hhs.8" → "hhs/8"
  return chapter_id.replace(".", "/");
}

/* ---------- ROUTING ---------- */

function parseHash() {
  const h = location.hash || "#/";
  let m = h.match(/^#\/chapter\/([a-z]+)\/(\d+)(?:#(.+))?$/);
  if (m) return { route: "chapter", book: m[1], juan: parseInt(m[2], 10), seg: m[3] || null };
  m = h.match(/^#\/timeline\/(\d+)$/);
  if (m) return { route: "timeline_year", year: parseInt(m[1], 10) };
  if (h === "#/timeline" || h.startsWith("#/timeline/")) return { route: "timeline_index" };
  return { route: "index" };
}

async function route() {
  const r = parseHash();
  if (r.route === "chapter") {
    await renderChapter(r.book, r.juan);
    if (r.seg) {
      const el = document.getElementById(r.seg);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      window.scrollTo(0, 0);
    }
  } else if (r.route === "timeline_year") {
    await renderTimelineYear(r.year);
    window.scrollTo(0, 0);
  } else if (r.route === "timeline_index") {
    await renderTimelineIndex();
    window.scrollTo(0, 0);
  } else {
    await renderIndex();
    window.scrollTo(0, 0);
  }
}

// Single delegated click handler bound once for the page lifetime. Same DOM
// across all responsive layouts; CSS decides whether the notes block is
// side-by-side, stacked below, or hidden-until-revealed (narrow).
const NARROW_MQ = window.matchMedia("(max-width: 600px)");

// Chapter JSON cache for lazy snippet expansion on the timeline.
const CHAPTER_CACHE = new Map();
async function fetchAndCacheChapter(url) {
  if (CHAPTER_CACHE.has(url)) return CHAPTER_CACHE.get(url);
  const promise = loadJSON(url);
  CHAPTER_CACHE.set(url, promise);
  try {
    const ch = await promise;
    CHAPTER_CACHE.set(url, ch);   // replace promise with resolved chapter
    return ch;
  } catch (err) {
    CHAPTER_CACHE.delete(url);    // don't cache failures
    throw err;
  }
}

async function toggleSnippet(btn) {
  const expanded = btn.getAttribute("aria-expanded") === "true";
  if (expanded) {
    btn.textContent = btn.dataset.snippetOriginal;
    btn.setAttribute("aria-expanded", "false");
    return;
  }
  const previous = btn.textContent;
  btn.textContent = "載入中…";
  try {
    const ch = await fetchAndCacheChapter(btn.dataset.dataUrl);
    const seg = ch.segments.find(s => s.id === btn.dataset.anchor);
    btn.textContent = seg ? seg.text : previous;
    btn.setAttribute("aria-expanded", "true");
  } catch (err) {
    btn.textContent = previous;
  }
}

function bindClicks() {
  $("#content").addEventListener("click", e => {
    // (1) timeline snippet click — expand inline (no navigation).
    const snippetBtn = e.target.closest(".event-snippet");
    if (snippetBtn) {
      e.preventDefault();
      toggleSnippet(snippetBtn);
      return;
    }
    // (2) tap on a 裴注 marker [N] — scroll and flash the matching note.
    const ref = e.target.closest(".pei-ref a");
    if (ref) {
      e.preventDefault();
      const seg = ref.closest(".segment");
      // On narrow, the notes block is collapsed by default — reveal it first
      // so scrollIntoView has a visible target.
      if (seg) seg.classList.add("notes-revealed");
      const id = ref.getAttribute("href").replace(/^#/, "");
      const note = document.getElementById(id);
      if (note) {
        note.scrollIntoView({ behavior: "smooth", block: "center" });
        note.classList.add("note-flash");
        setTimeout(() => note.classList.remove("note-flash"), 1200);
      }
      return;
    }
    // (3) tap on the 正文 (narrow only) — toggle the segment's notes.
    // Skip when the tap landed on a real link (e.g. a temporal-link inside the
    // text); the link's own navigation should not also toggle the notes.
    if (NARROW_MQ.matches && !e.target.closest("a")) {
      const segText = e.target.closest(".seg-text");
      if (segText) {
        const seg = segText.closest(".segment");
        if (seg) seg.classList.toggle("notes-revealed");
      }
    }
  });
}

/* ---------- TOGGLES ---------- */

function applyTogglesFromUI() {
  document.body.classList.toggle("hide-pei", !$("#show-pei").checked);
  document.body.classList.toggle("hide-temporal", !$("#show-temporal").checked);
}

window.addEventListener("hashchange", route);
document.addEventListener("DOMContentLoaded", () => {
  $("#show-pei").addEventListener("change", applyTogglesFromUI);
  $("#show-temporal").addEventListener("change", applyTogglesFromUI);
  applyTogglesFromUI();
  bindClicks();
  route();
});
