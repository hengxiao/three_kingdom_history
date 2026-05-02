# 数据格式规范

本文档定义 `three_kingdom_history` repo 的数据格式。所有正文、异文、注解文件必须遵守此规范，便于网站与 MCP 的程序化消费。

## 0. 总体原则

- **正文与衍生数据分离**：原文（texts/）、异文（variants/）、注解（annotations/）各自独立文件，互不污染。
- **繁体为正**：canonical 文本保留 ctext.org 的繁体原貌，不在源文件做简繁转换；展示层再转。
- **段级寻址**：每段有稳定 ID（如 `wei.1.p3`），所有衍生数据通过 ID 锚定。
- **可机器校验**：每段、每文件都附 SHA256，工具脚本可一键校验完整性。
- **零 LLM 依赖**：异文比对全部由 Python（hash + difflib + opencc）完成，不调用大模型。

## 1. 目录结构

```
texts/<work>/<book>/<NN>.md                  # 正文（canonical 版本）
variants/<work>/<book>/<NN>.yaml             # 异文记录
annotations/<work>/<book>/<NN>.yaml          # 注解（裴注 + 自注），后续阶段
sources/<source-id>/<work>/<book>/<NN>.<ext> # 各版本原始快照（带 sha256）
schema/                                       # JSON Schema（可选，用于校验）
tools/                                        # Python 工具脚本
doc/                                          # 文档
```

`<work>` 取值如 `sanguozhi`、`houhanshu`、`zizhi-tongjian`。`<book>` 取值如 `wei`、`shu`、`wu`。文件名形如 `01.md`，`<NN>` 是该 book 内的本地卷号，两位补零。卷的中文标题保存在 frontmatter 的 `title` 字段，不重复进文件名。

## 2. ID 体系

### 2.1 段 ID 格式

```
<work-prefix>.<juan>.p<paragraph-no>
```

- `<work-prefix>`：作品 + 子部缩写。三国志魏书 = `wei`，蜀书 = `shu`，吴书 = `wu`；后汉书 = `hhs`；通鉴 = `zztj`。
- `<juan>`：卷号，阿拉伯数字，不补零。
- `p<n>`：段号，从 1 起，沿用 ctext.org 的段落切分。

例：`wei.1.p3` = 三国志·魏书·卷一（武帝纪）·第 3 段。

### 2.2 ID 稳定性

- 段 ID 一旦发布，不得变更。
- 若发现 ctext 切段需要细分，新增子段用 `wei.1.p3a`、`wei.1.p3b`，原 `wei.1.p3` 保留为别名（在 frontmatter 的 `aliases` 里登记）。
- 若发现段需要合并，保留原 ID，在 frontmatter 标 `merged_into`。

## 3. 正文文件（texts/）

### 3.1 文件格式

Markdown，UTF-8，LF 换行。开头一段 YAML frontmatter，正文用 HTML 锚点标记段 ID。

### 3.2 Frontmatter 字段

```yaml
---
work: sanguozhi               # 必填，作品 ID
work_title: 三國志             # 必填，繁体显示名
book: wei                     # 必填，子部 ID
book_title: 魏書               # 必填
juan: 1                       # 必填，卷号
title: 武帝紀                  # 必填，本卷标题（繁体）
author: 陳壽                   # 必填
script: traditional           # 必填，traditional | simplified
source:                       # 必填，canonical 来源
  id: ctext
  url: https://ctext.org/text.pl?node=...
  retrieved: 2026-05-01       # ISO 8601
  sha256: <原始抓取 HTML 的 sha256>
segments_sha256: <所有段拼接后的 sha256>   # 必填，工具生成
aliases: {}                   # 可选，旧段 ID → 新段 ID
---
```

### 3.3 正文写法

每段前一行用 HTML 锚点标 ID，段落本身是普通 Markdown 段落（一段一行，段间空行）：

```markdown
<a id="wei.1.p1"></a>
太祖武皇帝，沛國譙人也，姓曹，諱操，字孟德，漢相國參之後。

<a id="wei.1.p2"></a>
桓帝世，曹騰為中常侍大長秋，封費亭侯。
```

规则：

- 一个 `<a id>` 对应一个段，段内不再分。
- 段正文是单一 Markdown 段落（可跨多行书写，但渲染为一段）；不要在段内插入额外锚点、标题、列表。
- 不在正文里加注解、夹注、按语 —— 那些去 annotations/。
- 标点保留 ctext 原样，不自行修订。

## 4. 异文文件（variants/）

### 4.1 文件格式

YAML，UTF-8。每卷一个文件，路径与正文文件镜像。

### 4.2 Schema

```yaml
chapter: wei.1                # 必填，等同正文的 work-prefix.juan
canonical: ctext              # 必填，canonical 源 ID（与正文 source.id 一致）

sources:                      # 必填，列出所有参与对照的版本
  ctext:
    edition: ctext.org 數位版
    url: https://ctext.org/text.pl?node=...
    retrieved: 2026-05-01
    file_sha256: <sources/ 下原始文件的 sha256>
  zhonghua1959:
    edition: 中華書局點校本（1959 年第 1 版）
    retrieved: 2026-05-01
    file_sha256: ...
  bona:
    edition: 百衲本二十四史（商務印書館影印）
    retrieved: 2026-05-01
    file_sha256: ...

segments:                     # 必填，按段 ID 列出
  wei.1.p1:
    canonical_hash: <段正文 sha256>
    canonical_normalized_hash: <去标点+简繁归一后 sha256>
    diffs:                    # 与 canonical 不一致的源；完全相等则省略本段
      - source: zhonghua1959
        kind: variant_char    # 见 4.3
        equal_normalized: true   # 归一化后相等 → 仅是写法差异
        ops:                  # difflib SequenceMatcher 输出
          - { op: replace, at: 12, length: 1, from: "國", to: "国" }
        note: 簡繁差異
      - source: bona
        kind: textual
        equal_normalized: false
        ops:
          - { op: insert, at: 30, text: "也" }
        note: 百衲本多一「也」字
```

### 4.3 `kind` 枚举

| kind | 含义 |
|---|---|
| `variant_char` | 异体字、简繁差异，归一化后相等 |
| `textual` | 字词增删改，归一化后仍不等，属于实质性异文 |
| `punctuation` | 仅标点不同 |
| `typo` | 明显刻误/录入错误（需在 note 说明） |
| `missing` | 该源缺整段 |
| `extra` | 该源多出整段（罕见，用 segments 外的 `extra_segments` 块记录） |

### 4.4 `ops` 操作

每个 op 描述一处对 canonical 段文本的变换，使其变为该源文本：

- `{op: equal, at, length}` — 一般省略不写
- `{op: replace, at, length, from, to}` — 替换
- `{op: insert, at, text}` — 插入
- `{op: delete, at, length, text}` — 删除（`text` 记录被删字符，便于人读）

`at` 是 canonical 段文本中的字符索引（0 起），按 Python 字符串切片语义。

### 4.5 整段缺失或新增

```yaml
segments:
  wei.1.p7:
    canonical_hash: ...
    diffs:
      - source: bona
        kind: missing
        note: 百衲本无此段

extra_segments:               # 可选，源中存在但 canonical 没有的段
  - source: bona
    after: wei.1.p7           # 插入位置
    text: |
      ……
    note: 百衲本独有
```

## 5. 注解文件（annotations/）

留作下一阶段细化。占位结构：

```yaml
chapter: wei.1
annotations:
  - id: wei.1.p1.a1
    anchor: wei.1.p1
    span: { at: 0, length: 5 }       # 可选，标注段内具体位置
    type: pei                         # pei（裴松之注）| editor（本仓注）| crossref
    source: 三國志·魏書·武帝紀·裴注
    text: |
      ……
    refs: []                          # 引用其他段 ID
```

## 6. 哈希与归一化规则

工具脚本（`tools/segment.py`）必须严格按以下规则计算，保证不同机器结果一致：

### 6.1 段 sha256（`canonical_hash`）

1. 取段正文（`<a id>` 行之后到下一空行/下一锚点之前）。
2. 移除所有空白字符（含半角/全角空格、制表符、换行）。理由：古文以字符为单位，CJK 段落里出现的空白几乎都是 Markdown 软换行或编辑器拖尾空格，无语义；统一去掉才能让「同一段不同折行」hash 相同。
3. UTF-8 编码后取 SHA256，hex 小写。

### 6.2 归一化 sha256（`canonical_normalized_hash`）

在 6.1 的基础上，再依次：

1. 移除所有 Unicode 类别 P*（标点）字符。
2. 用 OpenCC `t2s.json` 配置将繁体转简体。
3. UTF-8 编码后取 SHA256。

### 6.3 文件 sha256（`segments_sha256`）

将该文件所有段的 `canonical_hash`（按段 ID 升序）以换行拼接，再取 SHA256。

## 7. 工具脚本约定

- `tools/fetch_ctext.py <chapter-id>` — 从 ctext 抓取并写入 `sources/ctext/...`，更新 frontmatter 的 `source.sha256`。
- `tools/segment.py <text-file>` — 校验或重生成 `<a id>` 段落、`canonical_hash`、`segments_sha256`。幂等。
- `tools/diff_sources.py <chapter-id>` — 读取该 chapter 的所有 sources 与 canonical 正文，重生成 variants/ 文件。幂等。
- 所有脚本必须可重入；运行后 git diff 应只包含真实变更。

## 8. 校验

CI（或本地 `tools/check.py`）应执行：

1. 每个 texts/ 文件的 frontmatter 字段完整。
2. 每段都有合法 ID，无重复。
3. `segments_sha256` 与重算结果一致。
4. variants/ 中的 `canonical_hash` 与 texts/ 的实际段 hash 一致。
5. 所有 `source.id` 在 variants 的 `sources` 块中有定义。

## 9. 变更约定

- 段 ID 一经发布不得变更；如需调整见 §2.2。
- frontmatter 字段新增向后兼容；删除或语义变更需升级 `format_version`（届时本文档增加该字段）。
- 当前 format_version: **0.1**（草案，正文录入完成前可能仍有调整）。
