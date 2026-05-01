# 三國志知識庫 (three_kingdom_history)

中國三國時期史料的結構化倉庫，目標是建成一個開放的數據集，最終提供網站與 MCP server 兩種訪問方式。

當前階段：錄入《三國志》原文（取自 [ctext.org](https://ctext.org)），並用工具腳本對照其他版本記錄異文。注解、其他史書、網站、MCP 留作後續階段。

## 倉庫結構

```
texts/<work>/<book>/<NN-title>.md       正文（canonical 版本，繁體）
variants/<work>/<book>/<NN-title>.yaml  異文記錄（按段 ID 鎖定）
sources/<source-id>/...                 各版本原始快照
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
.venv/bin/python -m tools.segment texts/sanguozhi/wei/01-wudi-ji.md

# 重新計算並寫回 frontmatter
.venv/bin/python -m tools.segment texts/sanguozhi/wei/01-wudi-ji.md --update

# 列出每段的 hash（JSON）
.venv/bin/python -m tools.segment texts/sanguozhi/wei/01-wudi-ji.md --json
```

### fetch_ctext.py — 從 ctext.org 抓取一卷

每抓一卷會：保存原始 HTML 到 `sources/ctext/...`，剝離裴注（`<span class="inlinecomment">`），按段號（ctext 顯示的 1, 2, 3...）生成段 ID，寫入 `texts/...`，並回填 `source.sha256` 與 `segments_sha256`。

```bash
.venv/bin/python -m tools.fetch_ctext \
  --ctext-juan 1 \
  --work-prefix wei --book wei --book-title 魏書 \
  --juan 1 --title 武帝紀 --author 陳壽 \
  --out-text texts/sanguozhi/wei/01-wudi-ji.md \
  --out-source sources/ctext/sanguozhi/wei/01.html
```

### check.py — 倉庫級校驗（CI 用）

```bash
.venv/bin/python -m tools.check                   # 校驗 texts/ 下所有文件
.venv/bin/python -m tools.check texts/sanguozhi   # 限定子目錄
```

校驗項目：frontmatter 必填字段、`script`/`source.id` 取值合法、`source.sha256` 與 `segments_sha256` 為 64 位小寫 hex、段 ID 與 `book`/`juan` 一致、`segments_sha256` 與重算結果相等。

異文比對 API（`tools/diff_sources.py`）暫時只暴露為庫，待多源錄入後再加 CLI。

## 跑測試

```bash
.venv/bin/python -m pytest
```

新增任何 `tools/` 代碼必須同步補測試並跑通才算完成。

## 設計原則

- **繁體為正**：canonical 文本保留 ctext 的繁體原貌；簡繁轉換只在歸一化 hash 與展示層發生。
- **段級可寻址**：每段有穩定 ID（如 `wei.1.p3`），所有衍生數據（異文、注解）都通過 ID 鎖定。
- **零 LLM 依賴**：異文對照全部由 Python（hash + difflib + opencc）完成，可重現、可離線運行。
- **數據與展現解耦**：本倉庫只管數據，網站和 MCP 是下游消費者。
