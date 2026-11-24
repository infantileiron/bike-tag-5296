# 数据表初始化

本版通过 `src.models.base.Base.metadata.create_all` 创建首次运行所需的表。模型在 `src/models/workflow.py`。`python -m scripts.seed_demo` 按观测内容键补全演示样本，保留现有分析及签核。数据库端口只发布到本机。

## 补送更正版的在线迁移

历史库没有认账键列、版次列和分析快照列。应用启动（`src.main`  lifespan）和 worker 启动都会调用 `src.migrations.online.ensure_schema`，它用反射判断缺哪些列并幂等补齐：

* `observations` 增加 `district`、`batch`、`revision`，并从 `payload` 回填历史记录（旧记录按第 1 版认账），同时建立 `(district, batch)` 索引。
* `analysis_runs` 增加 `ledger_snapshot`（JSON），保存发起分析时认定的那一版认账台账。

迁移可重复执行；全新库由 `create_all` 直接建出含新列的表，迁移步骤自动跳过已存在的列。PostgreSQL 与本地测试用的 SQLite 都支持该流程。
