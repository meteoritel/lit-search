# Domain docs

本仓库采用 single-context：根目录 `GLOSSARY.md` 保存领域术语，`docs/adr/` 保存架构决策。

## 阅读规则

探索代码库前，读取已有的 `GLOSSARY.md`，并读取 `docs/adr/` 中与当前任务相关的 ADR。

文件缺失时继续工作。`domain-modeling` 在术语得到确认或需要记录已确定的架构决策时，按需创建相应文档。

## 术语与决策

Issue 标题、设计建议、假设与测试名称使用术语表定义的领域词汇。遇到未收录的概念时，先核对项目用语；确认存在术语缺口后，由 `domain-modeling` 补充。

建议与现有 ADR 冲突时，明确指出对应 ADR 与重新讨论的理由。
