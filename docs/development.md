# 开发、测试与交付

## 环境与检查

Python 3.12+，运行时仅标准库。开发依赖由 pyproject.toml 声明，uv.lock 固定解析结果。

```bash
uv sync --locked --extra dev
uv run --locked --extra dev python -m pytest
uv run --locked --extra dev ruff check .
uv run --locked --extra dev mypy
uv build
uv run --locked --extra dev python scripts/check_distribution.py
```

Ruff 检查运行错误和无效代码；mypy 检查运行模块及有类型标注的调用链。GitHub Actions 在 Windows/Linux 的 Python 3.12 和 3.14 环境执行测试、静态检查、构建及安装产物检查。

检索测试使用 FakeOpener 和内存响应，主动阻止未安排的额外请求。涉及 HTTP 的测试同时控制正常请求等待与重试等待。新增来源行为应覆盖请求映射、异常响应、分页结束与结果诊断。

## 职责与依赖方向

```text
CLI Adapter → 配置发现/参数解析 → QueryPlan
            → SearchClient.search → Provider Adapter → Http
                                  → 合并/主题过滤/排序 → SearchResult
            → 渲染/文件交付
```

源码继续保留单文件交付。SearchClient 是完整检索 Module 的 Interface；CLI 组织输入与交付副作用；Provider Adapter 隐藏来源查询、分页与字段差异；Http 内部 Seam 支持真实 urllib 与测试 opener。

配置与检索意图的校验覆盖 CLI 和 Python 两条入口。记录的 False/0 是有效值；合并时保留来源身份与空值回填语义。主题过滤在跨源合并后执行。新增业务规则应从完整检索 Interface 验证，并保留有独立领域价值的合并和匹配案例。

## 版本与构建

版本的唯一来源为 src/litsearch.py 中的 __version__，setuptools 从该属性生成分发元数据。wheel 交付运行模块与元数据；sdist 同时包含测试、fixtures、用户文档和交付检查脚本。计划档案不进入分发包。

`check_distribution.py` 在临时隔离环境安装当前版本 wheel，检查入口、来源位置、离线检索 JSON、配置解释与独立单文件运行，并核对 sdist 的验证资源和许可证元数据。

来源协议调整时先查[官方依据](python.md#外部来源契约)，使用响应样例增加契约回归；真实在线检索使用个人配置，凭据留在环境变量或受忽略的配置文件中。

## 文档与计划

README 提供安装与最短调用路径；usage.md 描述 CLI/配置，python.md 描述 Python/结果契约，compatibility.md 描述当前迁移动作。

临时实施安排放入 docs/plans，完成验收后移入 docs/plans/archive，并更新计划索引。正式文档与代码注释描述已实现行为。
