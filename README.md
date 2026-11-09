# litsearch

多源文献检索工具，提供 CLI 和 Python 调用入口。检索后统一去重、过滤、排序，输出文献清单、结构化结果或供 Zotero 导入的 DOI 列表。

运行要求 Python 3.12+，运行时仅使用标准库。OpenAlex 为默认来源，Crossref、Europe PMC 通过 `--source` 开启；`src/litsearch.py` 可单独拷走运行。

## 安装与开始使用

在克隆的仓库目录中安装命令：

```bash
uv tool install .
# 或 pipx install .
```

也可直接运行源码：

```bash
python src/litsearch.py --help
```

本工具使用 OpenAlex 时要求配置 API key。可将 `.env.example` 复制为当前目录的 `.env` 并填写 `OPENALEX_API_KEY`，也可设置同名环境变量，或在用户配置中保存：

```toml
[api_keys]
openalex = "your-key"
```

用户配置位置：Windows 为 `%APPDATA%/litsearch/config.toml`，macOS/Linux 为 `~/.config/litsearch/config.toml`。

```bash
# 在联网前检查生效计划、配置来源和来源能力
litsearch '"low nucleic acid content" bacteria' --year 2022-2026 --explain

# 输出 JSON，便于 agent 或管道读取
litsearch '"low nucleic acid content" bacteria' --year 2022-2026 --stdout

# 生成 Markdown 清单和 DOI 列表
litsearch "marine protist grazing" --limit 100 --max-results 30

# 开启 Crossref；严格要求所有来源执行请求的筛选条件
litsearch "marine bacteria" --source openalex,crossref --strict-filters --stdout

# 用概念组生成来源查询，并按主题证据与来源排名排序
litsearch --concept "perovskite" --concept "solar cell" --concept "moisture,humidity" --sort hybrid --stdout

# 在硬过滤前按 DOI 补充缺失摘要，最多查询 20 个新 DOI
litsearch "marine protist" --source crossref --enrich-abstracts 20 --require "seasonal,monthly" --stdout
```

默认文件写到 `output/`。目标已存在时使用 `--overwrite` 明确允许覆盖。文件模式 stdout 只打印成功提交的路径；JSON 模式 stdout 只输出结果，诊断均可在 JSON 和 stderr 中查看。

南海时间序列主题可从 [TOML 示例](examples/marine-timeseries.toml) 开始；先用 `--explain` 查看实际查询。`rrf`、`bm25`、`hybrid` 是可选排序，默认仍按被引数。算法对照与质量取舍见[评测说明](docs/research/retrieval-quality-evaluation.md)。

## Python 调用

安装后可直接 import；配置由调用方提供，检索结果通过返回值交付。

```python
from litsearch import Config, QueryPlan, SearchClient

client = SearchClient(Config(api_keys={"openalex": "your-key"}))
result = client.search(QueryPlan(
    queries=["marine protist grazing"],
    sources=["openalex"],
    limit=100,
    max_results=30,
))

for record in result.records:
    print(record.doi, record.title)

payload = result.to_dict()
```

## 文档

- [CLI、配置与检索工作流](docs/usage.md)
- [Python Interface 与 JSON 契约](docs/python.md)
- [兼容性与迁移](docs/compatibility.md)
- [开发、测试与构建](docs/development.md)

## 开发

```bash
uv sync --locked --extra dev
uv run --locked --extra dev python -m pytest
uv run --locked --extra dev ruff check .
uv run --locked --extra dev mypy
uv build
uv run --locked --extra dev python scripts/check_distribution.py
```

所有自动化检索测试均使用离线响应。许可证：MIT。
