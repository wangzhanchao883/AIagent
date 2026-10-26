# 脚本完整参数

`<SKILL_DIR>` = 本 skill 目录；`<PROJECT_ROOT>` = 项目根（默认 `~/health-data`）。
`$PY` 按 `SKILL.md` 第四节探测。**所有脚本都能从任意目录用绝对路径调用，不要 `cd` 进去。**

> 关于 `--root`：`record_*.py` 的 `--root` 在 1.4.0 里会被模块导入吞掉（已知限制）。**要指定非默认项目根，用环境变量 `HEALTH_ROOT`，不要传 `--root`。**

---

## bootstrap.py —— 首次配置

```bash
"$PY" <SKILL_DIR>/scripts/bootstrap.py --root ~/health-data \
  --height 171 --age 40 --unit jin --target-weight 130 --no-input
```

| 参数 | 说明 |
|---|---|
| `--root` | 项目根，默认 `~/health-data` |
| `--height` | 身高 cm（**必填**，算 BMR 用） |
| `--age` | 年龄（**必填**） |
| `--sex` | `male` / `female`，影响 BMR 系数 |
| `--unit` | `kg` 或 `jin`（体重单位，默认 `jin`） |
| `--target-weight` | 目标体重，单位同 `--unit` |
| `--fist-width` | 拳宽 cm，照片估份量用（可选） |
| `--name` | 用户称呼（可选） |
| `--no-input` | 全参数模式，不交互（Agent 默认用这个） |
| `--force` | 已有配置时强制重跑 |

做 7 件事：定根目录（**拒绝云同步目录**）→ 记解释器 → 装 PyYAML → 建目录 → 生成 `config/config.yaml` → 建库 → 写 `.setup_done.json`。

**验收**：输出里出现 `SETUP_OK`。

---

## selfcheck.py —— 环境自检

```bash
"$PY" <SKILL_DIR>/scripts/selfcheck.py --root ~/health-data
```

出 ✅ / ⚠️ / ❌ 清单。**有 ❌ 不许往下走。**

---

## record_body.py —— 体重 / 体脂 / 腰围

```bash
# 基本录入（单位按 config，默认斤）
"$PY" <PROJECT_ROOT>/scripts/record_body.py --weight 145.2

# 带附件指纹（宿主提供了才传）
"$PY" <PROJECT_ROOT>/scripts/record_body.py --weight 145.2 --blob-id <sha256>

# 顺带记体脂 / 肌肉 / 腰围
"$PY" <PROJECT_ROOT>/scripts/record_body.py --weight 145.2 --bodyfat 21.3 --waist 88

# 临时按公斤录入（覆盖 config）
"$PY" <PROJECT_ROOT>/scripts/record_body.py --weight 72.6 --unit kg

# 查看历史
"$PY" <PROJECT_ROOT>/scripts/record_body.py --show
```

| 参数 | 说明 |
|---|---|
| `--weight` | 体重，单位按 `--unit` 或 config（**必填**，除非 `--show`） |
| `--bodyfat` / `--muscle` | 体脂率 % / 肌肉量，只作趋势参考 |
| `--waist` | 腰围 cm（周频） |
| `--unit` | `kg` / `jin`，临时覆盖 config |
| `--date` | 补录指定日期 `YYYY-MM-DD` |
| `--source` | 默认 `photo`；手动录入可给 `manual` |
| `--note` | 备注（测量条件变化时写这里） |
| `--blob-id` | 源图指纹，可选 |
| `--photo` / `--photo-name` / `--keep-photo` | **不要用**。照片不归脚本管，不留档 |
| `--force` | 跳过单位/范围防线（**只在确认后使用**） |
| `--show` | 出历史表 |

**内置防线**：换算后 ≥100 kg 拒写（防「斤」当 kg）；超出 25–250 kg 一律拒写。

---

## record_meal.py —— 餐食

```bash
# 区间录入（推荐）
"$PY" <PROJECT_ROOT>/scripts/record_meal.py --meal-type lunch --desc "家做西红柿炒蛋+米饭一碗+一拳青菜" \
  --place 家 --kcal-low 620 --kcal-high 880 --protein-low 22 --protein-high 32 \
  --veg 1 --carb mid --confidence mid

# 只给点值 → 自动展开 ±15% 并标注
"$PY" <PROJECT_ROOT>/scripts/record_meal.py --meal-type breakfast --desc "水煮蛋两个" --kcal 140 --protein 12

# 存进个人份量库 / 从份量库复用
"$PY" <PROJECT_ROOT>/scripts/record_meal.py --meal-type lunch --desc "食堂青椒炒肉" \
  --kcal-low 500 --kcal-high 700 --save-to-library
"$PY" <PROJECT_ROOT>/scripts/record_meal.py --meal-type lunch --desc "青椒炒肉" --from-library "食堂青椒炒肉"

# 查看
"$PY" <PROJECT_ROOT>/scripts/record_meal.py --show
"$PY" <PROJECT_ROOT>/scripts/record_meal.py --day 2026-10-26
```

| 参数 | 说明 |
|---|---|
| `--meal-type` | `breakfast` / `lunch` / `dinner` / `snack`（**必填**） |
| `--desc` | 描述（**必填**），越具体越准 |
| `--kcal-low` / `--kcal-high` | 热量区间（推荐口径） |
| `--kcal` | 点值，脚本自动展开 ±15% |
| `--protein-low` / `--protein-high` / `--protein` | 蛋白克数 |
| `--calcium-low` / `--calcium-high` | 钙 mg（可选，照片通常判不出，宁缺勿造） |
| `--veg` | 蔬菜拳数（取代膳食纤维） |
| `--carb` | 碳水档位 `low` / `mid` / `high` |
| `--confidence` | `high` / `mid` / `low`（外食强制 `low`） |
| `--place` | `家` / `外食` / 具体店名 |
| `--from-library` | 从份量库复用 |
| `--save-to-library` | 存进份量库 |
| `--date` / `--time` | 补录时间 |
| `--note` / `--blob-id` | 备注 / 源图指纹（可选） |
| `--show` / `--day` | 查看 |

---

## record_sleep.py —— 手动补录睡眠

```bash
"$PY" <PROJECT_ROOT>/scripts/record_sleep.py --date 2026-10-26 --total-min 415 --start "23:30" --end "07:00"
```

| 参数 | 说明 |
|---|---|
| `--date` | 归属日 = **起床日** |
| `--total-min` | 总睡眠分钟 |
| `--start` / `--end` | 入睡 / 起床时刻 |
| `--deep` / `--light` / `--rem` / `--awake` | 分期分钟数 |
| `--wake-count` / `--score` | 夜醒次数 / 睡眠评分 |
| `--source` | 默认 `manual` |
| `--dry-run` / `--show` / `--force` | 预演 / 查看 / 强写 |

设备正常同步时**不需要**用这个，仅作补漏。

---

## sync_band.py —— 手环数据搬运（**出日报前必跑**）

链路：`云端 →（组件）→ 源库 mi_fitness.db →（本脚本）→ 主库 health.db`。缺最后一段，日报手环板块全空。

```bash
"$PY" <PROJECT_ROOT>/scripts/sync_band.py --start 2026-10-25 --end 2026-10-26
"$PY" <PROJECT_ROOT>/scripts/sync_band.py --days 7
"$PY" <PROJECT_ROOT>/scripts/sync_band.py --no-pull      # 不联网，只搬源库现有数据
"$PY" <PROJECT_ROOT>/scripts/sync_band.py --dry-run      # 只报会写什么
```

| 参数 | 说明 |
|---|---|
| `--days` | 回溯 N 天 |
| `--start` / `--end` | 指定区间 |
| `--no-pull` | 跳过云端拉取，只搬源库 |
| `--dry-run` | 不落库 |
| `--json` | JSON 输出 |
| `--src-db` | 手动指定源库路径（默认读 config） |

**特性**：幂等；**绝不写 `body_metric`**（体重只认用户自己的秤）；源库缺的字段留 NULL；单日 >60000 步或 >60 km 打 ⚠️（手机+手环重复累加）；原始快照落 `data/raw/mi_fitness/`；睡眠按**起床日**归属。

---

## install_band_mcp.py —— 安装小米云端取数组件

```bash
"$PY" <SKILL_DIR>/scripts/install_band_mcp.py --root <PROJECT_ROOT>
"$PY" <SKILL_DIR>/scripts/install_band_mcp.py --root <PROJECT_ROOT> --index-url https://pypi.tuna.tsinghua.edu.cn/simple
"$PY" <SKILL_DIR>/scripts/install_band_mcp.py --root <PROJECT_ROOT> --no-extras
"$PY" <SKILL_DIR>/scripts/install_band_mcp.py --root <PROJECT_ROOT> --force
"$PY" <SKILL_DIR>/scripts/install_band_mcp.py --root <PROJECT_ROOT> --dry-run
```

| 参数 | 说明 |
|---|---|
| `--root` | 项目根 |
| `--python` | 指定解释器（默认自动探测，**需 ≥3.11**） |
| `--src-url` | 换 git 源（默认上游，直连失败自动换镜像） |
| `--from-path` | 用本地已下载的源码目录，不联网 |
| `--index-url` | pip 镜像 |
| `--no-extras` | 最小安装（无 Web 扫码仪表盘） |
| `--force` | 重装 |
| `--dry-run` | 只报告计划 |

**边界**：不碰登录（扫码必须用户本人）；**不写 config.yaml**（只打印建议值）；需要网络。

---

## build_report.py —— 生成 HTML 日报

```bash
"$PY" <PROJECT_ROOT>/scripts/build_report.py --date 2026-10-26
"$PY" <PROJECT_ROOT>/scripts/build_report.py --date 2026-10-26 --no-sleep   # 当日速览用
"$PY" <PROJECT_ROOT>/scripts/build_report.py --date 2026-10-26 --open        # 生成后打开
```

| 参数 | 说明 |
|---|---|
| `--date` | 报告日，默认昨天 |
| `--no-sleep` | 不查睡眠（**22:00 当日速览必须加**，当天还没睡完） |
| `--sleep` | 强制含睡眠 |
| `--open` | 生成后用系统默认浏览器打开 |

版式见 `references/report-format.md`。

---

## audit_entries.py —— 漏记巡检

```bash
"$PY" <PROJECT_ROOT>/scripts/audit_entries.py                    # 巡检昨天
"$PY" <PROJECT_ROOT>/scripts/audit_entries.py --date 2026-10-26
"$PY" <PROJECT_ROOT>/scripts/audit_entries.py --days 3
"$PY" <PROJECT_ROOT>/scripts/audit_entries.py --json
"$PY" <PROJECT_ROOT>/scripts/audit_entries.py --dismiss <blob_id>
"$PY" <PROJECT_ROOT>/scripts/audit_entries.py --dismiss-date 2026-10-26
"$PY" <PROJECT_ROOT>/scripts/audit_entries.py --list-ignored
```

| 参数 | 说明 |
|---|---|
| `--date` / `--days` | 巡检范围 |
| `--json` | 结果写 `logs/audit-<date>.json` |
| `--all` | 含已忽略的 |
| `--include-screenshots` | 含截图噪音 |
| `--dismiss` / `--dismiss-date` | 标记忽略 |
| `--list-ignored` | 看已忽略清单 |

**退出码**：`0` = 无漏记；`2` = 有候选。

⚠️ **只在 WorkBuddy 上可信**。其他宿主没有附件台账，会返回「✅ 没有漏记」且退出码 0 —— 假阴性，见 `SKILL.md` 第七节。

---

## db_status.py —— 数据巡检与清理

```bash
"$PY" <PROJECT_ROOT>/scripts/db_status.py
"$PY" <PROJECT_ROOT>/scripts/db_status.py --purge-demo
"$PY" <PROJECT_ROOT>/scripts/db_status.py --purge-date 2026-10-26
"$PY" <PROJECT_ROOT>/scripts/db_status.py --purge-library
```

---

## init_db.py —— 建库 / 补列

```bash
"$PY" <PROJECT_ROOT>/scripts/init_db.py            # 幂等补列，不必重建
"$PY" <PROJECT_ROOT>/scripts/init_db.py --status
"$PY" <PROJECT_ROOT>/scripts/init_db.py --reset    # 慎用：删库重建
```

`--reset` 会丢数据，**未经用户明确同意不得使用**。

---

## 退出码约定

| 码 | 含义 | Agent 该做什么 |
|---|---|---|
| 0 | 成功 | 回执给用户 |
| 1 | 参数用法错 | 改参数重试 |
| 2 | 有候选待处置 / 校验拒绝 | 巡检：处置候选；录入：重新估算 |
| 3 | 环境问题 | 转 `bootstrap.py` / `selfcheck.py` |
| 4 | 数据源失败 | 如实说「本次没拉到」，不阻塞后续 |
