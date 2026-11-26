# Google Play Keyword Radar

面向出海 C 端产品经理的**零成本 Google Play 选品热词监控与蓝海机会发现系统**。

无前端界面、无付费 API 依赖，纯 Python CLI + 本地 SQLite 时序快照，每日自动输出可执行的 Markdown 日报/周报，并支持可选钉钉机器人推送。

## 核心能力

1. **双源广度发现**
   - **Google Play 原生搜索联想词（`IJ4APc` RPC）**：围绕自定义赛道种子词 + 每日轮转 `a-z` 扩展 + 意图前缀（`ai` / `free`），发现近期新冒头与顺位跃升词。
   - **Google Play 分类榜单黑马反推（`vyAe2` RPC）**：监控关注品类 Top Free 100 / Top Grossing 50，自动揪出「低安装进前排」或「排名飙升 ≥15 名」的黑马 App，拆出标题/简介需求词并反哺联想词验证，打破预设种子词盲区。
2. **选品机会导向评分（0-100 分）**
   - **热度异动分（40 分）**：新冒头、对比 7 天滚动中位数跃升（抗单日抖动）、多种子共现、关联榜单黑马。
   - **竞争可切入度（45 分）**：查搜索前 10 的真实发布时间与安装量级，优先排序「半年/一年新包占比高 + 中小体量包可进前 5 + 非巨头垄断 + 标题优化有缺口」的词。
   - **Google Trends 交叉验证（15 分）**：US 近 30 天日级斜率加成；遇 429 自动降级且不阻塞出报。
   - **自动品牌词识别与分流**：静态黑名单 + SERP #1 独占品牌启发式识别，自动把品牌词分流到独立观察区，保持主榜干净。

## 快速使用

```powershell
cd d:\antigravity-workspace\gplay-keyword-radar

# 1. 可选：以可编辑模式安装 CLI（安装后可直接运行 gplay-radar）
pip install -e . --no-build-isolation --no-deps

# 2. 检查代理与 4 个公开数据源连通性
python -m gplay_radar.cli check-health

# 3. 执行每日扫描并产出 Markdown 日报
python -m gplay_radar.cli run daily

# 4. 生成近 7 天持续热词周报
python -m gplay_radar.cli run weekly

# 5. 临时深挖任意单个关键词的竞争格局与机会分
python -m gplay_radar.cli inspect "coin identifier"
```

## 定时运行与钉钉推送

- **Windows 每日定时运行**（默认每天 09:00）：
  ```powershell
  .\scripts\register_scheduled_task.ps1 -RunAt "09:00"
  ```
- **开启钉钉自定义机器人推送**：
  1. 在 `config.yaml` 中将 `reporting.dingtalk.enabled` 设为 `true`；
  2. 通过环境变量配置机器人地址与加签密钥（推荐，避免密钥落盘）：
     ```powershell
     $env:DINGTALK_WEBHOOK_URL = "https://oapi.dingtalk.com/robot/send?access_token=..."
     $env:DINGTALK_SECRET = "SEC..."
     ```

## 常用配置调优（`config.yaml`）

- **增减赛道/种子词**：编辑 `charts.categories` 与 `suggest.seed_groups`。
- **扩展多国家监控**：在 `markets` 下新增条目即可（例如 `{country: "gb", lang: "en"}` 或 `{country: "jp", lang: "ja"}`），SQLite 主键已按 `(run_date, country, lang)` 隔离。
- **控制请求量**：调整 `suggest.az_letters_per_day`（默认每日轮转 6 个字母）与 `funnel.max_serp_candidates`（默认每日深挖前 35 个候选词）。
