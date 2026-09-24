# litsearch

多源文献检索 CLI。按自由文本 + 年份 + 作者/机构/期刊等条件检索学术文献，跨源去重后输出
**人读清单（Markdown）**与 **DOI 列表（纯文本，每行一个裸 DOI）**。

- **零第三方运行时依赖**：只用 Python 标准库（`>=3.12`），拷一个文件到任何机器就能跑
- **可插拔数据源**：OpenAlex（默认，主力）与 Crossref（默认关闭，见「关于 Crossref」）
- **面向 Zotero 的交付**：DOI 列表可直接交给 Zotero 的按标识符导入 / 相关插件，不必自己写文献管理器导出格式
- **对 AI agent 友好**：结果可走 stdout（默认 JSON，`--stdout-format md|csv|doi` 可换）／
  进度与警告走 stderr／退出码明确／无交互式输入

## 安装

从远程仓库克隆到本地（下面的方式 1~3 都在克隆出来的目录里执行）：

```bash
git clone https://github.com/meteoritel/lit-search.git
cd lit-search
```

**1. 单文件拷走（最省事，无需安装）**

```bash
# 把 src/litsearch.py 拷到任意位置即可，不必保留仓库的其它文件
python src/litsearch.py "marine protist prokaryote interaction" --year 2021-2026
```

要求本机有 Python 3.12+，仅此而已。

**2. 装成全局命令（推荐日常使用）**

```bash
uv tool install .          # 之后任何目录敲 litsearch 即可
# 或： pipx install .
```

不想先克隆也能直接装：

```bash
uv tool install git+https://github.com/meteoritel/lit-search.git
# 或： pipx install git+https://github.com/meteoritel/lit-search.git
```

**3. 不安装，直接从源码运行**

```bash
uv run litsearch "your query"       # 在克隆目录里执行
```

更新到最新版：在克隆目录 `git pull`，再重跑一次对应的安装命令（方式 2/3）即可。

## 配置 API key

OpenAlex 自 2026 年起对匿名访问限流，**必须提供 API key**。申请地址：
<https://openalex.org/settings/api>

key 的查找顺序是「环境变量 → 用户级 `config.toml` → 项目 `litsearch.toml` → 项目 `.env`」，
**逐键取首个命中**，所以同名配置项以更靠前者为准。

```bash
# 方式一：环境变量（Windows）
setx OPENALEX_API_KEY "你的key"      # 需重开终端
# 方式二：项目级 .env（照 .env.example 改）
cp .env.example .env
```

用户级配置文件位置：

- Windows：`%APPDATA%\litsearch\config.toml`
- macOS / Linux：`~/.config/litsearch/config.toml`

```toml
output_dir = "D:/lit/papers"
mailto = "you@example.com"          # 可选，进入 Crossref 礼貌池
default_sources = ["openalex"]

[api_keys]
openalex = "你的key"
```

若任何位置都没有 key，工具会**在发起任何网络请求之前**以退出码 3 退出，并打印申请地址与三种设置方法。

## 配置文件与预设

结构化配置（`output_dir` / `default_sources` / `[api_keys]` / `[presets]`）只能来自 TOML。
项目根 `litsearch.toml` 是项目级配置，适合把「一组检索方案」随项目一起交给组内同学。

> **工具不自带任何预设。** 下面这份 `litsearch.toml` 是**示例**，不是默认值

同名预设以高优先级文件为准（用户级覆盖项目级），不同名则叠加 —— 所以你可以把 key 放在用户级、
把预设放在项目级，两者同时生效。

```toml
# litsearch.toml —— 以下全部是示例，请按自己的实际情况替换
output_dir = "output"

[presets.marine_amplicon]      # 预设名由你自己定，这里只是个名字
queries = ["marine amplicon ASV protist prokaryote interaction",
           "nanoflagellate grazing bacteria"]
year = "2021-2026"
limit = 50
sort = "citations"
language = "en"
require = ["amplicon,asv,16s,18s,metabarcoding",       # 这两个词表只适用于该课题
           "protist,eukaryot,prokaryot,*bacteri*"]
exclude = ["retraction", "corrigendum"]
```

用 `--preset marine_amplicon` 调用。命令行上显式给出的参数会覆盖预设里的同名项；像 `--require`
这样的可重复参数，只要命令行出现过，就**整体替换**预设里的那组，不做追加。预设里同样可以写
`fetch_sort`、`max_results` 等任意结构化选项。

上面只有 `*bacteri*` 带了星号，因为单词默认就是**词首前缀**匹配，`protist` 已经能命中 `protists`、
`eukaryot` 能命中 `eukaryotic`，加不加 `*` 一样。而 `bacteria` 匹配不到 `cyanobacteria`（它在词中）
和 `bacterium`（异体），要这种效果必须显式写 `*bacteri*`。完整规则见下面的匹配规则表。

## 用法

```bash
# 基本检索：在 output/ 下生成 .md 与 .doi.txt
litsearch "marine protist prokaryote interaction" --year 2021-2026 --limit 30

# 多词术语务必加英文双引号 —— OpenAlex 的 search 是词袋匹配，不加引号召回会失控
# （实测 "low nucleic acid content" 156 篇，不加引号 99869 篇）
litsearch '"low nucleic acid content" bacteria' --year 2022-2026 --limit 100

# 相关性分组过滤：--require 组内 OR、组间 AND
litsearch '"low nucleic acid content" bacteria' \
    --require "*bacteri*,prokaryot" --require "marine,coastal,seawater,ocean" \
    --exclude "review,meta-analysis" --limit 200

# 要「全网最高被引的 N 篇」，而不只是「相关度最高的 N 篇」里再排序
litsearch '"low nucleic acid content" bacteria' \
    --fetch-sort citations --sort citations --limit 50

# 按作者 / 机构 / 期刊 / 类型 / 语言筛，只要开放获取
litsearch "grazing nanoflagellate" --author "Jane Smith" \
    --journal-issn 0024-3590 --type article --oa-only --language en

# 用预设，并把 JSON 打到 stdout（不写任何文件）
litsearch --preset marine_amplicon --stdout

# 同时用两个源
litsearch "grazing nanoflagellate" --source openalex,crossref --limit 20
```

### 参数

| 参数 | 说明 |
|---|---|
| `query...` | 自由文本检索词，可给多个，自动合并去重；可用 `--preset` 代替 |
| `--preset NAME` | 使用配置中的命名预设 |
| `--source openalex,crossref` | 数据源，逗号分隔。默认 `openalex` |
| `--year 2024` / `--year 2021-2026` | 年份范围 |
| `--limit N` | 每个查询从远端抓取的**候选**条数上限（默认 25）。**可超过 100**，自动 cursor 分页 |
| `--max-results N` | 最终写入清单的条数上限（默认不限）。配合 `--limit` 可「多抓候选、少出结果」 |
| `--min-citations N` | 最低被引数（`0` 与不写同义＝不限） |
| `--author` | 作者姓名，或 OpenAlex 作者 ID（形如 `A1234567`） |
| `--institution` | 机构名称、OpenAlex 机构 ID（`I...`）或 ROR 链接 |
| `--journal-issn` | 期刊 ISSN |
| `--type` | 文献类型，如 `article` / `preprint` / `review` |
| `--language` | 语言，如 `en` |
| `--oa-only` / `--no-oa-only` | 仅开放获取 |
| `--oa-filter STR` | 直接透传给 OpenAlex 的原始 filter 字符串（逃生舱） |
| `--require "a,b"` | 相关性分组过滤，可重复。组内 `,` 为 OR，组间为 AND，须命中全部组；匹配规则见下 |
| `--exclude "x,y"` | 排除词，可重复。命中任一即剔除；匹配规则同 `--require` |
| `--sort citations\|year\|relevance` | **展示**排序，默认 `citations`。只改变清单顺序 |
| `--fetch-sort relevance\|citations\|year` | **远端抓取**排序，默认 `relevance`。决定取到哪一批候选 |
| `--title` | Markdown 清单标题 |
| `--output PATH` | 覆盖主输出路径，后缀 `.md` / `.csv` / `.json` 决定格式（**只认这三个**，写别的后缀直接报错） |
| `--doi-list PATH` | 覆盖 DOI 列表输出路径 |
| `--stdout` | 把结果打到 stdout，不写任何文件 |
| `--stdout-format json\|md\|csv\|doi` | `--stdout` 的内容格式，默认 `json` |
| `--version` | 打印版本号 |

`--require` / `--exclude` 的作用范围是「标题 + 摘要」，在**合并去重之后**执行 —— 因此 `--limit`
要给得比目标产出大得多（实测 `--limit 100` × 4 查询 → 抓 394 → 去重 243 → 过滤后只剩 16）。
想「多抓候选、少出结果」就配 `--max-results`：`--limit 100 --max-results 30`。

产出的 markdown 会按阶段列出计数 —— 排除词刷掉多少、**每个 require 组单独能留多少**、
取交集后剩多少 —— 哪一组是瓶颈一眼可见，不必自己写脚本排查。远端总量超过抓取窗口时还会在
stderr 提示「共 N 篇，只考察了前 M 篇」，召回天花板不会是隐形的。

匹配前会把文本归一化（小写、标点统一成空格），所以标题里写作 `Nucleic Acid-Content` 的论文
能被检索词 `nucleic acid content` 命中：

| 写法 | 语义 | 例子 |
|---|---|---|
| `protist` | 词首前缀（默认） | 命中 `protists`；`sea` 不再命中 `research` / `disease`，但**仍会命中** `seasonality` / `search`（它们词首就是 `sea`） |
| `=sea` | 整词精确 | 只命中独立的 `sea`，不命中 `seawater` / `seasonality` / `search` |
| `*bacteri*` | 任意位置子串 | 命中 `cyanobacteria` 这类复合词；只写 `bacteri` 是匹配不到的 |
| `海洋` | 含非 ASCII 时退回子串 | 中文无空格，不做词边界 |

`--sort` 与 `--fetch-sort` 是两个独立旋钮。默认按相关度抓取，所以单给 `--sort citations`
只是在「相关度最高的 N 条」这个切片内部按被引重排；要拿全网最高被引的那批，两个都设 `citations`。

### 输出

默认写到 `<output_dir>/`，未配置 `output_dir` 时写到 `<当前目录>/output/`：

- `litsearch_<片段>_<日期>.md` —— 人读清单，表格含年份/标题/作者/期刊/被引/DOI/开放获取；
  用了多个源时额外加一列「来源」
- `litsearch_<片段>_<日期>.doi.txt` —— 每行一个裸 DOI，无表头

`<片段>` 优先取 `--title`，没给标题时取**第一个**查询；中文会被保留（`litsearch_海洋细菌_2026-09-24.md`），
不会退化成 `query` 而让同一天的两个课题互相覆盖；超长时按 `-` 边界截断，不会切出半个单词。

`--stdout` 默认输出 JSON（供 agent 解析），`--stdout-format md|csv|doi` 可换成 markdown 表格、
CSV 或裸 DOI 列表；`--stdout` 与 `--output` / `--doi-list` 互斥。

写入的文件路径会打印到 **stdout**（每行一个），其余日志与警告走 **stderr**，方便管道与 agent 取用。
无 DOI 的条目不会进 DOI 列表，数量会在 stderr 报告。

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 参数错误 |
| 3 | 配置缺失或 API key 缺失 |
| 4 | 网络或远端 API 错误 |

## 建议工作流

检索是「宽召回 + 后置过滤」两段式，推荐按下面六步走。括号里的数字都是对同一个真实课题
（海洋 高核酸/低核酸 HNA/LNA 细菌，2022-2026）实测得到的，不是估计值。

### 1. 先探远端量级，确认检索式够具体

多词术语**必须加英文双引号**，否则 OpenAlex 的词袋匹配会让召回量失控：

| 检索式 | 远端命中 |
|---|---|
| `low nucleic acid content bacteria` | 99869 篇 |
| `"low nucleic acid content" bacteria` | 156 篇 |

健康量级是几百到几千；上万基本说明术语没加引号或选得太宽。用 `--limit 5 --stdout` 看一眼
`queries[].total_count` 就能判断，不必真抓。

### 2. 定候选量与产出量

`--limit` 是每个查询的**候选**抓取量，`--max-results` 是最终**产出**上限。过滤器在抓取之后
执行，所以经验配方是「宽抓、少出」：

```bash
# 同一课题的多个同义查询一起给，主力靠 require 组做后置过滤
litsearch '"high nucleic acid content" bacteria' \
         '"low nucleic acid content" bacteria' \
    --year 2022-2026 --limit 100 --max-results 30 --type article \
    --require "nucleic acid" --require "marine,coastal,seawater,ocean"
```

这一课题实测：抓 394 条 → DOI 去重 151 条 → 过滤后 16 篇。下面第 3 步的分级计数与
第 4 步的饱和对比都取自同一次运行，可以直接对着看。

### 3. 看分级计数定位瓶颈，别凭感觉删词

生成的 markdown 会列出每个 `--require` 组**单独**能留下多少条（输入集合相同），瓶颈一眼可见：

```text
- 排除词刷掉 66 条，剩 177 条
- 单看第 1 组「nucleic acid」可留 33 条
- 单看第 2 组「marine 或 coastal 或 seawater 或 ...」可留 81 条
- 命中全部 require 组后剩 16 条
```

这里瓶颈是主题组（33）而非海洋组（81）—— 收紧海洋词没用，该调的是主题词。

### 4. 候选量加到「计数不再增长」为止

stderr 会提示抓取窗口是否被远端总量截断：

```text
[warn] openalex: 「"nucleic acid content" marine」远端共 382 篇，只考察了前 100 篇
```

但**不是越大越好**。同一课题把 `--limit` 从 100 提到 150：抓取量 394 → 510（多 116 条候选），
最终结果仍是 16 篇，一篇没多。分级计数停止增长就说明已饱和，再加只推高 OpenAlex 每日预算。
（单查询 `--limit` 超过 100 会触发 cursor 分页，每页一次请求。）

### 5. 残余假阳性交给人工核对摘要

结构上滤不掉的误召回是正常的，读摘要排除即可。本课题的两个典型误召回：

- 讲 "nucleic acid **sequencing**" 的海洋病毒综述；
- 拿 "nucleic acid content" 当**生物量指标**的浮游植物论文（研究对象是微微型真核藻，
  不是 HNA/LNA 细菌）。

想压这类噪声可以收紧主题组（如 `nucleic acid content` 再加一个 `bacteria,prokaryot*` 组），
但代价是同时误杀 `Low Nucleic Acid Prokaryotes ...` 这类正常标题 —— 实测一次丢掉 2 篇真相关。
**默认建议宽主题组 + 人工核对**，除非你的产出要全自动流转。

### 6. 固化与交付

- 把定好的组合写进 `[presets]`，下次 `--preset NAME` 复用；预设同样支持 `fetch_sort`、
  `max_results` 等全部结构化选项。
- 交给 Zotero 用 `.doi.txt`，清单给人读。
- 要找领域奠基性文献（而非最新进展）时两个排序都设被引序：`--fetch-sort citations --sort citations`。
  只设 `--sort citations` 只是在相关度最高的那 N 条内部重排，拿不到全网最高被引的那批。

## 配合 Zotero

`--doi-list` 产出的 `.doi.txt` 每行一个裸 DOI，直接喂给 Zotero 的
「添加条目 → 按标识符添加」（可粘贴多行），或任何支持按 DOI 批量导入的插件即可。

之所以不做 BibTeX / RIS / CSL-JSON 导出：Zotero 侧的按标识符导入已经能拿到权威元数据，
自己拼导出格式属于重复造轮子，且会引入字段保真度问题（例如 RIS 的条目类型有损、
无逗号的中文作者名会被拆错）。

## 关于 Crossref

Crossref **默认关闭**，用 `--source openalex,crossref` 显式开启。

需要留意的是：Crossref 的 REST 检索按官方文档的定位是**引文/元数据匹配器**，不是主题发现工具
（其 `query.bibliographic` 被明确定义为「用于引文查找」）。实测对主题词检索的相关性明显弱于
OpenAlex —— 例如用它检索 `protist bacterivory grazing` 会返回「放牧对植被与土壤的定量影响」
这类无关文献。它的强项在于作为 DOI 注册机构的权威性（精确 DOI、ORCID 作者、ROR 机构、基金号、
撤稿状态）。

实现上因此有这些兜底：使用 `query` 而非 `query.bibliographic`；它没有 `relevance_score`，所以
`--sort relevance` 时 Crossref 记录会排在后段；它不支持按被引数过滤，`--min-citations` 与
`--language` 只能在本地过滤并会给出警告。

## 已知限制

- **不做中文库适配**。OpenAlex / Crossref 对中文期刊覆盖很差，CNKI、万方没有公开 API。
  `--language zh` 只能筛出这两个库里已有的中文条目，召回率很低。
- **去重按「先 DOI 后标题」两级**。先按 DOI 合并（处理跨源同一文章标题字符串不一致），
  再按归一化标题合并（处理 preprint 与正式版 DOI 不同）。归一化只保留字母数字，
  因此**同名的不同文献会被合并** —— 这是为了合并版本而付出的代价。兜底是归一化后短于
  15 字符的标题不参与标题合并，避免 `Editorial` / `Correction` / `Front matter` 这类
  通用标题把不相干的论文并成一条。
- **本地过滤器只看「标题 + 摘要」，而远端 `search` 还能命中全文**。所以 `--require` 天然比
  远端查询更严：远端说命中了、本地却看不到那个词时会被刷掉。实测某课题去重后 243 条候选里，
  只有 33 条能在标题+摘要中找到主题词 `nucleic acid` —— 这是筛选后结果变少的主因，不是 bug。
- **跨源被引数不可比**。Crossref 的 `is-referenced-by-count` 与 OpenAlex 的 `cited_by_count`
  对同一篇论文的数值不同，合并时以被引高者为底，平手时 OpenAlex 优先。
- 默认会创建 `./output/` 目录，且没有交互确认。
- `--limit` 开很大时 cursor 深翻会推高 OpenAlex 的每日预算消耗。

## 开发

```bash
uv sync --extra dev
uv run python -m pytest -q      # 或用系统 python -m pytest
```

测试完全离线：`Http` 的 opener 可注入，`RetryPolicy` 的 sleep 可注入，所有响应体都是
`tests/fixtures/` 下手工裁剪的真实 API 响应片段，不存在任何实时网络调用。

构建与校验：

```bash
uv build          # 产出 dist/litsearch-<版本>-py3-none-any.whl 与 .tar.gz
uv build --offline   # PyPI 不可达时走 uv 缓存（需缓存里已有 setuptools>=77）
```

```python
# 校验 wheel：应只含 litsearch.py + dist-info/licenses/，且 METADATA 里有 License-Expression: MIT
import glob, zipfile
z = zipfile.ZipFile(sorted(glob.glob("dist/*.whl"))[-1])
print(z.namelist())
print(z.read(next(n for n in z.namelist() if n.endswith("METADATA"))).decode()[:400])
```
