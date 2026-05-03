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
    .map(a => ({ at: a.at, end: a.at + a.length, id: a.person_id, name: a.text }))
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
      const cls = isRel ? "temporal temporal--relative" : "temporal";
      const tooltip = escapeAttr(temporalTitle(ev.ann) + " — 點擊跳到時間軸");
      const href = ev.ann.year_ad != null ? `#/timeline/${ev.ann.year_ad}` : "";
      out += `<a class="temporal-link" href="${href}" title="${tooltip}"><span class="${cls}" data-resolution="${escapeAttr(ev.ann.resolution || "absolute")}">`;
    } else if (ev.kind === "temp_close") {
      const isRel = ev.ann.kind === "relative";
      const adCls = isRel ? "temporal-ad temporal-ad--relative" : "temporal-ad";
      out += `</span><span class="${adCls}">${formatAD(ev.ann)}</span></a>`;
    } else if (ev.kind === "person_open") {
      out += `<a class="person-link" href="#/people/${escapeAttr(ev.span.id)}">`;
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
                      aria-expanded="false">${wrapPersonNames(e.snippet, nameIndex)}</button>
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
  setBreadcrumbs([{ label: "目錄", href: "#/" }, { label: "人物志", href: "#/people" }]);
  const main = $("#content");
  main.innerHTML = "載入人物志中…";
  try {
    const idx = await loadPeopleIndex();
    const html = `
      <div class="chapter-header">
        <h2>人物志</h2>
        <div class="chapter-sub">${idx.people.length} 人，按提及次數排序</div>
      </div>
      <ul class="people-list">${
        idx.people.map(p => `
          <li class="person-row">
            <a href="#/people/${escapeAttr(p.id)}">
              <span class="person-name">${escapeHTML(p.primary_name)}</span>
              ${p.courtesy_name ? `<span class="person-courtesy">字${escapeHTML(p.courtesy_name)}</span>` : ""}
              <span class="person-dates">${dateRange(p)}</span>
              <span class="person-brief">${escapeHTML(p.brief || "")}</span>
              <span class="person-counts">本傳 ${p.n_bio_chapters} · 提及 ${p.n_mentions}</span>
            </a>
          </li>`).join("")
      }</ul>`;
    main.innerHTML = html;
  } catch (err) {
    main.innerHTML = `<div class="error">載入失敗：${escapeHTML(err.message)}</div>`;
  }
}

async function renderPersonPage(id) {
  setBreadcrumbs([
    { label: "目錄", href: "#/" },
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
      { label: "目錄", href: "#/" },
      { label: "人物志", href: "#/people" },
      { label: p.primary_name, href: `#/people/${id}` },
    ]);

    const bioHTML = p.bio_chapters.length ? `
      <section class="person-section">
        <h3>本傳</h3>
        <ul class="bio-chapter-list">${
          p.bio_chapters.map(b => `
            <li><a href="#/chapter/${chapterIdToRoute(b.chapter_id)}">${escapeHTML(b.book_title)} · ${escapeHTML(b.title)} <span class="muted">(${escapeHTML(b.chapter_id)})</span></a></li>
          `).join("")
        }</ul>
      </section>` : `
      <section class="person-section">
        <h3>本傳</h3>
        <p class="muted">三国志 / 后汉书 中無單獨本傳，仅見於他人傳中相關段落。</p>
      </section>`;

    const workSections = [
      ["zztj", "資治通鑑提及"],
      ["sanguozhi", "三國志他卷提及"],
      ["houhanshu", "後漢書他卷提及"],
    ].map(([wid, label]) => {
      const items = p.mentions_by_work[wid] || [];
      if (!items.length) return "";
      // Group by chapter
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
      return `
        <section class="person-section">
          <h3>${label} <span class="muted">(${items.length} 處)</span></h3>
          ${groups}
        </section>`;
    }).filter(Boolean).join("");

    const aliasesStr = p.aliases.length ? p.aliases.join("、") : "無";
    const otherNamesStr = p.other_names && p.other_names.length ? `<div class="person-other-names">其他名號（待上下文判別后計入）：${escapeHTML(p.other_names.join("、"))}</div>` : "";

    main.innerHTML = `
      <div class="chapter-header">
        <h2>${escapeHTML(p.primary_name)}${p.courtesy_name ? ` <span class="muted">字${escapeHTML(p.courtesy_name)}</span>` : ""}</h2>
        <div class="chapter-sub">
          ${escapeHTML(dateRange(p))} · ${escapeHTML(p.brief || "")}
        </div>
        <div class="person-aliases">搜索別名：${escapeHTML(aliasesStr)}</div>
        ${otherNamesStr}
      </div>
      ${bioHTML}
      ${workSections || `<section class="person-section"><h3>提及</h3><p class="muted">未在他卷中找到提及。</p></section>`}
    `;
  } catch (err) {
    main.innerHTML = `<div class="error">載入失敗：${escapeHTML(err.message)}</div>`;
  }
}

/* ---------- ROUTING ---------- */

function parseHash() {
  const h = location.hash || "#/";
  let m = h.match(/^#\/chapter\/([a-z]+)\/(\d+)(?:#(.+))?$/);
  if (m) return { route: "chapter", book: m[1], juan: parseInt(m[2], 10), seg: m[3] || null };
  m = h.match(/^#\/timeline\/(\d+)$/);
  if (m) return { route: "timeline_year", year: parseInt(m[1], 10) };
  if (h === "#/timeline" || h.startsWith("#/timeline/")) return { route: "timeline_index" };
  m = h.match(/^#\/people\/([a-z0-9_-]+)$/i);
  if (m) return { route: "person", id: m[1] };
  if (h === "#/people" || h.startsWith("#/people/")) return { route: "people_index" };
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
  } else if (r.route === "person") {
    await renderPersonPage(r.id);
    window.scrollTo(0, 0);
  } else if (r.route === "people_index") {
    await renderPeopleIndex();
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
