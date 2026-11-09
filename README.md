# litsearch

多源文献检索工具，提供 CLI 和 Python 调用入口。支持 OpenAlex、Crossref、Europe PMC，统一合并、补摘要、过滤和排序，输出 Markdown、JSON、CSV 或供 Zotero 导入的 DOI 列表。

要求 Python 3.12+，运行时仅使用标准库。默认检索来源为 OpenAlex，默认按被引数排序。

## 安装

克隆本仓库并进入项目目录：

```shell
git clone https://github.com/meteoritel/lit-search.git
cd lit-search
```

### 安装 CLI

已安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 时，可将命令安装到独立工具环境：

```shell
uv tool install .
litsearch --version
litsearch --help
```

若终端找不到 `litsearch`，执行 `uv tool update-shell` 后重新打开终端。

### 使用 pip 安装并调用 Python

Windows PowerShell：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install .
.venv/Scripts/litsearch.exe --help
```

macOS / Linux：

```shell
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/litsearch --help
```

这两种方式都可在对应虚拟环境中 `import litsearch`。激活环境后，可直接使用 `python` 和 `litsearch` 命令。

### 直接运行源码

```shell
python src/litsearch.py --help
```

`src/litsearch.py` 可单独复制运行。下文的 `litsearch` 也可替换为 `python src/litsearch.py` 或虚拟环境中的命令路径。

## 第一次检索

Crossref 无需 API key，可直接开始：

```shell
litsearch "marine bacteria" --source crossref --limit 10 --stdout
```

`--stdout` 输出 JSON；日志与诊断写入 stderr。联网前可用 `--explain` 查看实际查询、筛选能力和默认输出目录：

```shell
litsearch "marine bacteria" --source crossref --explain
```

使用默认 OpenAlex 来源时，先在 [OpenAlex](https://openalex.org/settings/api) 获取 key。在当前目录创建 UTF-8 编码的 `.env`，可参考 [.env.example](.env.example)：

```dotenv
OPENALEX_API_KEY=your-key
```

也可使用环境变量或用户级 TOML。Windows 配置位置为 `%APPDATA%/litsearch/config.toml`，macOS / Linux 为 `~/.config/litsearch/config.toml`：

```toml
[api_keys]
openalex = "your-key"
```

Europe PMC 无需 API key。配置优先级与筛选支持情况见 [CLI 文档](docs/usage.md)。

## 检索与输出

```shell
# 合并多源候选，用标题和摘要中的海洋词筛选，再按主题证据排序
litsearch "marine protist grazing" --source openalex,europepmc --year 2022-2026 --limit 100 --require "marine,seawater,ocean,coastal" --sort hybrid --max-results 30

# 在硬过滤前补齐缺失摘要，最多查询 20 个新 DOI
litsearch "marine protist" --source crossref --enrich-abstracts 20 --require "seasonal,monthly" --stdout

# 生成 JSON 文件和 DOI 列表
litsearch "marine bacteria" --source crossref --output output/marine.json --doi-list output/marine.doi.txt
```

文件模式默认在调用目录的 `output/` 中生成 Markdown 和 DOI 列表，stdout 打印已写入的路径。`--output` 支持 `.md`、`.json`、`.csv`。目标已存在时，使用 `--overwrite` 允许覆盖。终端流和结果文件均使用 UTF-8。

`--year` 可省略；`--limit` 控制每个来源、每个查询的候选窗口，`--max-results` 控制最终结果上限。`require` 组内按 OR、组间按 AND 匹配标题与摘要；缺失摘要会影响保留结果。

专题预设见 [南海时间序列](examples/marine-timeseries.toml) 和 [海洋 LNA/HNA](examples/marine-lna-hna.toml)。PowerShell 中可这样加载：

```powershell
$env:LITSEARCH_CONFIG = (Resolve-Path examples/marine-lna-hna.toml).Path
litsearch --preset marine_lna_hna --explain
litsearch --preset marine_lna_hna --stdout
```

## Python 调用

在安装了本项目的 Python 环境中，配置由调用方显式提供，结果通过返回值交付：

```python
from litsearch import Config, QueryPlan, SearchClient

client = SearchClient(Config())
result = client.search(QueryPlan(
    queries=["marine protist grazing"],
    sources=["crossref"],
    limit=50,
    max_results=20,
    sort="hybrid",
))

for record in result.records:
    print(record.doi, record.title)

payload = result.to_dict()
```

## 文档与开发

- [CLI、配置与检索工作流](docs/usage.md)
- [Python 接口与 JSON 契约](docs/python.md)
- [开发、离线测试与分发验收](docs/development.md)
- [资料档案](docs/archive/README.md)与[计划索引](docs/plans/README.md)

安装开发依赖后，本地与 CI 使用同一验收入口：

```shell
uv sync --locked --extra dev
uv run --locked --extra dev python scripts/check.py
```

该入口执行 Ruff、mypy、离线回归、构建及安装产物检查。许可证：[MIT](LICENSE)。
