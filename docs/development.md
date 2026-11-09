# 开发、测试与交付

## 环境与检查

要求 Python 3.12+ 和 uv。运行时仅使用标准库；开发依赖由 pyproject.toml 声明，uv.lock 固定解析结果。

在项目根目录执行：

```shell
uv sync --locked --extra dev
uv run --locked --extra dev python scripts/check.py
```

`scripts/check.py` 是统一验收入口，依次执行 Ruff、mypy、pytest、`uv build` 和分发检查。任一步失败即停止。本地与 GitHub Actions 使用同一命令；CI 配置覆盖 Windows / Linux 与 Python 3.12 / 3.14。

定位问题时可用 `uv run --locked --extra dev python -m pytest tests/test_cli.py` 单独运行相关回归。完整验收使用统一入口。

## 职责与数据流

```text
CLI → 配置发现 / 参数解析 → QueryPlan
    → SearchClient.search → 来源适配 → Http
                         → 身份合并 / 按需补摘要 → Http
                         → 主题过滤 / 排序 / 截断 → SearchResult
    → 渲染 / 终端与文件交付
```

`src/litsearch.py` 同时提供单文件运行、CLI 和 import 入口。SearchClient 负责完整检索；CLI 负责输入组织与交付副作用；来源适配隐藏查询、分页和字段差异；Http 支持 urllib 与测试 opener。

配置和检索意图共用校验规则。合并保留来源身份、逐查询排名与摘要来源；False 和 0 是有效值。主题过滤在合并和摘要补齐后执行；补摘要不增加检索投票，RRF 按独立来源与查询计票。契约见 [Python 文档](python.md)与 [CLI 文档](usage.md)。

## 离线测试

检索测试使用离线 opener 与内存响应，未安排的额外请求会使测试失败。正常请求等待与重试等待均受测试控制。新增网络行为应提供明确响应，并覆盖请求映射、异常结构、分页结束与诊断。

业务规则优先经过 `SearchClient.search` 或 CLI 验证；合并、文本匹配和排序保留有独立业务意义的单元回归。`tests/fixtures` 保存现役响应样例，以及摘要补齐回归使用的固定元数据和标签。

## 版本与分发

版本的唯一来源是源码中的 `__version__`，setuptools 从该属性生成元数据。wheel 包含运行模块与分发元数据；sdist 包含源码、现役测试及 fixtures、用户文档、配置示例和统一验收脚本。资料与计划档案不进入分发包。

统一验收在临时隔离环境离线安装当前 wheel，核对版本、安装后的 import 与 CLI、离线检索 JSON、配置解释、CP936 默认环境下的 UTF-8 输出，以及复制后的单文件运行；同时检查 sdist 资源与许可证元数据。构建产物位于 `dist/`。

来源协议调整时，先核对 [官方契约](python.md#外部来源契约)，再使用响应样例验证映射与分页。真实在线检索使用个人配置，凭据保存在环境变量或受忽略的配置文件中，请求地址中的凭据应脱敏。

## 文档与计划

- README 提供安装与最短调用路径。
- usage.md 维护 CLI、配置、筛选、排序和输出规则；python.md 维护 Python 接口与结果契约。
- docs/agents 维护 Agent 工作流约定，AGENTS.md 提供规则入口。
- 临时实施计划放入 docs/plans；完成验收后移至 docs/plans/archive，按 `YYYY-MM-DD-主题.md` 命名并更新[计划索引](plans/README.md)。
- 停用的研究报告和评测资料放入 docs/archive，并更新[资料索引](archive/README.md)。正式文档和代码注释只描述当前已确认行为。

`.local/` 用于受忽略的本地测试证据，`output/` 用于检索产物。归档原始日志和配置时保留其忽略边界，凭据不进入公开档案。
