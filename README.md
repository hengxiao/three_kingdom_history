# 三國志知識庫 (three_kingdom_history)

中國三國時期史料的結構化倉庫，目標是建成一個開放的數據集，最終提供網站與 MCP server 兩種訪問方式。

當前階段：65 卷《三國志》原文已從 [zh.wikisource.org](https://zh.wikisource.org) 入庫，並用工具腳本可對照其他版本（如 ctext.org）記錄異文。注解、其他史書、網站、MCP 留作後續階段。

> Wikisource 作 canonical 是因為它把每卷完整放在一頁，且裴注用 `〈...〉` 顯式包裹。ctext 因為大卷拆子頁加 captcha 阻擋，不適合批量，未來作為異文校對源使用。

## 倉庫結構

```
texts/<work>/<book>/<NN>.md             正文（canonical = wikisource，繁體）
annotations/<work>/<book>/<NN>.yaml     裴注（自動從 wikisource 〈...〉 抽出）
variants/<work>/<book>/<NN>.yaml        異文（canonical 與其他源的差異）
sources/<source-id>/...                 各版本原始快照（wikisource、ctext）
tools/                                  Python 工具腳本
tests/                                  pytest 測試
doc/format.md                           數據格式規範（必讀）
```

格式細節見 [doc/format.md](doc/format.md)。

## 開發

倉庫使用 PEP 668 環境下的本地 venv。首次建立：

```bash
python3 -m venv --without-pip .venv
curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
.venv/bin/pip install -e '.[dev]'
```

之後直接 `.venv/bin/python` 運行命令。

## 工具腳本

### segment.py — 正文校驗 / hash 重算

```bash
# 校驗一卷正文的 segments_sha256
.venv/bin/python -m tools.segment texts/sanguozhi/wei/01.md

# 重新計算並寫回 frontmatter
.venv/bin/python -m tools.segment texts/sanguozhi/wei/01.md --update

# 列出每段的 hash（JSON）
.venv/bin/python -m tools.segment texts/sanguozhi/wei/01.md --json
```

### fetch_wikisource.py / batch_fetch.py — 從 Wikisource 抓取

`fetch_wikisource.py` 抓單卷：保存原始 HTML 到 `sources/wikisource/...`，按 `〈...〉` 剝離裴注（state machine 處理嵌套；少數卷因 Wikisource 標記不平衡會回退到 lenient 正則），按 document order 編號，寫入 `texts/...`。

`batch_fetch.py` 按 [tools/sanguozhi_chapters.yaml](tools/sanguozhi_chapters.yaml) 把 65 卷一次抓完。`--sleep` 限速，`--no-fetch` 復用已存快照，`--resume` 跳過已寫入卷。

```bash
# 全量抓取（首次）
.venv/bin/python -m tools.batch_fetch --sleep 2

# 只重新解析（不發網絡請求，從 sources/wikisource/ 重生 texts/）
.venv/bin/python -m tools.batch_fetch --no-fetch
```

`fetch_ctext.py` 還在 repo 裡（兼容 ctext 子頁拆分前的數據），目前未在 batch 中使用，將來作異文對照源。

### extract_annotations.py — 抽出裴注

從 `sources/wikisource/...` 重新解析，將 `〈...〉` 標注按段錨寫入 `annotations/<work>/<book>/<NN>.yaml`。冪等：同源 HTML 產出 byte-identical YAML。

```bash
.venv/bin/python -m tools.extract_annotations
```

### build_variants.py — 對照 ctext / wikisource

每章嘗試從 ctext.org 抓取單頁版本，與 wikisource canonical 比對：
- 用 normalized hash + `difflib.SequenceMatcher` 對齊段
- 對每對段運行 `tools.diff_sources.classify`，得到字符級 diff ops
- 寫 `variants/<work>/<book>/<NN>.yaml`

ctext 對 35 卷拆了子頁（captcha 阻擋），這些卷會被 `SKIP`。其餘約 30 卷理論上可成功生成 variants，但 ctext 對連續抓取會 IP 限流（HTTP 403），需要分批拉、加大 `--sleep` 或等限流恢復後重試。當前 repo 已有 3 卷示例（wei/02, 03, 04）。

```bash
.venv/bin/python -m tools.build_variants --sleep 2          # 全量
.venv/bin/python -m tools.build_variants --no-fetch         # 復用 sources/ctext/ 快照
.venv/bin/python -m tools.build_variants --only 1,3,7       # 只跑指定 ctext_juan
```

### check.py — 倉庫級校驗（CI 用）

```bash
.venv/bin/python -m tools.check                   # 校驗 texts/ 與 annotations/
.venv/bin/python -m tools.check texts/sanguozhi   # 限定子目錄
```

校驗項目：
- texts/：frontmatter 必填字段、`script`/`source.id` 合法、sha256 格式、段 ID 與 `book`/`juan` 一致、`segments_sha256` 與重算結果相等
- annotations/：必填字段齊全、`anchor` 指向已存在段、`at` 在段長度內、`id` 唯一且匹配 `<segment-id>.aN`、`type` 在枚舉內

異文比對 API（`tools/diff_sources.py`）也可作為庫使用。

## 跑測試

```bash
.venv/bin/python -m pytest
```

新增任何 `tools/` 代碼必須同步補測試並跑通才算完成。

## 授權

雙重授權，分清楚「原文」和「整理」兩部分：

- **原文 / 文本內容**（`texts/`、`annotations/`、`variants/` 中的文字本身）—— **公有領域**。陳壽《三國志》與裴松之注成書於 3-5 世紀，早已超出任何版權期。整理選段、段 ID、排序等資料庫權利（如有）以 CC0 1.0 棄權。詳見 [LICENSE-DATA](LICENSE-DATA)。
- **代碼與整理框架**（`tools/`、`tests/`、`doc/`、frontmatter schema、hash 算法、CLI 工具等）—— **MIT License**。詳見 [LICENSE](LICENSE)。

## 設計原則

- **繁體為正**：canonical 文本保留 wikisource 的繁體原貌；簡繁轉換只在歸一化 hash 與展示層發生。
- **段級可寻址**：每段有穩定 ID（如 `wei.1.p3`），所有衍生數據（異文、注解）都通過 ID 鎖定。
- **零 LLM 依賴**：異文對照全部由 Python（hash + difflib + opencc）完成，可重現、可離線運行。
- **數據與展現解耦**：本倉庫只管數據，網站和 MCP 是下游消費者。
