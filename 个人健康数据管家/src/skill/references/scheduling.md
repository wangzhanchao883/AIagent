# 定时任务：两个宿主的写法

日报必须**送到用户面前**才算出了。定时任务就是那个「送到」的动作。

两个宿主的注册方式不同，但**提示词内容应该一致**——所以先写提示词，再按宿主选注册方式。

---

## 一、先在用户机器上取三个绝对路径

定时任务触发时通常是**新开一个会话，不继承今天的上下文**，所以路径必须写死进提示词。

```bash
# 项目根（固定）
echo "$HOME/health-data"

# 解释器绝对路径
[ -n "$HEALTH_PY" ] && echo "$HEALTH_PY"
[ -x "$HOME/health-data/.venv/bin/python" ] && echo "$HOME/health-data/.venv/bin/python"
[ -x "$HOME/health-data/.venv/Scripts/python.exe" ] && echo "$HOME/health-data/.venv/Scripts/python.exe"
command -v python3 || command -v python

# 脚本绝对路径（把 <SKILL_DIR> 换成实际目录）
echo "<SKILL_DIR>/scripts"
```

Windows 上形如 `C:\Users\xxx\health-data\...`，macOS / Linux 上是 `/Users/xxx/health-data/...`。

---

## 二、两个任务的提示词模板

> 把 `{{ROOT}}` `{{PY}}` `{{SCRIPTS}}` 换成上一步取到的真实值再交给宿主。**不要留占位符。**

### 任务一：22:00 当日速览

```
你现在在跑「健康数据管家 · 当日速览」定时任务。项目根是 {{ROOT}}，解释器是 {{PY}}，脚本目录是 {{SCRIPTS}}。

按顺序执行，每步都如实转述结果，不要跳过：
1. {{PY}} {{SCRIPTS}}/sync_band.py --start <今天> --end <今天>
   若报「云端拉取失败」，说明「本次未能联网拉取」，继续往下。
2. {{PY}} {{SCRIPTS}}/record_meal.py --day <今天>
3. {{PY}} {{SCRIPTS}}/build_report.py --date <今天> --no-sleep

然后读 {{ROOT}}/reports/report-<今天>.html，用 present_files 展示；没有这个工具就直接给绝对路径。
最后在对话里用 5 行以内讲：今天的运动与营养情况、热量缺口区间、明显缺口（比如蛋白偏低或蔬菜不够）、明天一条具体可执行的建议。

注意：当天还没结束，缺口是「截至现在」的口径，要说明；不查睡眠；营养一律说区间不说点值；不解读心率血氧。
```

### 任务二：10:10 昨日完整日报

```
你现在在跑「健康数据管家 · 昨日完整日报」定时任务。项目根是 {{ROOT}}，解释器是 {{PY}}，脚本目录是 {{SCRIPTS}}。

按顺序执行，每步都如实转述结果，不要跳过：
1. {{PY}} {{SCRIPTS}}/sync_band.py --start <昨天> --end <今天>
2. {{PY}} {{SCRIPTS}}/audit_entries.py --date <昨天> --json
   若退出码 2（有漏记候选），读出候选对应的图并补录；明显不是健康数据的用 --dismiss 标掉。
   若当前宿主没有 WorkBuddy 附件台账，该步骤会返回「无漏记」，这是已知假阴性，不要据此向用户保证没漏记。
3. {{PY}} {{SCRIPTS}}/build_report.py --date <昨天>
4. {{PY}} {{SCRIPTS}}/record_body.py --show        查看体重 7 日趋势
5. {{PY}} {{SCRIPTS}}/db_status.py                 确认数据覆盖

然后读 {{ROOT}}/reports/report-<昨天>.html，用 present_files 展示；没有这个工具就直接给绝对路径。
最后在对话里讲：昨日一句话总评、体重与 7 日均值对比、睡眠时长与质量、营养结构是否均衡、今天一条具体建议。

注意：含睡眠；营养说区间；趋势优于绝对值；不解读异常心率血氧，只标记并建议就医。
```

**为什么提示词这么长**：未来那次运行看不到今天这场对话，路径、脚本名、口径全得写死。写成「跑一下 daily 出个报告」这种，第二天必然报「找不到脚本」。

---

## 三、WorkBuddy：用自动化面板

工具是 `automation_update`，`mode="create"`，`scheduleType="recurring"`。

| 字段 | 值 |
|---|---|
| `name` | `健康数据管家 · 当日速览` / `健康数据管家 · 昨日完整日报` |
| `prompt` | 上面模板填好后的全文 |
| `scheduleType` | `recurring` |
| `rrule` | `FREQ=DAILY;BYHOUR=22;BYMINUTE=0` / `FREQ=DAILY;BYHOUR=10;BYMINUTE=10` |
| `status` | `ACTIVE` |
| `cwds` | `{{ROOT}}` |

建完把**任务名 + 时间**回报给用户，说一句「想改时间随时说」。要改就 `mode="update"`，要停就 `mode="update"` 且 `status="PAUSED"`。

---

## 四、豆包：用 `create_cron_job`

工具名与参数不同，语义对应如下：

| `create_cron_job` 参数 | 值 |
|---|---|
| `title` | 同 WorkBuddy 的 `name` |
| `query` | 上面模板填好后的全文（豆包把提示词叫 `query`） |
| `schedule` | `0 22 * * *` / `10 10 * * *` |
| `schedule_type` | `cron` |
| `enable` | `true` |

配套工具：`list_cron_jobs`（看已建）、`get_cron_job`（看单个含完整 `query`）、`update_cron_job`（改，只改时间就用 `patch` 里不带 `query`，服务端保留原值）、`delete_cron_job`。

**豆包的两个额外要求**（来自它自己的定时任务规范）：

1. 涉及「每天 / 明天 / 今晚」这类相对时间，**先调 `get_current_time`** 拿到实时时间再解析，禁止凭记忆推断日期。
2. 回复里要**同时给出 `query` 原文和需求解析**，不要只回一句「已设置」。

---

## 五、建完必须做的两件验证

### 1. 当场验证一次（不要等第二天）

```
① 先把任务时间临时改到「2 分钟后」
② 等它自己跑完，确认报告真的到了
③ 再改回 22:00 / 10:10
```

不这么做，你要到第二天早上才会发现它根本没跑起来。

### 2. 事后自查（程序侧，不依赖宿主承诺）

`build_report.py` 每次运行会给 `data/advice_log.jsonl` 追加记录。看最近几天的记录：

```bash
"$PY" {{SCRIPTS}}/db_status.py
```

如果最近 3 天里有一天没有记录，说明那天**定时任务没跑**，不是「那天没数据」。发现这种情况主动告诉用户：「10-24 那天没有日报记录，可能是定时任务没设成」，然后一起查。

> 用数据反推调度是否正常，比相信「我设好了」可靠得多。

---

## 六、常见失败原因（按出现频率）

| 现象 | 原因 | 处理 |
|---|---|---|
| 报「找不到脚本 / 找不到 health.db」 | 提示词里用了相对路径，或没写 `cwds` | 全部改绝对路径，`cwds` 填项目根 |
| 跑起来了但没读到数据 | 少了 `sync_band.py` 这步 | 补上，或用 `--no-pull` 只搬源库 |
| 报告生成了但用户没看到 | 没有推送动作 | 提示词末尾必须有「展示 HTML + 讲 5 行」
| 第二天发现完全没跑 | 宿主应用没开 / 电脑关机 / 额度用尽 | 这是本方案的边界：需要电脑开着且宿主应用在线。如实告知，不要归咎于脚本 |
| 时间对不上（差 8 小时） | 宿主的时区不是 `Asia/Shanghai` | 让用户确认宿主的时区设置 |

---

## 七、边界（要让用户知道）

这套定时任务的前提是：**用户的电脑开着，且宿主应用在线**。

- 电脑关机 / 睡眠 → 当天不会跑，第二天醒了也不会自动补。
- 补的办法：手动说一句「补一下 10-25 的日报」。
- 不做「错过自动补跑」——补跑会掩盖「定时任务根本没设对」这个真问题，用户永远修不好。
