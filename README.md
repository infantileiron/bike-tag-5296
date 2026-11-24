# 岑导骑行·公共车桩标牌补装预算

按街区桩位总量加权估计缺牌数量，输出有限总体修正后的预算区间。

## 模拟场景

[业务剧本](yangyang-business.md)记录团队、上游资料、计算口径和交接去向。业务模块包括抽桩与总体台账、分层缺牌估计、补装量预算。

观测以 CSV 导入或逐行登记。后台按提交时的观测快照运行 Python 算法；负责人签核后，使用方可查看结果、下载 CSV 或按计划接收报告。

## 启动

```sh
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```

浏览器打开 `http://127.0.0.1:18414`。API 健康检查位于 `http://127.0.0.1:19414/api/health`，数据库本地端口 `17914`。停机用 `docker compose down`。只有要重做演示数据时才加 `-v` 清除命名卷。

## 演示账号

| 角色 | 邮箱 | 演示密码 | 可执行动作 |
|---|---|---|---|
| 业务分析员 | `analyst@demo.local` | `Analyst-2026!` | 登记或导入样本、提交分析、下载内部结果 |
| 复核负责人 | `supervisor@demo.local` | `Supervisor-2026!` | 导入资料、签核或退回、安排或停止分发、查审计 |
| 只读查看 | `viewer@demo.local` | `Viewer-2026!` | 查看台账、已签核结果和本地收件箱 |

`.env` 以 `.env.example` 为模板。`FRONTEND_PORT`、`BACKEND_PORT`、`DB_PORT` 可做本机端口调整。Docker 私有子网可通过 `NETWORK_SUBNET` 覆盖。`JWT_SECRET` 和 `DB_PASSWORD` 为本地演示配置。`SEED_DEMO=false` 可跳过自动种子。所有公开端口绑定在回环地址。首次执行种子后，后续种子只填缺失观测，不覆盖已录入数据。

## 模块和本地开发

| 服务 | 技术 | 用途 |
|---|---|---|
| frontend | HTML、CSS、ES Modules、Nginx | 工作台、录入与报告 |
| backend | Python 3.12、FastAPI、SQLAlchemy | 校验、快照、业务 API 和 JWT |
| worker | Python 3.12、Redis | 计算排队、心跳和到时导出 |
| db | PostgreSQL 16 | 观测、快照、签核、分发计划和审计 |
| redis | Redis 7 | 唤醒、心跳和登录限流 |

在 `backend` 中可用 `pip install -r requirements.txt` 安装本地依赖，设置 `DATABASE_URL`、`REDIS_URL` 和可写 `ARTIFACT_ROOT` 后运行 `uvicorn src.main:app --port 8000`，计算进程运行 `python -m src.worker`。前端修改在 `frontend/src`，用 `docker compose up -d --build frontend` 通过 Nginx 检查同源代理。业务输入定义在 `backend/src/domain/spec.json`，算法在同目录的 `analysis.py`。角色、任务状态和签核状态在后端 `backend/src/constants/enums.py` 和前端 `frontend/src/constants/enums.js` 共同定义。数据库表在 `backend/src/models/workflow.py`。

分发计划锁定特定已签核快照，按北京时间显示。收件箱可下载真实 CSV，当前收件组是本地交接标签。停机跨过多个周期时补送最早未投递的一期，然后追到下一个将来的周期。新快照需重新签核并建新计划。

## 补送更正与认账台账

观测以「街区 + 抽样批次」为认账键，带更正版次（留空为第 1 版）。补送更正按原街区批次认账，抽检数/缺牌数随新版整体替换；乱序到达的旧版只留存审计不顶新版，重试入账幂等不新增批次。同一认账键同版出现两套不同数据时，该街区暂不出采购量，其余街区照常备料，汇总和 CSV 标清受影响街区。发起分析时固化当时的认账台账，历史分析按旧账算，更正后新分析按新账重算。详见[业务剧本](yangyang-business.md)。

## 测试

测试不依赖第三方库，用标准库 `unittest` 运行；集成测试通过 `tests/_sa_shim.py` 的 sqlite3 替身验证数据库行为（装有真 SQLAlchemy 的环境行为一致）：

```sh
cd backend
python3 tests/test_ledger_rules.py      # 认账台账与分析算法规则（12 项）
python3 tests/test_ledger_services.py   # 入账、快照、迁移、CSV 端到端（10 项）
```

许可证为 [MIT](LICENSE)。
