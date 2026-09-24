# litsearch

多源文献检索 CLI。按自由文本 + 年份 + 作者/机构/期刊等条件检索学术文献，跨源去重后输出
**人读清单（Markdown）**与 **DOI 列表（纯文本，每行一个裸 DOI）**。

- **零第三方运行时依赖**：只用 Python 标准库（`>=3.12`），拷一个文件到任何机器就能跑
- **可插拔数据源**：OpenAlex（默认，主力）与 Crossref（默认关闭，见「关于 Crossref」）
- **面向 Zotero 的交付**：DOI 列表可直接交给 Zotero 的按标识符导入 / 相关插件，不必自己写文献管理器导出格式
- **对 AI agent 友好**：JSON 走 stdout、进度与警告走 stderr、退出码明确、无交互式输入

## 安装

三种方式，任选其一。

**1. 单文件拷走（最省事，无需安装）**

```bash
# 把 src/litsearch.py 拷到任意位置
python litsearch.py "marine protist prokaryote interaction" --year 2021-2026
```

要求本机有 Python 3.12+，仅此而已。

**2. 装成全局命令（推荐日常使用）**

```bash
uv tool install .          # 之后任何目录敲 litsearch 即可
# 或： pipx install .
```

**3. 不安装，直接从源码运行**

```bash
uv run litsearch "your query"       # 在仓库根目录执行
```

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

同名预设以高优先级文件为准（用户级覆盖项目级），不同名则叠加 —— 所以你可以把 key 放在用户级、
把预设放在项目级，两者同时生效。

```toml
# litsearch.toml
output_dir = "output"

[presets.marine_amplicon]
queries = ["marine amplicon ASV protist prokaryote interaction",
           "nanoflagellate grazing bacteria"]
year = "2021-2026"
limit = 50
sort = "citations"
language = "en"
require = ["amplicon,asv,16s,18s,metabarcoding",
           "protist,eukaryot,prokaryot,bacteria,flagellate"]
exclude = ["retraction", "corrigendum"]
```

用 `--preset marine_amplicon` 调用。命令行上显式给出的参数会覆盖预设里的同名项；像 `--require`
这样的可重复参数，只要命令行出现过，就**整体替换**预设里的那组，不做追加。

## 用法

```bash
# 基本检索：在 output/ 下生成 .md 与 .doi.txt
litsearch "marine protist prokaryote interaction" --year 2021-2026 --limit 30

# 相关性分组过滤：--require 组内 OR、组间 AND
litsearch "marine protist prokaryote interaction" \
    --require "amplicon,asv,16s,18s" --require "protist,eukaryot,bacteria" \
    --exclude "review,meta-analysis" --limit 200

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
| `--limit N` | 每个查询抓取条数上限（默认 25）。**可超过 100**，自动 cursor 分页 |
| `--min-citations N` | 最低被引数 |
| `--author` | 作者姓名，或 OpenAlex 作者 ID（形如 `A1234567`） |
| `--institution` | 机构名称、OpenAlex 机构 ID（`I...`）或 ROR 链接 |
| `--journal-issn` | 期刊 ISSN |
| `--type` | 文献类型，如 `article` / `preprint` / `review` |
| `--language` | 语言，如 `en` |
| `--oa-only` / `--no-oa-only` | 仅开放获取 |
| `--oa-filter STR` | 直接透传给 OpenAlex 的原始 filter 字符串（逃生舱） |
| `--require "a,b"` | 相关性分组过滤，可重复。组内 `,` 为 OR，组间为 AND，须命中全部组 |
| `--exclude "x,y"` | 排除词，可重复。命中任一即剔除 |
| `--sort citations\|year\|relevance` | 排序，默认 `citations` |
| `--title` | Markdown 清单标题 |
| `--output PATH` | 覆盖主输出路径，后缀 `.md` / `.csv` / `.json` 决定格式 |
| `--doi-list PATH` | 覆盖 DOI 列表输出路径 |
| `--stdout` | 把 JSON 打到 stdout，不写任何文件 |

`--require` / `--exclude` 的作用范围是「标题 + 摘要」，在**合并去重之后**执行。

### 输出

默认写到 `<output_dir>/`，未配置 `output_dir` 时写到 `<当前目录>/output/`：

- `litsearch_<检索词slug>_<日期>.md` —— 人读清单，表格含年份/标题/作者/期刊/被引/DOI/开放获取；
  用了多个源时额外加一列「来源」
- `litsearch_<检索词slug>_<日期>.doi.txt` —— 每行一个裸 DOI，无表头

写入的文件路径会打印到 **stdout**（每行一个），其余日志与警告走 **stderr**，方便管道与 agent 取用。
无 DOI 的条目不会进 DOI 列表，数量会在 stderr 报告。

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 参数错误 |
| 3 | 配置缺失或 API key 缺失 |
| 4 | 网络或远端 API 错误 |

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
  因此**同名的不同文献会被合并** —— 这是为了合并版本而付出的代价。
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
uv build
python -c "import glob,zipfile; print(zipfile.ZipFile(glob.glob('dist/*.whl')[0]).namelist())"
```
