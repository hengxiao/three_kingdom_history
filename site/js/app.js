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

async function renderIndex() {
  setBreadcrumbs([{ label: "三國志", href: "#/" }]);
  const main = $("#content");
  main.innerHTML = "載入目錄中…";
  try {
    const idx = await loadJSON(`${DATA_BASE}/index.json`);
    const html = idx.books.map(book => {
      const items = book.chapters.map(ch => `
        <li>
          <a href="#/chapter/${book.id}/${ch.juan}">
            <span class="chapter-title">${escapeHTML(`卷${ch.juan}　${ch.title}`)}</span>
            <span class="chapter-meta">${ch.n_segments} 段 · 裴注 ${ch.n_pei} · 時間錨點 ${ch.n_temporal}</span>
          </a>
        </li>`).join("");
      return `
        <section class="book-section">
          <h2>${escapeHTML(book.title)}（共 ${book.chapters.length} 卷）</h2>
          <ul class="chapter-list">${items}</ul>
        </section>`;
    }).join("");
    main.innerHTML = html || `<div class="error">目錄為空。請先生成數據。</div>`;
  } catch (err) {
    main.innerHTML = `<div class="error">載入失敗：${escapeHTML(err.message)}</div>`;
  }
}

/* ---------- CHAPTER PAGE ---------- */

async function renderChapter(book, juan) {
  const nn = String(juan).padStart(2, "0");
  setBreadcrumbs([
    { label: "三國志", href: "#/" },
    { label: `${bookTitleFromId(book)}卷${juan}`, href: `#/chapter/${book}/${juan}` },
  ]);
  const main = $("#content");
  main.innerHTML = "載入章節中…";
  try {
    const ch = await loadJSON(`${DATA_BASE}/sanguozhi/${book}/${nn}.json`);
    main.innerHTML = renderChapterHTML(ch);
  } catch (err) {
    main.innerHTML = `<div class="error">載入章節失敗：${escapeHTML(err.message)}</div>`;
  }
}

function bookTitleFromId(id) {
  return { wei: "魏書", shu: "蜀書", wu: "吳書" }[id] || id;
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
      const cls = ev.ann.kind === "relative" ? "temporal temporal--relative" : "temporal";
      out += `<span class="${cls}" data-resolution="${escapeAttr(ev.ann.resolution || "absolute")}" title="${escapeAttr(temporalTitle(ev.ann))}">`;
    } else if (ev.kind === "temp_close") {
      const cls = ev.ann.kind === "relative" ? "temporal-ad temporal-ad--relative" : "temporal-ad";
      out += `</span><span class="${cls}" title="${escapeAttr(temporalTitle(ev.ann))}">${formatAD(ev.ann)}</span>`;
    } else if (ev.kind === "pei_marker") {
      const n = peiNum.get(ev.ann.id);
      out += `<sup class="pei-ref" data-ann="${escapeAttr(ev.ann.id)}"><a href="#${ev.ann.id}-note">[${n}]</a></sup>`;
    }
  }
  if (pos < text.length) out += escapeHTML(text.slice(pos));

  // 注解块: pei 列表 + temporal 列表（如有）
  let notesHTML = "";
  if (peis.length || temporals.length) {
    const peiItems = peis.map((a, i) => `
      <li class="note-item note-pei" id="${a.id}-note">
        <span class="note-marker">[${i + 1}]</span>
        <span class="note-text">${escapeHTML(a.text)}</span>
      </li>`).join("");
    const tempItems = temporals.map((a, i) => `
      <li class="note-item note-temporal">
        <span class="note-marker">時${i + 1}</span>
        <span class="note-text">${escapeHTML(a.text)} = 公元 ${a.year_ad} 年${a.month_ordinal ? `（農曆 ${a.month_ordinal} 月）` : ""}</span>
      </li>`).join("");
    notesHTML = `<ul class="notes">${peiItems}${tempItems}</ul>`;
  }

  return { textHTML: out, notesHTML };
}

function temporalTitle(a) {
  const month = a.month_chinese ? `（${a.month_chinese}）` : "";
  const eraStr = a.era ? `${a.era}${a.era_year === 1 ? "元" : a.era_year}年${month}` : a.text;
  const tail = a.kind === "relative" ? `（相對：${a.resolution || ""}）` : "";
  return `${eraStr} = 公元 ${a.year_ad} 年 ${tail}`.trim();
}

function formatAD(a) {
  const m = a.month_ordinal ? `/${a.month_ordinal}月` : "";
  return `AD${a.year_ad}${m}`;
}

/* ---------- ROUTING ---------- */

function parseHash() {
  const h = location.hash || "#/";
  // forms: "#/", "#/chapter/<book>/<juan>", optionally "#<segId>" within
  const m = h.match(/^#\/chapter\/([a-z]+)\/(\d+)(?:#(.+))?$/);
  if (m) return { route: "chapter", book: m[1], juan: parseInt(m[2], 10), seg: m[3] || null };
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
  } else {
    await renderIndex();
    window.scrollTo(0, 0);
  }
}

// Single delegated click handler bound once for the page lifetime. Same DOM
// across all responsive layouts; CSS decides whether the notes block is
// side-by-side, stacked below, or hidden-until-revealed (narrow).
const NARROW_MQ = window.matchMedia("(max-width: 600px)");
function bindClicks() {
  $("#content").addEventListener("click", e => {
    // (1) tap on a 裴注 marker [N] — scroll and flash the matching note.
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
    // (2) tap on the 正文 (narrow only) — toggle the segment's notes.
    if (NARROW_MQ.matches) {
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
