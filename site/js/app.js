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

  const segments = ch.segments.map(s => `
    <div class="segment" id="${s.id}">
      <div class="seg-id"><a href="#/chapter/${ch.book}/${ch.juan}#${s.id}" title="複製鏈接">${escapeHTML(s.id.split('.').slice(-1)[0])}</a></div>
      <div class="seg-text">${renderSegmentText(s)}</div>
    </div>`).join("");

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
 * Walk the segment text and intercalate annotation HTML at the right offsets.
 * Returns the rendered HTML for the segment body.
 *
 * Annotations carry `at` (offset into seg.text) and `length` (0 for point insertions
 * like 裴注; >0 for ranges like temporal surface highlights).
 */
function renderSegmentText(seg) {
  const text = seg.text;
  const events = []; // { pos, prio, kind, ann }
  for (const a of seg.annotations) {
    if (a.type === "temporal") {
      events.push({ pos: a.at, prio: 0, kind: "temp_open", ann: a });
      events.push({ pos: a.at + a.length, prio: 1, kind: "temp_close", ann: a });
    } else if (a.type === "pei" || a.type === "chen" || a.type === "editor" || a.type === "crossref") {
      // Point insertions render INSIDE any wrapping temporal span at the same position.
      events.push({ pos: a.at, prio: 2, kind: "pei_point", ann: a });
    }
  }
  // Sort: position ascending; close-temp (prio 1) before open-temp at same pos? No —
  // open before close so adjacent ranges render correctly. We sort prio asc within same pos.
  events.sort((a, b) => a.pos - b.pos || a.prio - b.prio);

  let out = "";
  let pos = 0;
  for (const ev of events) {
    if (ev.pos > pos) {
      out += escapeHTML(text.slice(pos, ev.pos));
      pos = ev.pos;
    }
    if (ev.kind === "temp_open") {
      out += `<span class="temporal" title="${escapeAttr(temporalTitle(ev.ann))}">`;
    } else if (ev.kind === "temp_close") {
      out += `</span><span class="temporal-ad" title="${escapeAttr(temporalTitle(ev.ann))}">${formatAD(ev.ann)}</span>`;
    } else if (ev.kind === "pei_point") {
      const marker = peiMarker(ev.ann);
      out += `<span class="pei" data-marker="${escapeAttr(marker)}" title="${escapeAttr(ev.ann.text)}"><span class="pei-text">${escapeHTML(ev.ann.text)}</span></span>`;
    }
  }
  if (pos < text.length) out += escapeHTML(text.slice(pos));
  return out;
}

function temporalTitle(a) {
  const month = a.month_chinese ? `（${a.month_chinese}）` : "";
  return `${a.era}${a.era_year === 1 ? "元" : a.era_year}年${month} = 公元 ${a.year_ad} 年`;
}

function formatAD(a) {
  const m = a.month_ordinal ? `/${a.month_ordinal}月` : "";
  return `AD${a.year_ad}${m}`;
}

function peiMarker(a) {
  // Last segment of the id, e.g. "wei.1.p3.a5" → "注5"
  const m = a.id.match(/\.([at])(\d+)$/);
  if (!m) return "注";
  return m[1] === "a" ? `注${m[2]}` : `t${m[2]}`;
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
  route();
});
