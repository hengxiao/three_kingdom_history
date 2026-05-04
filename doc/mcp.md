# MCP 規範 — `three_kingdom_history`

本文件定義 `three_kingdom_history` repo 對外暴露的 MCP（Model Context
Protocol）介面，及其同源 CLI。兩者共用同一個 Python 後端與 schema，以保證
任何透過 MCP 取得的事實，都能用 CLI 重現／除錯。

> **狀態**：spec — 尚未實作。先審稿，再開工。

## 0. 設計原則

1. **單一資料來源**：MCP 與 CLI 都讀取 repo 內已發佈的 `texts/` +
   `annotations/` + `site/data/`（後者由 `tools/build_*` 構建並 commit）。
   不另外建表，不寫資料庫。
2. **無 LLM 依賴**：本層只做查詢與檢索；對外的解釋／摘要由呼叫方的 LLM
   負責。本層不調用任何外部 model endpoint。
3. **段級寻址**：所有錨點沿用 `<book>.<juan>.p<n>` 體系（見
   [doc/format.md](format.md#21-段-id-格式)）；MCP 回傳值含段 ID，呼叫方可
   把段 ID 餵回 `get_segment` 取得 raw text。
4. **繁體規範**：對外字串一律繁體（與 `texts/` 一致）。簡↔繁轉換由消費端
   自行處理。
5. **離線優先**：包含一份 launcher（`tools/mcp_serve.py`），無需網路，無需
   API key。

## 1. 兩種運行模式

### 1.1 Server 模式（MCP over stdio）

```
python -m tools.mcp_serve [--repo-root PATH]
```

- 用 [`mcp` Python SDK](https://github.com/modelcontextprotocol/python-sdk)
  實作 stdio server。
- 對外暴露三類介面：**tools**、**resources**、**prompts**（見 §3–§5）。
- 啟動時把 `site/data/` 載入記憶體（≈ 50MB JSON 反序列化後），首次調用前
  完成索引建構。

### 1.2 CLI 模式

```
python -m tools.mcp_cli <command> [args ...]
```

- 同一份 Python 包，CLI 子命令一對一對映 tool 名（見 §6）。
- 預設輸出 JSON（pipe 友好）；`--format=text` 切人類可讀格式。
- CLI 可獨立使用，也方便 e2e 測試 MCP server 邏輯（共用 service layer）。

### 1.3 共用層（`tools/mcp/`）

```
tools/mcp/
  __init__.py
  service.py     # 純函式 — 所有 tool 邏輯放這裡
  schema.py     # pydantic / dataclass — tool 輸入輸出 schema
  cache.py      # repo 載入 + 索引建構（singleton）
mcp_serve.py    # MCP stdio entrypoint
mcp_cli.py      # CLI entrypoint
```

`mcp_serve.py` 與 `mcp_cli.py` 都只是 thin wrapper：把 stdin/argv 解析後
交給 `service.py` 同一組函式。確保兩條路徑邏輯一致。

## 2. Schema 約定

### 2.1 共用型別

| 名稱 | 形狀 | 說明 |
|---|---|---|
| `WorkId` | `"sanguozhi" \| "houhanshu" \| "zztj"` | 史書 |
| `BookId` | `"wei" \| "shu" \| "wu" \| "hhs" \| "zztj"` | 子部 |
| `ChapterId` | `"<BookId>.<juan>"` | 例 `wei.1`、`zztj.61` |
| `SegmentId` | `"<ChapterId>.p<n>[<suffix>]"` | 例 `wei.1.p10`、`hhs.74.p3a` |
| `PersonId` | `"[a-z][a-z0-9_]*"` | 拼音 ID，例 `caocao`、`liubian` |
| `YearAD` | `int` | 公元紀年（負值表示公元前） |

### 2.2 Annotation kinds

```jsonc
// type: "person"
{ "person_id": "caocao", "via": "primary|alias|chapter_alias|given_name|given_name_carry|llm",
  "text": "曹操", "at": 5, "length": 2, "reasoning": "?" }

// type: "temporal"
{ "kind": "absolute|relative", "year_ad": 194, "era": "興平", "era_year": 1,
  "month_chinese": "春正月", "month_ordinal": 1, "text": "興平元年春正月",
  "at": 0, "length": 7, "reasoning": "?" }

// type: "pei" | "lixian"
{ "text": "裴注全文…", "at": 12, "length": 0 }
```

## 3. Tools

每個 tool 同時：
- 在 MCP server 上以 `@server.tool()` 註冊
- 在 CLI 上以 `python -m tools.mcp_cli <tool-name>` 提供等價子命令

下列 tool 為 v1 範圍（10 個）。所有 input 嚴格 schema，所有 output
JSON 可序列化。

---

### 3.1 `list_chapters`

列出所有可用章節，按書／卷排序。

**Input**：

```jsonc
{
  "work": "sanguozhi" | "houhanshu" | "zztj" | null,   // null = 全部
  "book": "wei" | "shu" | "wu" | "hhs" | "zztj" | null
}
```

**Output**：`Chapter[]`，每筆：

```jsonc
{
  "id": "wei.1",
  "work": "sanguozhi",
  "book": "wei",
  "juan": 1,
  "title": "魏書·武帝紀",
  "book_title": "魏書",
  "n_segments": 123,
  "n_pei": 45,
  "n_temporal": 38,
  "n_persons": 445
}
```

---

### 3.2 `get_chapter`

取單章全文 + 注解。

**Input**：

```jsonc
{
  "chapter_id": "wei.1",
  "include_annotations": true,    // false 只回 segments[].text
  "annotation_types": ["person", "temporal", "pei"] | null   // null = 全要
}
```

**Output**：

```jsonc
{
  "id": "wei.1",
  "title": "魏書·武帝紀",
  "book_title": "魏書",
  "author": "陳壽",
  "source": { "id": "wikisource", "url": "https://...", "retrieved": "2026-05-03" },
  "segments": [
    {
      "id": "wei.1.p5",
      "text": "金城邊章…",
      "annotations": [
        { "type": "person", "person_id": "caocao", "at": 26, "length": 2, "text": "太祖", "via": "chapter_alias" },
        { "type": "temporal", "kind": "absolute", "year_ad": 189, "text": "中平六年", "at": 215, "length": 4 },
        ...
      ]
    },
    ...
  ]
}
```

---

### 3.3 `get_segment`

取單段（不必載整章）。

**Input**：`{ "segment_id": "wei.1.p10" }`

**Output**：上面 `segments[]` 中的單一筆，外加 `chapter_id`、`chapter_title`。

---

### 3.4 `search_text`

全文檢索。輸入字串（不正則），回傳每個命中的段 ID + snippet。

**Input**：

```jsonc
{
  "query": "赤壁",
  "work": "sanguozhi" | "houhanshu" | "zztj" | null,   // null = 全部
  "book": "wei" | ... | null,
  "case_sensitive": false,
  "limit": 50,        // 預設 50；最大 500
  "snippet_chars": 80 // 命中位置前後各 N 字
}
```

**Output**：

```jsonc
{
  "n_total": 12,         // 不受 limit 限制
  "hits": [
    {
      "chapter_id": "wei.1",
      "segment_id": "wei.1.p47",
      "at": 23,
      "snippet": "…秋七月，公至赤壁，與備戰…",
      "matched": "赤壁"
    },
    ...
  ]
}
```

---

### 3.5 `list_persons`

列出 roster。預設按 `n_mentions` 降序。

**Input**：

```jsonc
{
  "limit": 50,
  "min_mentions": 0,
  "name_contains": "曹" | null,    // 對 primary_name + aliases 子字串匹配
  "alive_in_year": 200 | null     // 過濾 birth_ad ≤ year ≤ death_ad（生卒未知者不過濾）
}
```

**Output**：

```jsonc
{
  "n_total": 388,
  "persons": [
    {
      "id": "caocao",
      "primary_name": "曹操",
      "courtesy_name": "孟德",
      "birth_ad": 155,
      "death_ad": 220,
      "brief": "三国魏太祖…",
      "n_bio_chapters": 1,
      "n_mentions": 755
    },
    ...
  ]
}
```

---

### 3.6 `get_person`

取單人 full info：本傳 + 跨史書提及。

**Input**：`{ "person_id": "caocao", "include_mentions": true }`

**Output**：直接回 `site/data/people/<id>.json` 的內容（已含 bio_chapters、
mentions_by_work、aliases、other_names 等）。

---

### 3.7 `resolve_name`

把人名／別名／單字（不限本字典所收）對應回 `person_id`。如無唯一匹配則
回多筆候選。

**Input**：

```jsonc
{
  "name": "孟德",
  "context_chapter": "wei.1" | null   // 提供時把 chapter_aliases 一併納入解析
}
```

**Output**：

```jsonc
{
  "candidates": [
    {
      "person_id": "caocao",
      "matched_via": "courtesy_name",   // primary_name | alias | chapter_alias | given_name
      "primary_name": "曹操"
    }
  ]
}
```

---

### 3.8 `get_year`

取一年內所有時間錨點，按書序（zztj first）。

**Input**：`{ "year_ad": 200, "work": null }`

**Output**：

```jsonc
{
  "year_ad": 200,
  "labels": [{ "era": "建安", "era_year": 5, "label": "建安五年" }],
  "n_events": 47,
  "events": [
    {
      "chapter_id": "zztj.63",
      "chapter_title": "漢紀五十五",
      "segment_id": "zztj.63.p19",
      "kind": "absolute",
      "surface": "建安五年",
      "snippet": "…",
      "reasoning": "原文「建安五年」直接出现年号；解析為 AD 200"
    },
    ...
  ]
}
```

---

### 3.9 `list_years`

時間軸總覽。

**Input**：`{ "from_year": null, "to_year": null }`

**Output**：

```jsonc
{
  "years": [
    { "year_ad": 168, "n_events": 12, "labels": ["建寧元年"] },
    { "year_ad": 169, "n_events": 8, "labels": ["建寧二年"] },
    ...
  ]
}
```

---

### 3.10 `find_mentions_for_person`

列出指定人物在所有章節的提及位置。

**Input**：

```jsonc
{
  "person_id": "caocao",
  "exclude_bio_chapters": true,
  "limit": 100
}
```

**Output**：

```jsonc
{
  "n_total": 754,
  "mentions": [
    {
      "chapter_id": "zztj.61",
      "chapter_title": "漢紀五十三",
      "segment_id": "zztj.61.p10",
      "at": 0,
      "matched": "曹操",
      "via": "primary",
      "snippet": "曹操使司馬荀彧、壽張令程昱守鄄城…"
    },
    ...
  ]
}
```

## 4. Resources

MCP resources 暴露唯讀 URI，呼叫方可用 `resources/read` 取得。

| URI 範式 | 內容 |
|---|---|
| `chapter://<chapter_id>` | 等同 `get_chapter({chapter_id})` 完整 JSON |
| `chapter://<chapter_id>#<segment_id>` | 等同 `get_segment({segment_id})` |
| `person://<person_id>` | 等同 `get_person({person_id})` |
| `year://<year_ad>` | 等同 `get_year({year_ad})` |
| `index://chapters` | 章節索引 |
| `index://persons` | 人物索引 |
| `index://timeline` | 時間軸索引 |

`resources/list` 動態回傳 119 個 chapter URI + 388 個 person URI +
210 個 year URI（每次調用都重新從 `cache` 讀取，所以 commit 後即時生效）。

## 5. Prompts

少量、實用的 prompt 模板，由 server 端注冊：

### 5.1 `summarize_chapter`

**參數**：`chapter_id`

**生成的 user message**：

```
請根據以下章節內容，撰寫 200 字以內的中文白話摘要，重點放在事件與時間。
不要評論，不要引文，不要重複原文段落。

【章節 {chapter_id}：{title}】
{full_chapter_text}
```

伺服端負責填入完整段落文字（呼叫 `get_chapter`）。

### 5.2 `bio_summary`

**參數**：`person_id`

注入該人物的 brief、本傳所在章節清單、跨史書 mentions 數量、生卒。
模型輸出簡短人物小傳。

### 5.3 `events_in_year`

**參數**：`year_ad`

注入該年所有事件（按 zztj→sanguozhi→houhanshu 序），讓模型寫成連貫
narrative。

## 6. CLI 介面

CLI 子命令名與 tool 名一一對映（kebab-case）：

```
tk list-chapters [--work sanguozhi] [--book wei]
tk get-chapter wei.1 [--no-annotations] [--types person,temporal]
tk get-segment wei.1.p10
tk search '赤壁' [--work zztj] [--limit 100]
tk list-persons [--limit 50] [--min-mentions 10] [--name-contains 曹] [--alive-in 200]
tk get-person caocao [--no-mentions]
tk resolve-name 孟德 [--in-chapter wei.1]
tk year 200 [--work null]
tk list-years [--from 180 --to 220]
tk mentions caocao [--exclude-bio] [--limit 100]
```

通用旗標：

| 旗標 | 預設 | 說明 |
|---|---|---|
| `--format json|text` | `json` | 人類可讀 vs 機器可讀 |
| `--repo-root PATH` | repo 自動偵測 | override 資料根目錄 |
| `--pretty` | 開 | JSON pretty-print |

`tk` 是 console_script alias（`pyproject.toml` 註冊），完整等同
`python -m tools.mcp_cli`。

## 7. 載入與快取

- 啟動時呼叫 `cache.load_all()`：
  - 讀 `site/data/index.json`（章節清單）
  - 讀 `site/data/people.json`（roster）
  - 讀 `site/data/timeline.json`（年份索引）
  - lazy load `site/data/<work>/<book>/<NN>.json` per chapter request（首次
    讀後 keep in memory，因為 LLM 會重複問同一章）
- 索引建構：
  - person_by_id, person_by_name（含 aliases）
  - chapter_by_id
  - text_by_segment（by anchor）
- 全部資料 < 100MB；單機載入應在 2 秒內。

## 8. 錯誤處理

統一錯誤回覆：

```jsonc
{
  "error": {
    "code": "not_found" | "invalid_input" | "ambiguous" | "internal",
    "message": "human-readable 中文",
    "details": { ... }   // optional
  }
}
```

例：`get_segment("wei.1.p999")` → `not_found` + 提示有效範圍。
`resolve_name("操")` 不帶 context →
`ambiguous`，details 列出候選 `[caocao]` 並建議加 `context_chapter`。

## 9. 測試

- 每個 tool 配 pytest（`tests/test_mcp_*.py`），覆蓋：
  - 成功路徑（至少 1 個 fixture chapter／person／year）
  - error path（invalid input、not found、ambiguous）
  - schema validation（pydantic 通過）
- CLI 測試用 `subprocess.run` 跑 `python -m tools.mcp_cli ...`，確保 JSON
  輸出與 service layer 同步。
- MCP server 測試用 `mcp` SDK 提供的 in-memory client，呼叫每個 tool /
  read 每類 resource / fetch 每個 prompt。

## 10. 安裝與使用範例

```bash
pip install -e ".[mcp]"

# CLI
tk get-segment wei.1.p10
tk year 200 | jq '.events | length'
tk resolve-name 太祖 --in-chapter wei.1
# → {"candidates": [{"person_id": "caocao", "matched_via": "chapter_alias", ...}]}

# MCP server (stdio) — Claude Desktop / Cursor / etc. config:
# {
#   "mcpServers": {
#     "three-kingdoms": {
#       "command": "python",
#       "args": ["-m", "tools.mcp_serve", "--repo-root", "/path/to/three_kingdom_history"]
#     }
#   }
# }
```

## 11. 範圍外（v2 候選）

下列功能 v1 不做，留作後續：

- **search_persons_by_relation**：取「曹操之子／父／妻」等關係。Roster 目
  前不存血緣關係。
- **search_events_by_keyword**：跨年份找「赤壁」「官渡」相關事件。可由
  `search_text` + 時間 filter 組合，不需新 tool。
- **diff_sources**：對同一事件比較三家史書記載。需要事件級實體，目前無。
- **temporal_range_query**：「光和年間哪些 person 在世？」可由
  `list_persons --alive-in` + `list_years` 拼出，但若使用頻繁可包成 tool。
- **MCP elicitation / sampling**：不暴露這些（純讀庫，無需動態 prompt）。
- **HTTP transport**：v1 只 stdio。如遠端需要，未來可加 SSE。

## 12. 開發路線

| Phase | 範圍 | 估時 |
|---|---|---|
| 0 | service.py 骨架 + cache + 5 個核心 tool（list_chapters / get_chapter / get_segment / list_persons / get_person） | 0.5d |
| 1 | 剩餘 5 個 tool（search_text、resolve_name、get_year、list_years、find_mentions） | 0.5d |
| 2 | mcp_cli.py + console_script + CLI tests | 0.5d |
| 3 | mcp_serve.py + resources + prompts + MCP tests | 0.5d |
| 4 | docs/example、Claude Desktop config、README 章節 | 0.25d |

合計 ≈ 2.25 工作日。
