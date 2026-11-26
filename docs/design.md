# Google Play Keyword Radar — 系统设计文档

## 1. 背景与目标

本系统面向出海 C 端产品经理的**赛道选品与新热词发现**场景：
- **核心目标**：以零 API 订阅成本，持续监控 Google Play（首期聚焦 US / 英文，架构天然支持多国扩展）重点品类榜单与核心种子词，捕捉「**新起量 + 非品牌 + 竞争可切入**」的蓝海需求词。
- **运行形态**：无前端 Web 界面，纯 Python CLI 脚本定时运行，数据持久化至本地 SQLite 时序库，每日/每周产出可执行的 Markdown 简报，并支持可选钉钉自定义机器人 Webhook 推送。

## 2. 整体架构与模块边界

采用四段式漏斗流水线，每个模块职责单一、可独立单测：

```text
config.yaml -> [1. Discovery] -> SQLite -> [2. Filter & Diff] -> [3. Enrich] -> SQLite -> [4. Scoring] -> [5. Reporting]
```

| 模块 | 路径 | 职责 |
|---|---|---|
| 配置加载 | `src/gplay_radar/config.py` | 读取与校验 `config.yaml`，处理环境变量与代理继承 |
| 数据模型 | `src/gplay_radar/models.py` | 全链路强类型 Dataclass 定义 |
| 采集层 | `src/gplay_radar/collectors/` | 统一限频客户端 + `suggest` / `charts` / `serp` / `app_detail` / `trends` 五个独立适配器 |
| 存储层 | `src/gplay_radar/storage/` | SQLite 建表、当日幂等写入、7d/30d 历史窗口查询、App 详情 7 天本地缓存 |
| 分析与评分 | `src/gplay_radar/pipeline/` | 词形清洗、标题 n-gram 拆词、历史 Diff 与防抖、可切入度评分、自动品牌识别 |
| 简报与推送 | `src/gplay_radar/reporting/` | 日报/周报 Markdown 渲染、钉钉加签 Webhook 推送 |
| 命令行入口 | `src/gplay_radar/cli.py` | `run daily` / `run weekly` / `inspect <kw>` / `init-db` / `check-health` |

## 3. 公开数据源与抗限流降级

1. **Google Play 联想词（Suggest）**：调用 Google Play Web 原生 `batchexecute` RPC `IJ4APc`（`hl=en&gl=us`），采集核心种子词、按日轮转的 `a-z` 扩展以及榜单黑马 App 反推词根。
2. **Google Play 分类榜单（Charts）**：调用原生 `batchexecute` RPC `vyAe2`（精简请求体），每日拉取关注品类的 Top Free（前 100）和 Top Grossing（前 50），直接获取排名、包名、标题、安装量级与简介，识别**榜单黑马 App**（排名跃升 `>=15`、新进榜或安装量 `<=100 万` 却进前排的 App），并对其标题拆词反哺到 Suggest。
3. **搜索结果与竞品详情（SERP & Detail）**：仅对漏斗筛出的 Top 35 候选词执行搜索（`google_play_scraper.search`）与详情补齐（`google_play_scraper.app`），获取精确安装数 `realInstalls`、首次发布时间 `released`、评分数与短描述；详情缓存在 `apps_cache` 7 天，避免重复抓取巨头 App。
4. **Google Trends 交叉验证**：对 SERP 筛选后的 Top 20 词（每批 5 词打包，仅 4 次请求）通过 Cookie 预热 + `/trends/api/explore` + `/trends/api/widgetdata/multiline` 获取 US 近 30 天日级曲线。设为**可降级加分项**：若遇 429，记录 `degraded` 告警且照常出报。

**稳定性保障**：
- 统一随机抖动节流（`0.6s–1.5s`）、429/5xx 指数退避重试、每日请求预算上限（默认 600 次）。
- SQLite 主键包含 `(run_date, country, lang, ...)`，支持同日重跑幂等跳过已抓条目。

## 4. SQLite Schema 与选品机会评分模型

### 4.1 核心数据表
- `runs`：运行记录、状态、请求消耗、降级源列表。
- `suggest_snapshots`：每日每种子词的联想结果与顺位。
- `chart_snapshots`：每日每品类每榜单的 App 排名、安装量级与标题。
- `apps_cache`：App 详情元数据缓存（7 天 TTL）。
- `serp_snapshots`：每日候选词的 SERP Top 10 结果。
- `trends_snapshots`：每日候选词的 Google Trends 7d/30d 斜率与原始序列。
- `keyword_scores`：每日最终评分、分项得分、品牌标记与 Top 竞品摘要。

### 4.2 机会评分模型（0–100 分）
- **热度异动分 `momentum_score`（0–40 分）**：
  - 有历史基线时（`diff` 模式）：新进联想词、对比 7 天滚动中位数的顺位跃升（防单日抖动）、多种子词共现宽度、关联榜单黑马 App 数量与涨幅。
  - 冷启动首日（`baseline` 模式）：明确标记为「基线扫描」，依据联想前排占位、多种子共现与榜单低装高排黑马信号排序，不虚报涨幅。
- **竞争可切入度分 `entry_score`（0–45 分）**：
  - SERP Top 10 中新发布 App 占比（`<365 天` 及 `<180 天` 额外加权）；
  - 中小体量 App 占比（`<100 万` 安装及 Top 5 `<50 万`）；
  - 非巨头垄断空间（Top 5 中 `>=5000 万` 巨头越少分越高）；
  - ASO 供给缺口（Top 10 标题未精确覆盖该词的比例，反映标题优化空白）。
- **外部趋势加成 `trends_score`（0–15 分）**：
  - 近 7 天均值对比前 21 天均值的涨幅斜率；Trends 降级时给中性基准分 5 分并在报告标注。
- **自动品牌词识别与分流**：
  - 结合静态大厂黑名单 + SERP 启发式规则（若 #1 App 品牌前缀/开发商与关键词高度重合，且 #1 为千万级巨头或 Top 10 中其余竞品极少包含该词），自动标记 `is_brand=True`，主榜降权并分流至「品牌词飙升观察」子表，确保主榜聚焦通用需求机会词。
