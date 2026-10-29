# 个人健康数据管家 · 双宿主通用版

把体重秤照片、餐食照片和一句口述，变成你自己电脑上的结构化健康记录，并每天自动出一份带趋势和建议的日报。

**同一份技能，两个宿主都能装**：WorkBuddy（专家包）和豆包（技能）。

---

## 一、它到底做什么

| 你做 | 它做 |
|---|---|
| 发一张体重秤照片，或说「今早 145.2」 | 读秤面 → 换算成 kg 存库 → 回报「145.2 斤 = 72.6 kg」 |
| 发一张午饭照片 + 一句「家里做的，油不多」 | 估**区间** + 置信度 → 入库 → 回报当日累计 |
| 说「出一下昨天的健康日报」 | 汇总活动 / 睡眠 / 体重 / 营养 → 生成 HTML 报告 → 讲给你听 |
| 什么都不说，到点自己跑 | 22:00 当日速览、10:10 昨日完整日报（需你同意后开启） |
| 有小米手环 / 体脂秤 | 可选：把小米云端的数据（活动 / 睡眠 / 心率 / 血氧 / 压力）同步进来 |

数据**全在你本机** `~/health-data/data/health.db`。照片只用来读数字，不留档。

---

## 二、怎么装

### 第一步：下载

打开 **Releases 页面**：<https://github.com/wangzhanchao883/AIagent/releases/latest>

按你的 AI 助手下载对应那一个：

| 你的 AI 助手 | 下载这个文件 | 直链 |
|---|---|---|
| 豆包（桌面版 / 工作版） | `health-data-steward-doubao.zip` | [下载](https://github.com/wangzhanchao883/AIagent/releases/latest/download/health-data-steward-doubao.zip) |
| WorkBuddy | `health-data-steward-workbuddy.zip` | [下载](https://github.com/wangzhanchao883/AIagent/releases/latest/download/health-data-steward-workbuddy.zip) |
| 其他支持 Skill 的助手（Claude Code、Codex、Cursor 等） | `health-data-steward-doubao.zip` | [下载](https://github.com/wangzhanchao883/AIagent/releases/latest/download/health-data-steward-doubao.zip) |

> 直链的文件名不带版本号，所以永远指向最新版——分享给别人时可以直接发这两个链接。

> 不用 clone 整个仓库——这里还放着别的专家，体积不小。单个包走 Release 就够。
> 两个包**核心完全一样**（逐字节相同的 `SKILL.md` 与脚本），差别只是 WorkBuddy 包多一层专家包外壳（`plugin.json` + 人设 + 头像）。

### 第二步：解压并导入

**豆包**：解压得到 `health-data-steward/` 目录 → 在豆包的「技能」里上传这个目录。

**WorkBuddy**：解压得到 `health-data-steward-1.5.0/` 目录 → 导入专家包。

**其他宿主**：把解压出的 `health-data-steward/` 放进它约定的 skills 目录。此路径**未逐一实测**，遇到问题请提 issue。

### 第三步：说一句话

对助手说「先帮我配置一下健康数据管家」，它会问四个数（身高 / 年龄 / 目标体重 / 体重用斤还是 kg），然后自己建目录、建库、自检。

详细的安装说明见 **[部署说明.md](部署说明.md)**。

---

## 三、首次使用会发生什么

1. **问四个数**：身高、年龄、目标体重、体重单位（斤 / 公斤）。缺这几个算不出基础代谢和热量缺口。
2. **建环境**：在 `~/health-data` 建目录、生成配置、建 SQLite 库、装唯一依赖 PyYAML。
3. **自检**：出一张 ✅/⚠️/❌ 清单，有 ❌ 不往下走。
4. **讲一遍怎么用**，然后问你要不要把日报挂成每天自动。

**它会拒绝把项目放在云同步目录**（OneDrive / 百度网盘 / iCloud）——数据放云盘就等于上云了，这是故意的。

---

## 四、边界（装之前请先看）

这套东西**不是云端服务**，它跑在**你自己的电脑上**，靠你的 AI 助手当入口。

| 情形 | 能不能用 |
|---|---|
| 电脑开着，桌面版 AI 助手在线 | ✅ 唯一支持的情况 |
| 用手机端遥控电脑上的 AI 助手（电脑仍开着） | ✅ |
| 电脑关机 / 睡眠 | ❌ 当天不会跑，也不会自动补 |
| 纯云端 AI（网页版、云沙箱） | ❌ 它看不到你本机的数据库 |
| 想不用 AI 助手、直接命令行 | ✅ 可以，脚本都是独立的 Python 脚本 |

**已适配并实测的宿主**：WorkBuddy、豆包。
**其他支持 Skill 的宿主**：理论上可以，未逐一实测。

---

## 五、已知限制（如实写在前面）

1. **漏记巡检只在 WorkBuddy 上可信**。它读的是 WorkBuddy 自己的附件台账；在豆包等宿主上没有这张台账，脚本会报「没有漏记」或「找不到会话记录目录」，**但退出码都是 0**。所以在豆包上不要相信「一张都没漏」这句承诺——判断缺数据要看数据形态（某天有体重没餐食、三餐只录一餐）。
2. **项目根固定 `~/health-data`**。想换位置要用环境变量 `HEALTH_ROOT`，直接给脚本传非默认的 `--root` 会被静默忽略（已知问题，1.5.0 未修）。
3. **小米接入是第三方逆向项目**（mi-fitness-mcp-cn，MIT，非小米官方），接口可能因小米改版失效。失效时它会如实告诉你，不装作没事。扫码登录必须你本人操作。
4. **不做错过自动补跑**。某天没跑就是没跑，要补得手动说一句「补一下 10-25 的日报」——自动补跑会掩盖「定时任务根本没设对」这个真问题。

---

## 六、安全边界

- **不做疾病诊断**，不给药物 / 补剂建议，不给极端饮食方案。
- **不解读异常心率、血氧、血压数值**，发现异常只做一件事：标记 + 建议就医。
- 所有建议限定在生活方式层面：作息、饮食结构、运动量、体重趋势、腰围。
- 营养一律给**区间 + 置信度**，从不给点值。油量是最大误差源。

---

## 七、仓库结构（改东西时看这节）

```
个人健康数据管家/
├── README.md                    ← 本文件：是什么、怎么装、边界
├── 部署说明.md                   ← 一步步装（给使用者看）
├── 发布说明-1.5.0.md             ← 这个版本改了什么（Release 正文用它）
├── build.py                     ← 构建脚本：src/ → dist/ + 两个 zip
└── src/                         ← ★唯一事实源，改东西只改这里
    ├── skill/                   ← 技能本体（标准 Agent Skills 结构）
    │   ├── SKILL.md             ← 行为 + 操作，324 行
    │   ├── references/          ← 9 篇按需加载的细节文档
    │   ├── scripts/             ← 12 个 Python 脚本（仅标准库 + PyYAML）
    │   └── templates/
    └── workbuddy/               ← WorkBuddy 外壳的源
        ├── plugin.json
        ├── persona.md           ← 瘦身人设（行为层已在 SKILL.md）
        └── expert.png
```

**产物不提交进仓库。** `dist/` 已被 `.gitignore` 挡住，由 `build.py` 生成；zip 只作为 Release 附件上传。

理由：同一份 `SKILL.md` 如果既在 `src/` 又在产物里，就成了两份副本——有人在网页上直接改产物、下次构建被静默覆盖，是最常见的漂移来源。版本号交给 git tag，成品交给 Release，仓库里只留一份源。

**改完源码后跑一次**：

```bash
python build.py --ver 1.4.1
```

`build.py` 会校验：两份 SKILL.md 必须逐字节一致、frontmatter 齐备、SKILL.md 不超过 500 行（Agent Skills 规范建议上限），并把两个固定文件名的 zip 打到 `dist/`：

```
dist/health-data-steward-doubao.zip
dist/health-data-steward-workbuddy.zip
```

**发版**：

```bash
gh release create health-data-steward-v1.4.1 \
  --title "健康数据管家 1.4.1" \
  --notes-file 发布说明-1.4.1.md \
  dist/health-data-steward-workbuddy.zip dist/health-data-steward-doubao.zip
```

zip 文件名**故意不带版本号**：GitHub 的 `releases/latest/download/<文件名>` 直链要求文件名不变，才能永久指向最新版，README 里的下载链接就永远不用改。

**发版后必须自查三件事**（漏一件就等于没发好）：

1. 打开 Release 页面，**下方必须有一个 Assets 区块**，里面是两个 zip，能看到文件大小
2. 如果两个 zip 出现在上面的说明文字里、Assets 区是空的 —— **说明拖错了位置**（拖进了描述框）。点 Edit，把它们拖到下方「Attach binaries by dropping them here」区域，再 Update
3. 点一下 README 里的直链，确认能下载且大小对得上（豆包约 125 KB，WorkBuddy 约 375 KB）

> 为什么这条要写死：GitHub 允许把文件拖进 Release 的**说明框**，那样会变成正文里的 `user-attachments` 链接——文件确实能下载，但**不会**进 Assets，`releases/latest/download/` 直链照样 404。两种位置在编辑界面上只差几十像素，很容易拖错。

---

## 八、数据安全提醒

`~/health-data/` 那一层**永远不要提交到任何 git 仓库**。里面有你的身高体重、三餐、睡眠、心率。

本目录下的 `.gitignore` 已经挡住了常见误提交（`data/` `*.db` `config/config.yaml` `reports/`）。但**不要把项目根建在这个仓库里面**——建在 `~/health-data`，物理分开。

---

## 九、版本

| 版本 | 说明 |
|---|---|
| 1.5.0 | 输出规范升级为三条硬规则（答案先行五段 / 一律熟重 / 六类缺口框架）；新增配餐建议模板；修复睡眠柱状图上界重复计、缺觉仍催训练、休息日被催补走、同日睡眠互相覆盖四处缺陷 |
| 1.4.0 | 双宿主通用版：行为层并入 SKILL.md，豆包可直接上传；修复首次记餐必失败、目标体重单位错算两处阻塞 |
| 1.3.0 | WorkBuddy 专家包（单一宿主） |

每个版本的安装包都在 [Releases](https://github.com/wangzhanchao883/AIagent/releases) 里留档，可以回退到任意历史版本。

---

## 十、第三方依赖与致谢

- **小米云端取数**：可选能力，依赖开源项目 [mi-fitness-mcp-cn](https://github.com/HUAYUE1024/mi-fitness-mcp-cn)。本项目**不内嵌**它的源码，而是在你执行 `install_band_mcp.py` 时从上游 `git clone` 到你的数据目录，遵循该项目自身的许可证。感谢作者 HUAYUE1024。
- **PyYAML**：脚本唯一的第三方 Python 依赖，`bootstrap.py` 会自动装入项目内 `.venv`，不污染系统环境。

本项目其余代码仅使用 Python 标准库。
