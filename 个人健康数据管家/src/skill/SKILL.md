---
name: health-data-steward
description: 操作手册——健康数据管家如何把体重秤照片、餐食照片与口述内容写进本地 SQLite 库，并生成 HTML 日报。当用户发来秤面照片、餐食照片、描述吃了什么，或要求出健康日报 / 看趋势时加载。
---

# 健康数据管家 · 操作手册

本 skill 是操作层文档：**项目在哪、用哪个解释器、有哪些脚本、怎么调、出问题查什么**。

## 零、首次使用：一键配置

新用户的第一次，**只跑两条命令**：

```bash
"$PY" <SKILL_DIR>/scripts/bootstrap.py --root ~/health-data \
  --height 175 --age 45 --unit kg --target-weight 72 --no-input
"$PY" <SKILL_DIR>/scripts/selfcheck.py --root ~/health-data
```

- `bootstrap.py`：定根目录（默认 `~/health-data`，**拒绝云同步目录**）→ 记解释器 → 装 PyYAML
  → 建目录 → 生成 `config/config.yaml` → 建库 → 写 `.setup_done.json`
- `selfcheck.py`：出 ✅/⚠️/❌ 清单；有 ❌ 不许往下走
- 之后所有脚本都用 `--root <PROJECT_ROOT>` 调（也可设环境变量 `HEALTH_ROOT`）

> **首次务必拿到**：身高、年龄、目标体重、体重单位。没有这些，BMR、热量缺口、蛋白参考量都算不出来。
## 一、环境速查

**路径约定**：本 skill 内所有 `<SKILL_DIR>` 指本 skill 所在目录；`<PROJECT_ROOT>` 指用户的数据项目根目录（内含 `data/` `config/` `reports/`）。


| 项 | 值 |
|---|---|
| 本 skill 目录 | `<SKILL_DIR>`（即 `skills/health-data-steward/`） |
| 脚本目录 | `<SKILL_DIR>/scripts/` |
| 配置模板 | `<SKILL_DIR>/templates/config.example.yaml` |
| 项目根 | `<PROJECT_ROOT>`（用户自定，默认 `~/health-data`） |
| 主库 | `<PROJECT_ROOT>/data/health.db`（SQLite，WAL 模式） |
| Python | 用 WorkBuddy 托管的解释器，见下 |
| 个人配置 | `<PROJECT_ROOT>/config/config.yaml`（身高、目标、**体重单位**、拳宽） |
| 照片来源 | **对话附件**（`--photo` 一律不传；源文件由 WorkBuddy 附件机制持有，脚本不接触，不留档） |
| 报告输出 | `<PROJECT_ROOT>/reports/report-<日期>.html` |
| 录入台账 | `<PROJECT_ROOT>/data/intake_log.jsonl`（每次录入写的 blob_id，供漏记巡检对账） |
| 附件台账（外部） | `~/.workbuddy/projects/*/*.jsonl` 里的 `image_blob_ref` + `~/.workbuddy/blobs/` |

**解释器**：不要写死绝对路径（换机器就失效）。按下面顺序探测，取第一个存在的：

```bash
# 优先用 WorkBuddy 托管的 venv
PY="$HOME/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
[ -x "$PY" ] || PY="$HOME/.workbuddy/binaries/python/envs/default/bin/python"
# 兜底：系统 Python
[ -x "$PY" ] || PY="$(command -v python3 || command -v python)"
"$PY" --version
```

若托管解释器不存在，用系统 Python 即可（脚本只用标准库 `sqlite3` / `yaml`，
若缺 PyYAML：`"$PY" -m pip install pyyaml`）。

**工作目录**：所有脚本都支持从任意目录用绝对路径调用，不要 `cd` 进项目再跑（避免工作目录漂移）。
脚本内部按 `--root` 或环境变量 `HEALTH_ROOT` 定位项目；都没给时默认 **`~/health-data`**。
推荐做法：显式传 `--root <PROJECT_ROOT>`（相对路径按当前目录解析）。

## 二、脚本清单

### `bootstrap.py` / `selfcheck.py` —— 首次配置与自检

见第零节。二者都不需要用户干预（除非装 PyYAML 失败）。


### `record_body.py` —— 体重 / 体脂 / 腰围录入

```bash
# 拍秤面照片录入（单位默认「斤」，脚本自动换算成 kg 存库）
# 照片直接从对话附件读，不传 --photo（源文件不归脚本管，也不留档）
# --blob-id 是对话附件的 sha256 指纹，带上它漏记巡检才认得出"这张已录"
"$PY" <PROJECT_ROOT>/scripts/record_body.py --weight <斤数> --blob-id <sha256>

# 顺带录腰围（cm，周频）
"$PY" <PROJECT_ROOT>/scripts/record_body.py --weight <斤数> --waist <cm>

# 临时按公斤录入（覆盖 config 的单位设置）
"$PY" <PROJECT_ROOT>/scripts/record_body.py --weight <kg> --unit kg

# 查看历史
"$PY" <PROJECT_ROOT>/scripts/record_body.py --show
```

**内置防线**：体重换算后若 ≥100 kg 会被拒绝（防「斤」当 kg 误录）；超出 25–250 kg 一律拒写。

### `record_meal.py` —— 餐食录入

```bash
# 区间录入（推荐）
"$PY" <PROJECT_ROOT>/scripts/record_meal.py --meal-type lunch --desc "家做西红柿炒蛋+米饭一碗+一拳青菜" \
  --place 家 --kcal-low <下限> --kcal-high <上限> --protein-low <下限> --protein-high <上限> \
  --veg 1 --carb mid --confidence mid --blob-id <sha256>

# 只给点值 → 自动展开 ±15% 并标注
"$PY" <PROJECT_ROOT>/scripts/record_meal.py --meal-type breakfast --desc "水煮蛋两个" --kcal <值> --protein <值>

# 发酵食物与钠（可选，两项都只影响趋势图与结构判定，不进热量核算）
"$PY" <PROJECT_ROOT>/scripts/record_meal.py --meal-type lunch --desc "米饭+鸡胸+泡菜" \
  --kcal-low <下限> --kcal-high <上限> --ferment 2 --ferment-desc "无糖酸奶 200g" \
  --sodium-low 600 --sodium-high 900

# 存进个人份量库 / 从份量库复用
"$PY" <PROJECT_ROOT>/scripts/record_meal.py --meal-type lunch --desc "食堂青椒炒肉" \
  --kcal-low <下限> --kcal-high <上限> --save-to-library
"$PY" <PROJECT_ROOT>/scripts/record_meal.py --meal-type lunch --desc "青椒炒肉" --from-library "食堂青椒炒肉"

# 查看
"$PY" <PROJECT_ROOT>/scripts/record_meal.py --show
"$PY" <PROJECT_ROOT>/scripts/record_meal.py --day <YYYY-MM-DD>
```

**发酵食物（`--ferment`）计数口径**：只计**含活菌、吃前没有再加热**的。1 份 = 发酵乳 100 g / 发酵豆制品 30 g / 发酵蔬菜 50 g / 味噌 10 g / 发酵饮品 200 ml。加热过的（泡菜下锅、纳豆拌热饭）、馒头面包醋酱油豆瓣酱、巴氏杀菌与常温货架产品**一律不计**；腐乳 10 g 折 1 份。目标见 `goals.ferment_servings_target`（默认 2 份/天，3 份良好），依据见 `references/report-format.md`。

**钠（`--sodium-low/high`）**：**历史行不回填**——回填等于拿今天的估算覆盖当时的口径。没填的日子钠图就是空的，这是诚实，不是缺失。


### `sync_band.py` —— 手环数据搬运（**出日报前必跑**）

MCP 拉来的数据**不会自动进主库**。链路是「云端 →（MCP）→ 源库 `mi_fitness.db` →（本脚本）→ 主库 `health.db`」，缺了最后一段，日报的手环板块就是空的。

```bash
# 指定区间（推荐：昨天 ~ 今天，含联网拉取）
"$PY" <PROJECT_ROOT>/scripts/sync_band.py --start <YYYY-MM-DD> --end <YYYY-MM-DD>

# 回溯 N 天
"$PY" <PROJECT_ROOT>/scripts/sync_band.py --days 7

# 不联网，只把源库现有数据搬进主库
"$PY" <PROJECT_ROOT>/scripts/sync_band.py --no-pull

# 只报会写什么，不落库
"$PY" <PROJECT_ROOT>/scripts/sync_band.py --dry-run
```

**前置**：需要先完成小米运动健康 MCP 安装与登录配对。**安装跑 `scripts/install_band_mcp.py`**（源码不随包分发，运行时从上游 git 拉取、直连失败自动换镜像；登录步骤见 `references/band-setup.md`）。安装后把 `config.yaml` 里 `sources.band.source_db` 与 `mcp_exe` 填上。

**特性与约束**：

- **幂等**：重复跑不产生重复行（UPSERT / INSERT-OR-IGNORE）。
- **绝不写 `body_metric`**：体重只认用户自己的秤（拍照录入）。手环 `body_measurements` 一律跳过，否则两套数据打架。
  （若已配置体脂秤接入，`write_body_metric` 会按晨起窗 05:00–11:00 取当日首条写入，且**先查后写、绝不覆盖**。）
- **不臆造**：源库没有的字段留 NULL。
- **异常预警**：单日 `>60000 步` 或 `>60 km` → 输出 ⚠️（MCP 的活动聚合是纯累加，手机+手环同时上报会翻倍）。**见到警告要在回复里如实转述。**
- **原始快照**：落 `data/raw/mi_fitness/<起>_<止>.json`。
- **来源标注**：全部写 `source='mi_fitness'`。⚠️ **设备来源不可判**——MCP 丢弃了 `sid`，源库分不出手机/手环。**不要说成"手环记录"**。
- **云端拉取失败不中断**：脚本会继续用源库现有数据，如实说明"本次未能联网拉取"即可。
- **睡眠归属**：按**起床日**（与小米运动健康 App 一致），不是入睡日。
  → 报告里的口径是「报告日 D 展示 D 夜 → D+1 晨那一觉」，即早上看到的"昨夜"（见 `references/report-format.md`）。

### `install_band_mcp.py` —— 安装 mi-fitness-mcp（小米云端取数，可选增强）

```bash
# 标准安装（源码运行时从上游拉取、直连失败自动换镜像；pip 依赖仍需 PyPI 网络）
"$PY" <SKILL_DIR>/scripts/install_band_mcp.py --root <PROJECT_ROOT>
"$PY" <SKILL_DIR>/scripts/install_band_mcp.py --root <PROJECT_ROOT> --index-url https://pypi.tuna.tsinghua.edu.cn/simple  # 国内慢时换镜像
"$PY" <SKILL_DIR>/scripts/install_band_mcp.py --root <PROJECT_ROOT> --no-extras   # 最小安装（无 Web 扫码仪表盘）
"$PY" <SKILL_DIR>/scripts/install_band_mcp.py --root <PROJECT_ROOT> --force       # 重装（重新解压 + 重建 venv）
"$PY" <SKILL_DIR>/scripts/install_band_mcp.py --root <PROJECT_ROOT> --dry-run     # 只报告计划，不动手
```

做 5 件事：git 拉源码（已有则复用）→ 探测 Python ≥3.11 → 落到 `<PROJECT_ROOT>/vendor/mi-fitness-mcp-cn/` → 建 venv 并 `pip install -e '.[all]'` → 验证 CLI 并打印 `mcp_exe` / 预期源库路径 / 登录指引。

**边界（话术要对上）**：
- **不碰登录**——扫码授权必须用户本人操作（`api` + `web` → `127.0.0.1:8322`，或 `setup` 手动 Cookie），详见 `references/band-setup.md`
- **不写 config.yaml**——`mcp_exe` / `source_db` 只打印建议值，由 Agent 与用户确认后填写；**源库路径以 `mi-fitness-mcp doctor` 输出为准**，脚本打印的"预期路径"只是预览
- 拉源码与 pip 依赖**都需要网络**；拉不到源码时脚本给「换源 / 下 ZIP / `--from-path`」三条兜底，原样转述即可
- 已有安装默认跳过（幂等），`--force` 才重建

### `build_report.py` —— 生成 HTML 日报

```bash
"$PY" <PROJECT_ROOT>/scripts/build_report.py --date <YYYY-MM-DD>
```

报告版式（三层结构）见 `references/report-format.md`。报告里有两处图表，都由脚本自己画（**原生 SVG，不引任何前端库**，单文件可离线打开）：

- **一句话总结里的体重总趋势图**：从 `weight_loss.start_date` 到报告日，灰点 = 每日实测，绿线 = 每 7 天均值，绿虚线 = 目标。斜率用最小二乘回归，**不用两点相减**。
- **核心指标总览下方的「近 14 天趋势」**：12 张小图分三组，浅绿区 = 建议区间。区间型指标画误差棒（线 = 区间中位），**中位只用于看图，不当点值结论**。缺数不插值，折线在该日断开。

⚠️ 图上判定与正文判定必须一致：钙按下界判、钠按上界判。改图不改正文（或反过来）就会出现「图说达标、文字说不足」的自相矛盾。

### `record_sleep.py` —— 手动补录睡眠（设备漏记的备援）

```bash
"$PY" <PROJECT_ROOT>/scripts/record_sleep.py --date <YYYY-MM-DD> --total-min <分钟> --start "23:30" --end "07:00"
```

设备正常同步时**不需要**用这个，仅作补漏。

### `db_status.py` —— 数据巡检与清理

```bash
"$PY" <PROJECT_ROOT>/scripts/db_status.py                  # 各表行数、来源分布、时间覆盖
"$PY" <PROJECT_ROOT>/scripts/db_status.py --purge-demo     # 清演示数据
"$PY" <PROJECT_ROOT>/scripts/db_status.py --purge-date <YYYY-MM-DD>
"$PY" <PROJECT_ROOT>/scripts/db_status.py --purge-library  # 清空份量库
```

### `init_db.py` —— 建库 / 升级 schema（幂等）

```bash
"$PY" <PROJECT_ROOT>/scripts/init_db.py            # 缺列自动补，不必重建库
"$PY" <PROJECT_ROOT>/scripts/init_db.py --status   # 看库状态
```

### `audit_entries.py` —— 漏记巡检（「发了但没录」）

```bash
"$PY" <PROJECT_ROOT>/scripts/audit_entries.py                    # 巡检昨天（退出码 2 = 有漏记候选）
"$PY" <PROJECT_ROOT>/scripts/audit_entries.py --date <YYYY-MM-DD>   # 指定某天
"$PY" <PROJECT_ROOT>/scripts/audit_entries.py --days 3            # 最近 3 天
"$PY" <PROJECT_ROOT>/scripts/audit_entries.py --json              # 结果存 logs/audit-<date>.json
"$PY" <PROJECT_ROOT>/scripts/audit_entries.py --dismiss <blob_id> # 这张不用记，以后别提示
"$PY" <PROJECT_ROOT>/scripts/audit_entries.py --dismiss-date <YYYY-MM-DD>
"$PY" <PROJECT_ROOT>/scripts/audit_entries.py --list-ignored      # 看已忽略清单
```

**它查什么**：不查文件系统（照片本来就不落盘），而是查 **WorkBuddy 自己的附件台账**
（会话记录 `~/.workbuddy/projects/*/*.jsonl` 里每条带图消息的 `image_blob_ref`），
再减去 `data/intake_log.jsonl`（已录）与 `data/audit_ignored.jsonl`（已忽略）→ 差额就是漏记候选。

**候选怎么处置**：
- 是要记的 → 直接 `Read` 候选给出的 `blob_path`（图还在本机），识读后补录，**记得带 `--blob-id`**；
- 不用记 → `--dismiss <blob_id>` 或 `--dismiss-date <日期>`。

**噪音规则**：文件名命中 `config.audit.ignore_name_patterns`（默认 `Clipboard_Screenshot*` / `Screen*` 等）
的图视为「例行非健康数据」，只报一行计数、不列入候选。不要因此把候选当成"完整清单"——必要时加
`--include-screenshots` 看全量。

**覆盖边界（要能如实说）**：如果消息压根没进到会话记录里（比如主机没开时手机端发的），
附件台账里不会有痕迹，**这一步查不出来**。这种情况如实说明"查不到"，别假装覆盖了。

## 三、数据表速查

| 表 | 存什么 | 谁写 |
|---|---|---|
| `activity_daily` | 日均步数、距离、活动热量、站立小时 | 手环 MCP |
| `heart_rate` | 心率样本（`ts`, `bpm`, `hr_type`） | 手环 MCP |
| `sleep_session` | 睡眠分期（深/浅/REM/清醒）、时长、评分 | 手环 MCP |
| `spo2` / `stress` | 血氧 / 压力样本 | 手环 MCP |
| `workout` | 运动记录 | 手环 MCP |
| `body_metric` | 体重 / BMI / 体脂 / 肌肉量（**每日一行**） | `record_body.py` 或体脂秤 |
| `waist` | 腰围（周频） | `record_body.py` |
| `meal` | 每餐（区间热量、蛋白、蔬菜拳数、碳水档位、置信度、**发酵份数**、**钠区间**） | `record_meal.py` |
| `portion_library` | 个人份量库（越用越准） | `record_meal.py` |
| `daily_review` | 每日主观反馈（精力、饥饿感、建议执行情况） | 手动 / 报告流程 |
| 视图 `v_body_trend` | 体重 + BMI + 体脂 + 腰围联合视图 | — |

**旁路台账**（不是数据源，是 JSONL）：

| 文件 | 存什么 | 谁写 |
|---|---|---|
| `data/intake_log.jsonl` | 每条录入来自哪张图（`blob_id` / `date` / `kind` / `summary`） | 录入脚本自动写 |
| `data/audit_ignored.jsonl` | 已忽略的漏记候选 | `audit_entries.py --dismiss*` |
| `data/advice_log.jsonl` | 每份报告给出的建议 + 目标日（次日），用于 T+1 执行率核验 | `build_report.py` 自动写 |

**source 字段枚举**：`photo` / `manual` / `scale_mcp` / `mi_fitness` / `demo`。

## 四、口径与纪律（易错点）

1. **单位**：按 `config.yaml` 的 `weight_unit` 解读用户报数（默认「斤」）。把原值传给脚本，**不要自己先换算**——脚本负责。临时用 kg 时加 `--unit kg`。
2. **营养只写区间**。绝不输出点值，绝不把"估算"说成"测量"。
3. **照片只用于取数，取完即删**。不复制、不转发、不留档。
4. **不录膳食纤维**——照片判不出来。用蔬菜拳数代替。
5. **数据不足就说不足**，不要用估算填满报告。
6. **异常心率 / 血氧不解读**，只标记 + 建议就医。
7. **睡眠口径**：报告日 D 展示「D 夜 → D+1 晨」那一觉（早上看就是"昨夜"），库内归属日 = 起床日。
8. **判趋势不判单日**：体重与缺口都看 7 日/多日均值，单日波动多半是噪声。
9. **进食窗口是可选功能（默认关闭），不是给所有人的建议**。给用户开窗口前必须先做安全筛查（见 `references/safety-boundary.md`）。

## 五、故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `no such column: xxx` | 库里缺列 | 跑 `init_db.py`（幂等补列） |
| 体重被拒写 | 值 ≥100 kg，疑似斤 | 确认单位；是斤就直接传斤数（脚本默认按斤） |
| 脚本报「找不到配置文件」 | 没建项目或工作目录不对 | `--root <PROJECT_ROOT>`，或先跑第零节的初始化 |
| 脚本中文输出乱码 | 控制台编码 | 脚本已内置 UTF-8 重设；若仍乱码，检查终端 `chcp 65001` |
| 报告某个板块空 | 该数据源当期无数据 | 正常。如实呈现"数据不足"，勿造数 |
| **手环板块全空** | 忘了跑 `sync_band.py`（MCP 的数据躺在源库，没进主库）或没配对 MCP | 先配对，再跑 `sync_band.py`，最后出报告。**这是最常见的原因** |
| `sync_band` 报「云端拉取失败」 | 网络 / 凭据过期 | 脚本会继续用源库数据；如实说明"本次未能联网拉取" |
| 手环数据为 0（跑过搬运仍为 0） | 云端还没有数据（未戴 / 未同步） | 不是接口问题。等源头产生数据 |
| 某天步数异常大 | 手机与手环同一天各自上报被累加 | `sync_band.py` 打 ⚠️ 时会提示；如实转述，别静默接受 |

## 六、参考文档

| 文档 | 内容 |
|---|---|
| `references/report-format.md` | 日报三层版式与判定口径（必读，改报告前看） |
| `references/meal-advice-template.md` | **配餐建议输出模板**：五段固定顺序（结论→结果表→缺口表→依据→偏差）+ 六类速查 + 不合格写法对照 + 自检清单。写饮食建议前必读 |
| `references/safety-boundary.md` | 做什么、不做什么（硬约束） |
| `references/band-setup.md` | 小米手环 / 体脂秤接入步骤 |
| `references/privacy.md` | 隐私纪律与照片处理规则 |
