# 开发、测试与交付

## 环境与检查

要求 Python 3.12+ 和 uv。运行时仅使用标准库；开发依赖由 pyproject.toml 声明，uv.lock 固定解析结果。

在项目根目录执行：

```shell
uv sync --locked --extra dev
uv run --locked --extra dev python scripts/check.py
```

`scripts/check.py` 是统一验收入口，依次执行 Ruff、mypy、pytest、`uv build` 和分发检查。任一步失败即停止。本地与 GitHub Actions 使用同一命令；CI 配置覆盖 Windows / Linux 与 Python 3.12 / 3.14，并通过 `workflow_call` 供 Release 工作流复用。

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

版本的唯一来源是源码中的 `__version__`，setuptools 从该属性生成元数据。wheel 包含运行模块与分发元数据；sdist 包含源码、现役测试及 fixtures、用户文档、环境变量模板和统一验收脚本。本地专题预设保存在受 Git 忽略的 `.local/presets/`；资料与计划档案、本地测试配置均不进入分发包。

统一验收在临时隔离环境离线安装当前 wheel，核对版本、安装后的 import 与 CLI、离线检索 JSON、配置解释、CP936 默认环境下的 UTF-8 输出、CSV stdout 与文件字节一致性，以及复制后的单文件运行；同时检查发布 tag 的接受与拒绝、sdist 资源与许可证元数据。构建产物位于 `dist/`。

### Windows x64 便携包

便携版使用 Windows x64 Python 3.12 和 PyInstaller `onedir`，自带解释器及运行依赖。PyInstaller 属于锁定的 `release` 开发依赖组，按 Windows / Python 3.12 条件安装；wheel 的运行时依赖保持为空。

在 Windows x64 上执行：

```shell
uv sync --locked --python 3.12 --extra dev --group release
uv run --locked --python 3.12 --extra dev --group release python scripts/check.py
uv run --locked --python 3.12 --extra dev --group release python scripts/release.py build
```

`scripts/release.py` 复用源码版本，检查 wheel 与当前源码一致，在临时目录构建便携包，附入 README、项目及 Python/PyInstaller 许可证、`.env.example` 和 CLI/Python 文档。构建过程只复制明确列出的用户资料，不读取本机凭据文件。

验收从 ZIP 解压产物，在带中文和空格的路径、独立调用目录、隔离配置以及不含 Python 的 PATH 中执行，覆盖版本、帮助、UTF-8 输出、错误退出码、`--explain` 和当前目录 TOML 发现。所有检查通过后才替换 `dist/release/` 中的发布附件：

- `litsearch-<版本>-windows-x64.zip`：完整的 `litsearch/` 目录，包含 `litsearch.exe`、`_internal/` 和随附资料。
- `litsearch-<版本>-py3-none-any.whl`、`litsearch-<版本>.tar.gz`：已验收的 Python 分发产物。
- `SHA256SUMS`：上述附件的 SHA-256 校验值。

### GitHub Release

`.github/workflows/release.yml` 在推送 `v*` tag 时触发。工作流先在 `verify-tag` 作业快速核对 tag 严格等于 `v<源码 __version__>`，不匹配立即失败；随后调用完整 CI 矩阵，取 Windows / Python 3.12 已验收的 wheel 和 sdist，构建并验收便携 ZIP。

发布作业只在构建成功后运行，核对附件校验和，使用 GitHub Actions 自动注入的 `GITHUB_TOKEN` 创建草稿 Release、上传全部附件并公开发布。只有该作业授予 `contents: write`（需确保仓库 Actions 设置允许工作流请求写权限，无需额外配置 PAT 或 PyPI 凭据）。Release 使用 GitHub 自动生成的说明，已有同名 Release 时停止，不覆盖既有附件；若创建草稿后公开阶段因瞬时网络失败，可在 GitHub 网页手动发布或删除残留草稿后重跑。

发布时先更新源码 `__version__`，提交并推送经过验收的代码，再为该提交创建对应 tag。例如源码版本为 `0.5.0` 时：

```shell
git tag v0.5.0
git push origin v0.5.0
```

上述推送会自动发布 GitHub Release。项目发布渠道为 GitHub Releases，PyPI 同名项目不属于本仓库。

来源协议调整时，先核对 [官方契约](python.md#外部来源契约)，再使用响应样例验证映射与分页。真实在线检索使用个人配置，凭据保存在环境变量或受忽略的配置文件中，请求地址中的凭据应脱敏。

## 文档与计划

- README 提供安装与最短调用路径。
- usage.md 维护 CLI、配置、筛选、排序和输出规则；python.md 维护 Python 接口与结果契约。
- docs/agents 维护 Agent 工作流约定，AGENTS.md 提供规则入口。
- 临时实施计划放入 docs/plans；完成验收后移至受 Git 忽略的 `.local/archive/`，按日期与主题归档。
- 停用的研究报告和评测资料保存在 `.local/archive/`。正式文档和代码注释只描述当前已确认行为。

`.local/` 用于受忽略的本地测试证据，`output/` 用于检索产物。归档原始日志和配置时保留其忽略边界，凭据不进入公开档案。
