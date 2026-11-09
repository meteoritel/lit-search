# litsearch

多源文献检索 CLI 与 Python Module，Python 3.12+，运行时仅使用标准库。

- 开发检查与构建命令见 [开发文档](docs/development.md#环境与检查)。
- 修改检索流程、结果字段或配置时，先读 [Python 契约](docs/python.md) 与 [CLI 规则](docs/usage.md)，同步受影响的测试和文档。
- 保留 src/litsearch.py 的单文件交付和 import/CLI 入口；SearchClient 负责完整检索，CLI 负责终端与文件输出。
- tests 使用离线 opener 与受控等待；新增网络行为在测试中提供明确响应。
- 版本以源码 __version__ 为准；分发包检查使用 scripts/check_distribution.py。
- 凭据保留在环境变量或受忽略配置中，结果与错误中的请求地址脱敏。
- 正式文档描述当前契约；实施计划完成后归档到 docs/plans/archive 并更新索引。
