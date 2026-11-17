# 智能运营决策分析平台

面向用户行为序列分析、RFM分层、流失预警、智能推荐、归因建模、活动效果评估与实时运营决策的数据分析工程平台。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 行为序列 | `sequence` | 序列模式 | 模式编号、用户分群、事件序列 |
| RFM分层 | `rfm` | 分层规则 | 规则编号、最近消费天界、消费频次界 |
| 流失预警 | `churn` | 预警模型 | 模型编号、模型名称、特征变量 |
| 智能推荐 | `recommend` | 推荐策略 | 策略编号、推荐场景、召回算法 |
| 归因建模 | `attrmodel` | 归因模型 | 模型编号、模型名称、归因逻辑 |
| 活动评估 | `activity` | 活动方案 | 方案编号、活动名称、活动类型 |
| 实时决策 | `realtime` | 决策规则 | 规则编号、触发事件、决策条件 |
| 预算分配 | `budget` | 预算方案 | 方案编号、预算总额、渠道分配比例 |
| LTV估算 | `lifetime` | LTV模型 | 模型编号、模型名称、估算方法 |
| 实验变体 | `abvariant` | 变体方案 | 变体编号、关联实验、变体名称 |
| 运营看板 | `dashboard` | 看板视图 | 视图编号、视图名称、指标组合 |
| 评分卡 | `scorecard` | 评分卡 | 评分编号、评分对象、特征变量组 |
| 聚类分群 | `cluster` | 聚类任务 | 任务编号、聚类算法、特征维度 |
| 对比分析 | `comparison` | 对比组 | 对比编号、对比名称、实验组筛选 |
| 数据漂移 | `datadrift` | 漂移检测 | 检测编号、监控数据集、基线版本 |
| 推送计划 | `pushplan` | 推送方案 | 方案编号、推送内容、目标人群 |
| 指标归因 | `metricdrill` | 归因分析 | 分析编号、目标指标、波动方向 |
| 策略库 | `tacticlib` | 运营策略 | 策略编号、策略名称、策略类型 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
- 同一路由文件里，`/export`、`/metrics` 这类静态段必须声明在 `/{entry_id}` 之前，
  否则会被当作 entry_id 解析（int 校验失败 → 422）。这是「导出/回查卡住」的接口断点。

## 实验变体 ↔ 运营看板指标链路

`DASH-0003` 是结果回查样例：实验分流看板，告警线 5.0%。

状态机（跳级会被拦下）：`草稿 → 变体审批中 → 已生效 → 已下线`。
`下线变体` 是唯一负向动作，会在同一事务里完成下面这条链路：

1. 把变体状态改为 `已下线`；
2. 从**异常批次**（`ab_batch` / `ab_batch_record`）读取该视图当前对比周期的分流记录；
3. 只保留状态快照为「已生效」且未被剔除、未重复的记录，重算当前窗口指标；
4. 生成/复用指标明细数据集（`abmetric_dataset` / `abmetric_detail`），带 `数据集编号`、
   `指标版本`、`数据快照编号` 和 `落库凭证`；变体记录同步写回 `指标数据集编号`/`指标版本`；
5. 把新数据集登记到运营看板待处理明细（`dashboard_pending`）；
6. 同步告警（`ab_alert`）与订阅口径为「剔除非生效分流」；
7. 历史窗口只追加「来源已下线」标注，**不改**历史指标值与已确认的告警时点。

数据集指纹只取「视图 + 对比周期 + 告警线 + 生效变体的分流/曝光/转化」，因此：
- 下线回执与看板刷新落在同一对比周期时，只会得到**一份**有效数据集（旧数据集标记
  `被替代数据集`，仍可按快照回查）；
- 高并发重复下线按幂等处理，所有请求返回同一个 `数据集编号` 和落库凭证。

失败处理：看板未发布、对比周期无可用批次等情况会拒绝刷新，并把变体状态回滚，
避免「已下线但看板仍在计数」。当没有生效分流时，指标值为 `null`、告警状态为 `空态`
（页面显示「暂无数据」），不会用 0 冒充。

回查接口：

```bash
curl 'http://127.0.0.1:8000/api/dashboard/metrics?view=DASH-0003'   # 指标明细数据集（含明细）
curl 'http://127.0.0.1:8000/api/dashboard/pending?view=DASH-0003'   # 待处理明细
curl 'http://127.0.0.1:8000/api/dashboard/alerts?view=DASH-0003'    # 告警与订阅口径
curl 'http://127.0.0.1:8000/api/abvariant/metrics?view=DASH-0003'   # 从变体侧回查同一批数据集
```

前端：实验变体页执行下线后会展示落库凭证卡片；运营看板页可切换「数据集快照」回查
当前/历史窗口明细、告警与待处理明细。

## 验证

```bash
cd backend
.venv/bin/python -m tests.test_abmetric_flow
```

覆盖：草稿直接生效被拦下、下线残留、当前与历史指标差异、同周期双边界、并发只落一份
数据集、空态与暂无数据、失败回滚、导出接口不被动态路由遮蔽。

领域：数据工程
子方向：数据分析与数据应用
