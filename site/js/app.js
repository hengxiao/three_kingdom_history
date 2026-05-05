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

/* ---------- name index (people-name → id, used to wrap name links inline) ---------- */

let NAME_INDEX_CACHE = null;
async function getNameIndex() {
  if (NAME_INDEX_CACHE !== null) return NAME_INDEX_CACHE;
  try {
    const idx = await loadJSON(`${DATA_BASE}/people.json`);
    NAME_INDEX_CACHE = idx.name_index || [];
  } catch {
    NAME_INDEX_CACHE = [];
  }
  return NAME_INDEX_CACHE;
}

// id → {primary_name, courtesy_name, brief, birth_ad, death_ad} — used for
// inline tooltips on person-link spans. Lazy-loaded once per session.
let PERSON_BY_ID_CACHE = null;
async function getPersonByIdMap() {
  if (PERSON_BY_ID_CACHE !== null) return PERSON_BY_ID_CACHE;
  try {
    const idx = await loadJSON(`${DATA_BASE}/people.json`);
    const map = new Map();
    for (const p of idx.people || []) {
      map.set(p.id, p);
    }
    PERSON_BY_ID_CACHE = map;
  } catch {
    PERSON_BY_ID_CACHE = new Map();
  }
  return PERSON_BY_ID_CACHE;
}

function personTooltip(personById, person_id, llmReasoning) {
  const p = personById.get(person_id);
  if (!p) return "";
  const parts = [p.primary_name];
  if (p.courtesy_name) parts.push(`字${p.courtesy_name}`);
  if (p.birth_ad != null || p.death_ad != null) {
    parts.push(`${p.birth_ad ?? "?"}-${p.death_ad ?? "?"}`);
  }
  if (p.brief) parts.push(p.brief);
  if (llmReasoning) parts.push(`推理：${llmReasoning}`);
  return parts.join(" · ");
}

// Find non-overlapping person-name spans in `text`. `excludeRanges` is a list of
// [start, end) pairs (e.g. temporal surface positions) that the matcher must
// not enter.
function findPersonSpans(text, nameIndex, excludeRanges = []) {
  const spans = [];
  let pos = 0;
  while (pos < text.length) {
    let inExcluded = false;
    for (const [s, e] of excludeRanges) {
      if (s <= pos && pos < e) { inExcluded = true; pos = e; break; }
    }
    if (inExcluded) continue;
    let matched = null;
    for (const [name] of nameIndex) {
      if (text.startsWith(name, pos)) { matched = name; break; }
    }
    if (!matched) { pos++; continue; }
    const id = nameIndex.find(([n]) => n === matched)[1];
    const end = pos + matched.length;
    let crossesExcluded = false;
    for (const [s, e] of excludeRanges) {
      if (pos < e && end > s) { crossesExcluded = true; break; }
    }
    if (!crossesExcluded) spans.push({ at: pos, end, id, name: matched });
    pos = end;
  }
  return spans;
}

// Render plain text with person-name occurrences wrapped in <a class="person-link">.
function wrapPersonNames(text, nameIndex) {
  if (!nameIndex || nameIndex.length === 0) return escapeHTML(text);
  const spans = findPersonSpans(text, nameIndex);
  if (spans.length === 0) return escapeHTML(text);
  let out = "";
  let pos = 0;
  for (const sp of spans) {
    if (sp.at > pos) out += escapeHTML(text.slice(pos, sp.at));
    out += `<a class="person-link" href="#/people/${escapeAttr(sp.id)}">${escapeHTML(sp.name)}</a>`;
    pos = sp.end;
  }
  if (pos < text.length) out += escapeHTML(text.slice(pos));
  return out;
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
  setBreadcrumbs([{ label: "首頁", href: "#/" }, { label: "目錄", href: "#/chapters" }]);
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
    const works = [...byWork.values()];
    const tocItems = works.map(w => ({
      id: `work-${w.work_id}`,
      href: `#/chapters#work-${w.work_id}`,
      label: w.work_title,
      sub: `${w.books.reduce((n,b) => n + b.chapters.length, 0)} 卷`,
    }));
    const sections = works.map(work => `
      <section class="work-section" id="work-${escapeAttr(work.work_id)}">
        <h2 class="work-title">${escapeHTML(work.work_title)}</h2>
        ${work.books.map(book => {
          const items = book.chapters.map(ch => `
            <li>
              <a href="#/chapter/${book.id}/${ch.juan}">
                <span class="chapter-title">${escapeHTML(`卷${ch.juan}　${ch.title}`)}</span>
                <span class="chapter-meta">${ch.n_segments} 段 · 注 ${ch.n_pei} · 時間 ${ch.n_temporal}</span>
              </a>
            </li>`).join("");
          const heading = work.books.length > 1
            ? `<h3 class="book-title">${escapeHTML(book.title)}（共 ${book.chapters.length} 卷）</h3>`
            : "";
          return `<div class="book-section">${heading}<ul class="chapter-list">${items}</ul></div>`;
        }).join("")}
      </section>`).join("");
    if (!sections) {
      main.innerHTML = `<div class="error">目錄為空。請先生成數據。</div>`;
      return;
    }
    main.innerHTML = `
      <button class="toc-toggle" type="button" aria-controls="chapter-toc" aria-expanded="false">☰ 史書</button>
      <div class="chapter-layout">
        ${renderSidebarTOC("史書", tocItems)}
        <div class="chapter-main">${sections}</div>
      </div>`;
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
    const [ch, personById] = await Promise.all([loadJSON(dataUrl), getPersonByIdMap()]);
    main.innerHTML = renderChapterHTML(ch, personById);
  } catch (err) {
    main.innerHTML = `<div class="error">載入章節失敗：${escapeHTML(err.message)}</div>`;
  }
}

function bookTitleFromId(id) {
  return { wei: "魏書", shu: "蜀書", wu: "吳書", hhs: "後漢書", zztj: "資治通鑑" }[id] || id;
}

function renderChapterTOC(ch) {
  // Sidebar listing every segment so the reader can jump within a long chapter.
  // Each segment ID is `<book>.<juan>.p<N>[<suffix>]` — show just the trailing
  // p-tag fragment as a compact label (1, 2, 3a, ...). Link format mirrors the
  // seg-id anchor inside the body: `#/chapter/<book>/<juan>#<seg-id>` so it
  // routes correctly through parseHash.
  const items = ch.segments.map((s) => {
    const tail = s.id.split(".").slice(-1)[0]; // "p17", "p17a", ...
    const label = tail.replace(/^p/, "");
    return `<li><a href="#/chapter/${ch.book}/${ch.juan}#${s.id}" data-seg="${escapeAttr(s.id)}">${escapeHTML(label)}</a></li>`;
  }).join("");
  return `
    <aside id="chapter-toc" aria-label="章節目錄">
      <div class="toc-head">
        <h3>段落 (${ch.segments.length})</h3>
      </div>
      <ol class="toc-list">${items}</ol>
    </aside>`;
}

// One IntersectionObserver active at a time across all pages with a sidebar TOC.
// Disconnect when navigating to a new page so observers don't accumulate.
let currentScrollSpyIO = null;

function bindScrollSpy(itemsSelector) {
  if (currentScrollSpyIO) {
    currentScrollSpyIO.disconnect();
    currentScrollSpyIO = null;
  }
  const segs = document.querySelectorAll(itemsSelector);
  if (!segs.length) return;
  const tocLinks = new Map();
  document.querySelectorAll("#chapter-toc a[data-seg]").forEach(a => {
    tocLinks.set(a.dataset.seg, a);
  });
  if (!tocLinks.size) return;

  // Highlight the segment whose top is closest to (and not below) the
  // viewport's middle band. Maintain it in `state.activeId` and update only
  // on transitions to keep DOM writes minimal.
  const visibleRatios = new Map();
  let activeId = null;

  const updateActive = () => {
    let bestId = null;
    let bestRatio = 0;
    for (const [id, ratio] of visibleRatios) {
      if (ratio > bestRatio) { bestRatio = ratio; bestId = id; }
    }
    if (!bestId) {
      // Nothing intersecting — fall back to the topmost segment above viewport bottom.
      let topId = null, topY = -Infinity;
      for (const seg of segs) {
        const r = seg.getBoundingClientRect();
        if (r.top < window.innerHeight && r.top > topY) {
          topY = r.top;
          topId = seg.id;
        }
      }
      bestId = topId;
    }
    if (bestId === activeId) return;
    if (activeId) {
      const prev = tocLinks.get(activeId);
      if (prev) prev.classList.remove("active");
    }
    activeId = bestId;
    if (activeId) {
      const cur = tocLinks.get(activeId);
      if (cur) {
        cur.classList.add("active");
        // Keep the active link in view INSIDE the sidebar without scrolling
        // the page. Manual scrollTop is safer than scrollIntoView, which on
        // narrow layouts (where the sidebar is inline) would jump the page.
        const aside = document.getElementById("chapter-toc");
        if (aside && aside.scrollHeight > aside.clientHeight + 4) {
          const aRect = aside.getBoundingClientRect();
          const cRect = cur.getBoundingClientRect();
          if (cRect.top < aRect.top || cRect.bottom > aRect.bottom) {
            aside.scrollTop = cur.offsetTop - aside.clientHeight / 2;
          }
        }
      }
    }
  };

  currentScrollSpyIO = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) visibleRatios.set(e.target.id, e.intersectionRatio);
      else visibleRatios.delete(e.target.id);
    }
    updateActive();
  }, {
    rootMargin: "-15% 0px -55% 0px",
    threshold: [0, 0.25, 0.5, 0.75, 1],
  });
  for (const seg of segs) currentScrollSpyIO.observe(seg);
  updateActive();
}

// Generic sidebar TOC builder. `items` is a list of {id, label, sub} where
// `id` matches the DOM element's id attribute on the page. Output mirrors
// the chapter TOC so existing CSS + hamburger work unchanged.
function renderSidebarTOC(title, items) {
  if (!items || !items.length) return "";
  const lis = items.map(it => `
    <li><a href="${escapeAttr(it.href)}" data-seg="${escapeAttr(it.id)}">
      <span class="toc-label">${escapeHTML(it.label)}</span>
      ${it.sub ? `<span class="toc-sub">${escapeHTML(it.sub)}</span>` : ""}
    </a></li>`).join("");
  return `
    <aside id="chapter-toc" aria-label="${escapeAttr(title)}">
      <div class="toc-head"><h3>${escapeHTML(title)} (${items.length})</h3></div>
      <ol class="toc-list toc-list--rich">${lis}</ol>
    </aside>`;
}

function renderChapterHTML(ch, personById) {
  const warns = (ch.parse_warnings && ch.parse_warnings.length) ? `
    <div class="parse-warnings">
      <strong>解析警告</strong>（多半因 Wikisource 標記不平衡，已盡力恢復）：
      <ul>${ch.parse_warnings.map(w => `<li>${escapeHTML(w)}</li>`).join("")}</ul>
    </div>` : "";

  const segments = ch.segments.map(s => {
    const r = renderSegmentText(s, personById);
    return `
    <div class="segment" id="${s.id}">
      <div class="seg-id"><a href="#/chapter/${ch.book}/${ch.juan}#${s.id}" title="複製鏈接">${escapeHTML(s.id.split('.').slice(-1)[0])}</a></div>
      <div class="seg-text">${r.textHTML}</div>
      ${r.notesHTML}
    </div>`;
  }).join("");

  return `
    <button class="toc-toggle" type="button" aria-label="開合段落目錄" aria-controls="chapter-toc" aria-expanded="false">☰ 段落</button>
    <div class="chapter-layout">
      ${renderChapterTOC(ch)}
      <div class="chapter-main">
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
      </div>
    </div>
  `;
}

function renderChapterNav(ch) {
  const prev = ch.juan > 1 ? `<a href="#/chapter/${ch.book}/${ch.juan - 1}">◄ 卷${ch.juan - 1}</a>` : "<span></span>";
  // We don't know the upper bound per book here; just show prev if any.
  // Next link rendered unconditionally — if it's invalid, the user lands on a load-fail page they can navigate back from.
  const next = `<a href="#/chapter/${ch.book}/${ch.juan + 1}">卷${ch.juan + 1} ►</a>`;
  const home = `<a href="#/chapters">▲ 目錄</a>`;
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
function renderSegmentText(seg, personById) {
  const text = seg.text;
  const peis = seg.annotations.filter(a => a.type !== "temporal" && a.type !== "person");
  const temporals = seg.annotations.filter(a => a.type === "temporal");
  const personAnns = seg.annotations.filter(a => a.type === "person");
  const peiNum = new Map();
  peis.forEach((a, i) => peiNum.set(a.id, i + 1));
  const tempNum = new Map();
  temporals.forEach((a, i) => tempNum.set(a.id, i + 1));

  // Person spans come from build-time annotations (tools/extract_persons.py).
  // Skip any that overlap a temporal surface — temporal already wraps that range
  // with an <a> tag, and nesting <a>s produces invalid HTML.
  const tempRanges = temporals.map(a => [a.at, a.at + a.length]);
  const personSpans = personAnns
    .map(a => ({
      at: a.at, end: a.at + a.length, id: a.person_id, name: a.text,
      reasoning: a.reasoning || "",
    }))
    .filter(sp => !tempRanges.some(([s, e]) => sp.at < e && sp.end > s));

  const events = [];
  for (const a of temporals) {
    events.push({ pos: a.at, prio: 0, kind: "temp_open", ann: a });
    events.push({ pos: a.at + a.length, prio: 5, kind: "temp_close", ann: a });
  }
  for (const sp of personSpans) {
    events.push({ pos: sp.at, prio: 1, kind: "person_open", span: sp });
    events.push({ pos: sp.end, prio: 4, kind: "person_close", span: sp });
  }
  for (const a of peis) {
    events.push({ pos: a.at, prio: 3, kind: "pei_marker", ann: a });
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
      const lowConf = typeof ev.ann.confidence === "number" && ev.ann.confidence < 0.7;
      let cls = isRel ? "temporal temporal--relative" : "temporal";
      if (lowConf) cls += " temporal--low-confidence";
      const tooltip = escapeAttr(temporalTitle(ev.ann) + " — 點擊跳到時間軸");
      const href = ev.ann.year_ad != null ? `#/timeline/${ev.ann.year_ad}` : "";
      out += `<a class="temporal-link" href="${href}" title="${tooltip}"><span class="${cls}" data-resolution="${escapeAttr(ev.ann.resolution || "absolute")}">`;
    } else if (ev.kind === "temp_close") {
      const isRel = ev.ann.kind === "relative";
      const lowConf = typeof ev.ann.confidence === "number" && ev.ann.confidence < 0.7;
      let adCls = isRel ? "temporal-ad temporal-ad--relative" : "temporal-ad";
      if (lowConf) adCls += " temporal-ad--low-confidence";
      const adText = formatAD(ev.ann) + (lowConf ? "?" : "");
      out += `</span><span class="${adCls}">${adText}</span></a>`;
    } else if (ev.kind === "person_open") {
      const tip = personById ? personTooltip(personById, ev.span.id, ev.span.reasoning) : "";
      const titleAttr = tip ? ` title="${escapeAttr(tip)}"` : "";
      out += `<a class="person-link" href="#/people/${escapeAttr(ev.span.id)}"${titleAttr}>`;
    } else if (ev.kind === "person_close") {
      out += `</a>`;
    } else if (ev.kind === "pei_marker") {
      const n = peiNum.get(ev.ann.id);
      // Plain <sup> with data-anchor — no nested <a> so it can sit inside the
      // surrounding person-link / temporal-link without producing invalid HTML.
      // The delegated click handler intercepts the click and scrolls to the note.
      out += `<sup class="pei-ref" data-anchor="${escapeAttr(ev.ann.id)}-note" tabindex="0" role="button" aria-label="跳到注${n}">[${n}]</sup>`;
    }
  }
  if (pos < text.length) out += escapeHTML(text.slice(pos));

  // 注解块:
  //   - 裴注 / 李賢注 (commentary on the text, full content)
  //   - 相對時間注解 ONLY — explains how a 是歲/明年/二月 etc. resolves to AD.
  //     Absolute-time annotations show their AD year inline (after the surface),
  //     so the duplicate note is suppressed.
  //   - 人名注解 hover-only (tooltip) — not in the notes block.
  const relativeTemporals = temporals.filter(a => a.kind === "relative");
  let notesHTML = "";
  if (peis.length || relativeTemporals.length) {
    const peiItems = peis.map((a, i) => `
      <li class="note-item note-commentary note-${a.type}" id="${a.id}-note">
        <span class="note-marker">[${i + 1}]</span>
        <span class="note-text">${escapeHTML(a.text)}</span>
      </li>`).join("");
    const tempItems = relativeTemporals.map((a, i) => {
      const lowConf = typeof a.confidence === "number" && a.confidence < 0.7;
      const lowConfBadge = lowConf
        ? '<span class="note-low-conf" title="本段為倒敘，無段內絕對年號可定錨；AD 為估計值">⚠ 置信度低</span>'
        : "";
      const adSuffix = lowConf ? "?" : "";
      return `
      <li class="note-item note-temporal${lowConf ? " note-temporal--low-conf" : ""}">
        <span class="note-marker">時${i + 1}</span>
        <span class="note-text">
          「${escapeHTML(a.text)}」 = 公元 ${a.year_ad}${adSuffix} 年${a.month_ordinal ? `（農曆 ${a.month_ordinal} 月）` : ""}
          ${lowConfBadge}
          ${a.reasoning ? `<span class="note-reasoning">推理：${escapeHTML(a.reasoning)}</span>` : ""}
        </span>
      </li>`;
    }).join("");
    notesHTML = `<ul class="notes">${peiItems}${tempItems}</ul>`;
  }

  return { textHTML: out, notesHTML };
}

function temporalTitle(a) {
  const month = a.month_chinese ? `（${a.month_chinese}）` : "";
  const eraStr = a.era ? `${a.era}${a.era_year === 1 ? "元" : a.era_year}年${month}` : a.text;
  const tail = a.kind === "relative" ? `（相對：${a.resolution || ""}）` : "";
  const head = `${eraStr} = 公元 ${a.year_ad} 年 ${tail}`.trim();
  const lowConf = typeof a.confidence === "number" && a.confidence < 0.7;
  const confNote = lowConf
    ? "\n⚠ 置信度低：本段為「初/先是」開頭的倒敘，無段內絕對年號可定錨；此處 AD 沿用前文敘事年，可能不準確。"
    : "";
  return (a.reasoning ? `${head}\n推理：${a.reasoning}` : head) + confNote;
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
  setBreadcrumbs([{ label: "首頁", href: "#/" }, { label: "時間軸", href: "#/timeline" }]);
  const main = $("#content");
  main.innerHTML = "載入時間軸中…";
  try {
    const tl = await loadTimeline();
    // Group years by decade so the sidebar TOC is navigable for ~210 years.
    const byDecade = new Map();
    for (const y of tl.years) {
      const dec = Math.floor(y.year_ad / 10) * 10;
      if (!byDecade.has(dec)) byDecade.set(dec, []);
      byDecade.get(dec).push(y);
    }
    const decades = [...byDecade.entries()].sort((a, b) => a[0] - b[0]);
    const tocItems = decades.map(([dec, ys]) => ({
      id: `decade-${dec}`,
      href: `#/timeline#decade-${dec}`,
      label: `${dec}–${dec + 9}`,
      sub: `${ys.length} 年 · ${ys.reduce((n, y) => n + y.n_events, 0)} 條`,
    }));
    const decadeSections = decades.map(([dec, ys]) => `
      <section class="timeline-decade" id="decade-${dec}">
        <h3 class="timeline-decade-head">${dec}–${dec + 9} 年代</h3>
        <div class="timeline-years">${
          ys.map(y => `
            <a class="timeline-year-card" href="#/timeline/${y.year_ad}">
              <div class="year-ad">公元 ${y.year_ad} 年</div>
              <div class="year-labels">${
                y.labels.length
                  ? y.labels.map(l => escapeHTML(l.label)).join("、")
                  : '<span class="muted">（無年號標籤）</span>'
              }</div>
              <div class="year-count">${y.n_events} 條</div>
            </a>`).join("")
        }</div>
      </section>`).join("");
    main.innerHTML = `
      <button class="toc-toggle" type="button" aria-controls="chapter-toc" aria-expanded="false">☰ 年代</button>
      <div class="chapter-layout">
        ${renderSidebarTOC("年代", tocItems)}
        <div class="chapter-main">
          <div class="chapter-header">
            <h2>時間軸</h2>
            <div class="chapter-sub">${tl.years.length} 個年份 ·
              ${tl.years.reduce((s,y) => s + y.n_events, 0)} 個時間錨點，跨三國志、後漢書、資治通鑑</div>
          </div>
          ${decadeSections}
        </div>
      </div>`;
  } catch (err) {
    main.innerHTML = `<div class="error">載入失敗：${escapeHTML(err.message)}</div>`;
  }
}

async function renderTimelineYear(year) {
  setBreadcrumbs([
    { label: "首頁", href: "#/" },
    { label: "時間軸", href: "#/timeline" },
    { label: `公元 ${year} 年`, href: `#/timeline/${year}` },
  ]);
  const main = $("#content");
  main.innerHTML = "載入年份中…";
  try {
    const [tl, nameIndex] = await Promise.all([loadTimeline(), getNameIndex()]);
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
    const tocItems = [...byChapter.values()].map(g => ({
      id: `tlchap-${g.meta.chapter_id.replace(/\./g, "-")}`,
      href: `#/timeline/${year}#tlchap-${g.meta.chapter_id.replace(/\./g, "-")}`,
      label: `${g.meta.book_title}·${g.meta.chapter_title}`.length > 20
        ? g.meta.chapter_title
        : `${g.meta.book_title}·${g.meta.chapter_title}`,
      sub: `${g.events.length} 條`,
    }));
    const groupsHTML = [...byChapter.values()].map(g => `
      <section class="timeline-chapter" id="tlchap-${escapeAttr(g.meta.chapter_id.replace(/\./g, "-"))}">
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
                      aria-expanded="false">${wrapPersonNames(e.snippet, nameIndex)}</button>
            </li>`).join("")
        }</ul>
      </section>`).join("");
    main.innerHTML = `
      <button class="toc-toggle" type="button" aria-controls="chapter-toc" aria-expanded="false">☰ 章節</button>
      <div class="chapter-layout">
        ${renderSidebarTOC("章節", tocItems)}
        <div class="chapter-main">
          <div class="chapter-header">
            <h2>公元 ${year} 年 <span class="muted">${labelStr}</span></h2>
            <div class="chapter-sub">${y.n_events} 條 · 涉及 ${byChapter.size} 個章節</div>
          </div>
          ${renderYearNav(tl, year)}
          ${groupsHTML}
          ${renderYearNav(tl, year)}
        </div>
      </div>`;
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

/* ---------- PEOPLE PAGES ---------- */

let PEOPLE_INDEX_CACHE = null;
async function loadPeopleIndex() {
  if (!PEOPLE_INDEX_CACHE) PEOPLE_INDEX_CACHE = await loadJSON(`${DATA_BASE}/people.json`);
  return PEOPLE_INDEX_CACHE;
}

function dateRange(p) {
  const b = p.birth_ad, d = p.death_ad;
  if (b == null && d == null) return "生卒不詳";
  if (b == null) return `?-${d}`;
  if (d == null) return `${b}-?`;
  return `${b}-${d}`;
}

async function renderPeopleIndex() {
  setBreadcrumbs([{ label: "首頁", href: "#/" }, { label: "人物志", href: "#/people" }]);
  const main = $("#content");
  main.innerHTML = "載入人物志中…";
  try {
    const idx = await loadPeopleIndex();
    // The default sort is by mention count desc. Group into bands so the
    // sidebar TOC stays usable across 388+ persons. Bands: ≥100, 50-99, 20-49,
    // 10-19, 5-9, 1-4, 0.
    const bands = [
      { id: "band-100", label: "100+ 提及", test: n => n >= 100 },
      { id: "band-50", label: "50–99 提及", test: n => n >= 50 && n < 100 },
      { id: "band-20", label: "20–49 提及", test: n => n >= 20 && n < 50 },
      { id: "band-10", label: "10–19 提及", test: n => n >= 10 && n < 20 },
      { id: "band-5", label: "5–9 提及",   test: n => n >= 5 && n < 10 },
      { id: "band-1", label: "1–4 提及",    test: n => n >= 1 && n < 5 },
      { id: "band-0", label: "尚無錨定提及", test: n => n === 0 },
    ];
    const grouped = bands.map(b => ({
      ...b,
      people: idx.people.filter(p => b.test(p.n_mentions)),
    })).filter(b => b.people.length);
    const tocItems = grouped.map(b => ({
      id: b.id,
      href: `#/people#${b.id}`,
      label: b.label,
      sub: `${b.people.length} 人`,
    }));
    const sectionsHTML = grouped.map(b => `
      <section class="people-letter-group" id="${escapeAttr(b.id)}">
        <h3 class="people-band-head">${escapeHTML(b.label)} <span class="muted">(${b.people.length})</span></h3>
        <ul class="people-list">${
          b.people.map(p => `
            <li class="person-row">
              <a href="#/people/${escapeAttr(p.id)}">
                <span class="person-name">${escapeHTML(p.primary_name)}</span>
                ${p.courtesy_name ? `<span class="person-courtesy">字${escapeHTML(p.courtesy_name)}</span>` : ""}
                <span class="person-dates">${dateRange(p)}</span>
                <span class="person-brief">${escapeHTML(p.brief || "")}</span>
                <span class="person-counts">本傳 ${p.n_bio_chapters} · 提及 ${p.n_mentions}</span>
              </a>
            </li>`).join("")
        }</ul>
      </section>`).join("");
    main.innerHTML = `
      <button class="toc-toggle" type="button" aria-controls="chapter-toc" aria-expanded="false">☰ 分組</button>
      <div class="chapter-layout">
        ${renderSidebarTOC("分組", tocItems)}
        <div class="chapter-main">
          <div class="chapter-header">
            <h2>人物志</h2>
            <div class="chapter-sub">${idx.people.length} 人，按提及次數分組</div>
          </div>
          ${sectionsHTML}
        </div>
      </div>`;
  } catch (err) {
    main.innerHTML = `<div class="error">載入失敗：${escapeHTML(err.message)}</div>`;
  }
}

async function renderPersonPage(id) {
  setBreadcrumbs([
    { label: "首頁", href: "#/" },
    { label: "人物志", href: "#/people" },
    { label: id, href: `#/people/${id}` },
  ]);
  const main = $("#content");
  main.innerHTML = "載入人物中…";
  try {
    const [p, nameIndex] = await Promise.all([
      loadJSON(`${DATA_BASE}/people/${id}.json`),
      getNameIndex(),
    ]);
    setBreadcrumbs([
      { label: "首頁", href: "#/" },
      { label: "人物志", href: "#/people" },
      { label: p.primary_name, href: `#/people/${id}` },
    ]);

    const sections = [];
    sections.push({
      id: "person-bio",
      label: "本傳",
      sub: p.bio_chapters.length ? `${p.bio_chapters.length} 卷` : "—",
      html: p.bio_chapters.length ? `
        <section class="person-section" id="person-bio">
          <h3>本傳</h3>
          <ul class="bio-chapter-list">${
            p.bio_chapters.map(b => `
              <li><a href="#/chapter/${chapterIdToRoute(b.chapter_id)}">${escapeHTML(b.book_title)} · ${escapeHTML(b.title)} <span class="muted">(${escapeHTML(b.chapter_id)})</span></a></li>
            `).join("")
          }</ul>
        </section>` : `
        <section class="person-section" id="person-bio">
          <h3>本傳</h3>
          <p class="muted">三國志 / 後漢書 中無單獨本傳，僅見於他人傳中相關段落。</p>
        </section>`,
    });

    [
      ["zztj", "資治通鑑提及", "person-zztj"],
      ["sanguozhi", "三國志他卷提及", "person-sanguozhi"],
      ["houhanshu", "後漢書他卷提及", "person-houhanshu"],
    ].forEach(([wid, label, sectionId]) => {
      const items = p.mentions_by_work[wid] || [];
      if (!items.length) return;
      const byChapter = new Map();
      for (const m of items) {
        if (!byChapter.has(m.chapter_id)) byChapter.set(m.chapter_id, { meta: m, items: [] });
        byChapter.get(m.chapter_id).items.push(m);
      }
      const groups = [...byChapter.values()].map(g => `
        <div class="person-chapter">
          <h4><a class="chapter-link" href="#/chapter/${chapterIdToRoute(g.meta.chapter_id)}">${escapeHTML(g.meta.book_title)} · ${escapeHTML(g.meta.chapter_title)} <span class="muted">(${escapeHTML(g.meta.chapter_id)})</span></a></h4>
          <ul class="person-mention-list">${
            g.items.map(m => `
              <li class="person-mention">
                <button class="event-snippet"
                        type="button"
                        data-anchor="${escapeAttr(m.anchor)}"
                        data-data-url="${escapeAttr(m.data_url)}"
                        data-snippet-original="${escapeAttr(m.snippet)}"
                        aria-expanded="false"
                        title="匹配：${escapeAttr(m.matched)}">${wrapPersonNames(m.snippet, nameIndex)}</button>
              </li>`).join("")
          }</ul>
        </div>`).join("");
      sections.push({
        id: sectionId,
        label,
        sub: `${items.length} 處`,
        html: `
          <section class="person-section" id="${sectionId}">
            <h3>${label} <span class="muted">(${items.length} 處)</span></h3>
            ${groups}
          </section>`,
      });
    });

    const tocItems = sections.map(s => ({
      id: s.id,
      href: `#/people/${id}#${s.id}`,
      label: s.label,
      sub: s.sub,
    }));

    const aliasesStr = p.aliases.length ? p.aliases.join("、") : "無";
    const otherNamesStr = p.other_names && p.other_names.length ? `<div class="person-other-names">其他名號（待上下文判別后計入）：${escapeHTML(p.other_names.join("、"))}</div>` : "";

    main.innerHTML = `
      <button class="toc-toggle" type="button" aria-controls="chapter-toc" aria-expanded="false">☰ 分節</button>
      <div class="chapter-layout">
        ${renderSidebarTOC("分節", tocItems)}
        <div class="chapter-main">
          <div class="chapter-header">
            <h2>${escapeHTML(p.primary_name)}${p.courtesy_name ? ` <span class="muted">字${escapeHTML(p.courtesy_name)}</span>` : ""}</h2>
            <div class="chapter-sub">
              ${escapeHTML(dateRange(p))} · ${escapeHTML(p.brief || "")}
            </div>
            <div class="person-aliases">搜索別名：${escapeHTML(aliasesStr)}</div>
            ${otherNamesStr}
          </div>
          ${sections.map(s => s.html).join("")}
        </div>
      </div>
    `;
  } catch (err) {
    main.innerHTML = `<div class="error">載入失敗：${escapeHTML(err.message)}</div>`;
  }
}

/* ---------- HOME PAGE ---------- */

async function renderHome() {
  setBreadcrumbs([{ label: "首頁", href: "#/" }]);
  const main = $("#content");
  // Try to surface a few stats so the home page feels alive (count of chapters,
  // years on timeline, persons in roster). Failures fall back gracefully.
  let stats = { chapters: 0, years: 0, persons: 0, mentions: 0 };
  try {
    const [idx, tl, pp] = await Promise.all([
      loadJSON(`${DATA_BASE}/index.json`).catch(() => null),
      loadJSON(`${DATA_BASE}/timeline.json`).catch(() => null),
      loadJSON(`${DATA_BASE}/people.json`).catch(() => null),
    ]);
    if (idx) stats.chapters = idx.books.reduce((n, b) => n + b.chapters.length, 0);
    if (tl) {
      stats.years = tl.years.length;
      stats.mentions = tl.years.reduce((n, y) => n + y.n_events, 0);
    }
    if (pp) stats.persons = pp.people.length;
  } catch (_) { /* ignore */ }

  main.innerHTML = `
    <section class="home-hero">
      <h2>三國志知識庫</h2>
      <p class="home-tagline">原文、注解、時間、人物 — 一個跨史書的可閱讀資料庫</p>
      <p class="home-blurb">
        資料以 <strong>三國志</strong>、<strong>後漢書</strong>（漢靈帝以後）、
        <strong>資治通鑑</strong>（卷 56–83）為基底，原文取自 Wikisource 公有領域版本，
        並依照 <code>doc/format.md</code> 規範整理。每段落自動標記公元紀年（絕對與相對皆可追溯推理過程）；
        每個人名連結至對應人物頁，旁注顯示字、生卒、簡介。
      </p>
    </section>
    <section class="home-tiles">
      <a class="home-tile" href="#/chapters">
        <div class="tile-icon">📖</div>
        <h3>原文目錄</h3>
        <p>${stats.chapters} 卷三家史書，按書分組。原文 + 裴注 / 李賢注。</p>
      </a>
      <a class="home-tile" href="#/timeline">
        <div class="tile-icon">🕒</div>
        <h3>時間軸</h3>
        <p>${stats.years} 個年份，${stats.mentions.toLocaleString()} 條時間錨點。資治通鑑首列，三國志、後漢書交錯對應。</p>
      </a>
      <a class="home-tile" href="#/people">
        <div class="tile-icon">👤</div>
        <h3>人物志</h3>
        <p>${stats.persons} 位人物，依出現次數排序。本傳 + 跨史書提及。</p>
      </a>
    </section>
    <section class="home-features">
      <h3>功能特點</h3>
      <ul>
        <li>原文 + 注解分開顯示，行動裝置上點段落展開注解。</li>
        <li>時間注解：絕對紀年顯示於正文後；相對時間 (是歲、明年、二月) 在注解區說明所依據的絕對紀年與推理過程。</li>
        <li>人名連結：滑鼠 hover 顯示字、生卒、簡介；點擊進入人物頁。</li>
        <li>段落目錄 (左側) 一鍵跳轉長卷內任意段落，行動裝置上以 ☰ 收合。</li>
      </ul>
    </section>
  `;
}


/* ---------- ROUTING ---------- */

function parseHash() {
  const h = location.hash || "#/";
  let m = h.match(/^#\/chapter\/([a-z]+)\/(\d+)(?:#(.+))?$/);
  if (m) return { route: "chapter", book: m[1], juan: parseInt(m[2], 10), seg: m[3] || null };
  m = h.match(/^#\/timeline\/(\d+)(?:#(.+))?$/);
  if (m) return { route: "timeline_year", year: parseInt(m[1], 10), anchor: m[2] || null };
  m = h.match(/^#\/timeline(?:#(.+))?$/);
  if (m) return { route: "timeline_index", anchor: m[1] || null };
  m = h.match(/^#\/people\/([a-z0-9_-]+)(?:#(.+))?$/i);
  if (m) return { route: "person", id: m[1], anchor: m[2] || null };
  m = h.match(/^#\/people(?:#(.+))?$/);
  if (m) return { route: "people_index", anchor: m[1] || null };
  m = h.match(/^#\/chapters(?:#(.+))?$/);
  if (m) return { route: "chapter_index", anchor: m[1] || null };
  if (h === "#/" || h === "" || h === "#") return { route: "home" };
  return { route: "home" };
}

// Manually scroll to an element id within the page after a route render —
// browsers don't auto-scroll for double-hash URLs like #/people#alpha-曹.
function scrollToAnchorOrTop(anchorId) {
  if (anchorId) {
    const el = document.getElementById(anchorId);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
  }
  window.scrollTo(0, 0);
}

// Track the last-rendered page so anchor-only hash changes (clicking a TOC
// link inside the current page) just scroll instead of re-fetching+re-rendering
// the chapter, which would lose scroll position and flash the page blank.
let currentRoute = null;

function sameRouteIgnoringAnchor(a, b) {
  if (!a || !b || a.route !== b.route) return false;
  if (a.route === "chapter") return a.book === b.book && a.juan === b.juan;
  if (a.route === "timeline_year") return a.year === b.year;
  if (a.route === "person") return a.id === b.id;
  // Index-style routes have no params, so same route name is enough.
  return true;
}

async function route() {
  const r = parseHash();
  // Anchor-only navigation within the same page: skip re-render.
  if (sameRouteIgnoringAnchor(currentRoute, r)) {
    scrollToAnchorOrTop(r.seg || r.anchor);
    return;
  }
  currentRoute = r;
  if (r.route === "chapter") {
    await renderChapter(r.book, r.juan);
    bindScrollSpy("#content .segment");
    scrollToAnchorOrTop(r.seg);
  } else if (r.route === "timeline_year") {
    await renderTimelineYear(r.year);
    bindScrollSpy("#content .timeline-chapter");
    scrollToAnchorOrTop(r.anchor);
  } else if (r.route === "timeline_index") {
    await renderTimelineIndex();
    bindScrollSpy("#content .timeline-decade");
    scrollToAnchorOrTop(r.anchor);
  } else if (r.route === "person") {
    await renderPersonPage(r.id);
    bindScrollSpy("#content .person-section");
    scrollToAnchorOrTop(r.anchor);
  } else if (r.route === "home") {
    await renderHome();
    window.scrollTo(0, 0);
  } else if (r.route === "chapter_index") {
    await renderIndex();
    bindScrollSpy("#content .work-section");
    scrollToAnchorOrTop(r.anchor);
  } else if (r.route === "people_index") {
    await renderPeopleIndex();
    bindScrollSpy("#content .people-letter-group");
    scrollToAnchorOrTop(r.anchor);
  } else {
    await renderHome();
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
  const nameIndex = await getNameIndex();
  const expanded = btn.getAttribute("aria-expanded") === "true";
  if (expanded) {
    btn.innerHTML = wrapPersonNames(btn.dataset.snippetOriginal, nameIndex);
    btn.setAttribute("aria-expanded", "false");
    return;
  }
  btn.textContent = "載入中…";
  try {
    const ch = await fetchAndCacheChapter(btn.dataset.dataUrl);
    const seg = ch.segments.find(s => s.id === btn.dataset.anchor);
    btn.innerHTML = seg
      ? wrapPersonNames(seg.text, nameIndex)
      : wrapPersonNames(btn.dataset.snippetOriginal, nameIndex);
    btn.setAttribute("aria-expanded", "true");
  } catch (err) {
    btn.innerHTML = wrapPersonNames(btn.dataset.snippetOriginal, nameIndex);
  }
}

function bindClicks() {
  $("#content").addEventListener("click", e => {
    // (0) hamburger toggle for the chapter sidebar TOC (visible on narrow screens).
    const tocBtn = e.target.closest(".toc-toggle");
    if (tocBtn) {
      e.preventDefault();
      const aside = document.getElementById("chapter-toc");
      const opened = aside && aside.classList.toggle("toc-open");
      tocBtn.setAttribute("aria-expanded", opened ? "true" : "false");
      return;
    }
    // Tapping a TOC link on narrow auto-closes the panel after navigation.
    const tocLink = e.target.closest("#chapter-toc a");
    if (tocLink && NARROW_MQ.matches) {
      const aside = document.getElementById("chapter-toc");
      if (aside) aside.classList.remove("toc-open");
      const btn = $(".toc-toggle");
      if (btn) btn.setAttribute("aria-expanded", "false");
      // fall through — we still want default <a> navigation
    }
    // (1) timeline snippet click — expand inline (no navigation).
    // Important: snippet detection runs BEFORE the person-link check below,
    // because a tap on a person link inside an expanded snippet should navigate.
    if (e.target.closest("a") === null) {
      const snippetBtn = e.target.closest(".event-snippet");
      if (snippetBtn) {
        e.preventDefault();
        toggleSnippet(snippetBtn);
        return;
      }
    }
    // (2) tap on a 裴注 marker [N] — scroll and flash the matching note.
    const ref = e.target.closest(".pei-ref");
    if (ref) {
      e.preventDefault();
      e.stopPropagation();   // don't bubble up to a wrapping person-link
      const seg = ref.closest(".segment");
      if (seg) seg.classList.add("notes-revealed");
      const id = ref.dataset.anchor;
      const note = id && document.getElementById(id);
      if (note) {
        note.scrollIntoView({ behavior: "smooth", block: "center" });
        note.classList.add("note-flash");
        setTimeout(() => note.classList.remove("note-flash"), 1200);
      }
      return;
    }
    // (3) tap on the 正文 (narrow only) — toggle the segment's notes.
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
