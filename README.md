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

# 3. 执行每日扫描并产出 Markdown 日报（默认覆盖 10 国并自动生成跨市场总览）
gplay-radar run daily

# 按区域或指定国家运行（支持 na / eu / latam / sea_mena 或逗号分隔国家码）
gplay-radar run daily --region eu
gplay-radar run daily --region latam
gplay-radar run daily --region sea_mena
gplay-radar run daily --market de,br,id,sa

# 4. 生成近 7 天持续热词周报 & 跨市场总览
gplay-radar run weekly
gplay-radar run global-summary

# 5. 临时深挖任意国家/语言单个关键词的竞争格局与机会分
gplay-radar inspect "coin identifier" --country us --lang en
gplay-radar inspect "escaner pdf" --country mx --lang es
gplay-radar inspect "ماسح pdf" --country sa --lang ar
```

## 定时运行与钉钉推送

### 方式一：GitHub Actions 云端免费每日自动跑（推荐）
仓库已内置 [.github/workflows/daily_radar.yml](.github/workflows/daily_radar.yml)：
- 每天北京时间 **09:00**（`01:00 UTC`）自动运行，无需本机开机与代理；
- 历史 `data/radar.db` 与所有 Markdown 日/周报自动回存至独立 `data` 分支，支持直接在线查看并做每日 Diff；
- 在 GitHub 仓库 `Settings → Secrets and variables → Actions` 新增以下两个 Secret 即可自动推钉钉（多国自动合并成 **1 条**跨国总览卡片）：
  - `DINGTALK_WEBHOOK_URL`：钉钉群自定义机器人 Webhook 地址
  - `DINGTALK_SECRET`：钉钉机器人加签密钥（`SEC...`）

### 方式二：Windows 本机每日定时运行
- 注册 Windows 每日任务（默认每天 09:00）：
  ```powershell
  .\scripts\register_scheduled_task.ps1 -RunAt "09:00"
  ```
- 开启钉钉推送（设置系统环境变量即可自动生效）：
  ```powershell
  [Environment]::SetEnvironmentVariable("DINGTALK_WEBHOOK_URL", "https://oapi.dingtalk.com/robot/send?access_token=...", "User")
  [Environment]::SetEnvironmentVariable("DINGTALK_SECRET", "SEC...", "User")
  ```

## 常用配置调优（`config.yaml`）

- **增减赛道/种子词**：编辑 `charts.categories` 与 `suggest.seed_groups`。
- **扩展多国家监控**：在 `markets` 下新增条目即可（例如 `{country: "gb", lang: "en"}` 或 `{country: "jp", lang: "ja"}`），SQLite 主键已按 `(run_date, country, lang)` 隔离。
- **控制请求量**：调整 `suggest.az_letters_per_day`（默认每日轮转 6 个字母）与 `funnel.max_serp_candidates`（默认每日深挖前 35 个候选词）。
