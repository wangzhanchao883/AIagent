# 手环 / 体脂秤接入

> 手环数据能让日报的「活动 / 睡眠 / 心率 / 血氧」板块从空变成实。
> 但它是**可选增强**——没有手环也能用本专家（照片 + 口述 + 手动录入已能覆盖核心功能）。

## 一、链路原理（先看懂再配）

```
小米云端 ──(mi-fitness-mcp sync)──▶ 源库 mi_fitness.db ──(sync_band.py)──▶ 主库 health.db
                                                                   ▲
                                                              日报只读主库
```

**关键**：MCP 拉来的数据躺在**源库**里，**不会自动进主库**。
出报告前不跑 `sync_band.py`，手环板块就是空的——这是最常见的"数据缺失"原因。

## 二、前置条件

| 条件 | 说明 |
|---|---|
| 小米运动健康 App | 手机上登录账号，数据同步已开启 |
| `mi-fitness-mcp` | 本地 MCP 服务，负责登录 + 拉数据 + 写源库。**用安装器装**，别自己 clone 到别处（见下） |
| 本机 Python ≥ 3.11 | MCP 硬性要求；安装器会自动探测 |
| 网络 | 拉源码（GitHub 或镜像）+ pip 拉依赖（PyPI）都要联网；国内慢可换清华镜像 |

## 三、接入步骤

### 1. 装 MCP（跑安装器，不要手动 clone）

```bash
"$PY" <SKILL_DIR>/scripts/install_band_mcp.py --root <PROJECT_ROOT>
# 国内慢：--index-url https://pypi.tuna.tsinghua.edu.cn/simple
# 最小安装（无 Web 扫码仪表盘）：--no-extras；重装：--force；只看计划：--dry-run
```

- 源码**不随包分发**：安装器 `git clone --depth 1` 到 `<PROJECT_ROOT>/vendor/mi-fitness-mcp-cn/`，venv 建在其下
- 直连 GitHub 失败会自动换代理镜像（ghfast / ghproxy / gitclone）；全失败时按脚本提示换源、下 ZIP 或用 `--from-path`
- 上游仓库：https://github.com/HUAYUE1024/mi-fitness-mcp-cn （MIT，非小米官方）
- 安装完成后，安装器会打印 `mcp_exe` 绝对路径、**预期**源库路径和登录指引

### 2. 登录（二选一；⚠️ `doctor` 不负责登录，它只做诊断）

**方式 A：Web 仪表盘扫码登录（推荐，无需抓 Cookie）**

```bash
mi-fitness-mcp api    # 终端 1：后端 API 服务 (127.0.0.1:8321)
mi-fitness-mcp web    # 终端 2：Web 仪表盘 (127.0.0.1:8322)
```

浏览器打开 `http://127.0.0.1:8322/` → 点击「扫码登录」→ 用小米账号 / 米家 App 扫码确认。
**这一步必须用户本人操作**，AI 只能引导，不能代劳。

**方式 B：手动配置凭据（Cookie 方式）**

浏览器登录 [account.xiaomi.com](https://account.xiaomi.com)，从 Cookie 获取 `userId` 与 `passToken`：

```bash
mi-fitness-mcp setup --mode mi_fitness_cloud --user-id "<userId>" --pass-token "<passToken>" --region cn
```

登录成功后凭据存本机系统 keyring（Windows DPAPI / macOS Keychain），**不落明文**。

### 3. 诊断验证

```bash
mi-fitness-mcp doctor
```

看两件事：「连接状态 ✅」+「数据库： <路径>」。
**源库路径以这里的输出为准**（安装器打印的"预期路径"只是预览，不拿它落库）。

### 4. 填配置

编辑 `config/config.yaml`：

```yaml
sources:
  band:
    enabled: true
    verified_at: "<配对成功的日期>"
    source_db: "<doctor 输出的源库 mi_fitness.db 绝对路径>"
    mcp_exe: "<安装器打印的 mi-fitness-mcp.exe 绝对路径>"
```

### 5. 验证搬运链路

```bash
"$PY" <PROJECT_ROOT>/scripts/sync_band.py --days 3 --dry-run
```

先 dry-run 看会写什么，确认无误再实跑。

## 四、体脂秤接入

若用户的秤数据也走小米云：

```yaml
sources:
  scale:
    enabled: true
    route: "mi_fitness"
    window: "05:00-11:00"   # 只取晨起窗内当日首条
```

搬运口径（`sync_band.py` 的 `write_body_metric`）：

- 体脂秤会同步**全天每一次**称重 → **只取晨起窗 05:00–11:00 内的当日首条**
- **先查后写、绝不覆盖**：该日期主库已有记录（照片 / 口述）一律不动
- 入库 `source = scale_mcp`（区别于活动数据的 `mi_fitness`）

没接体脂秤也能用：体重直接拍照录入（`record_body.py`）即可。

## 五、已知限制（如实告知用户）

| 限制 | 说明 |
|---|---|
| **设备来源不可判** | MCP 在解析时丢弃了设备标识（`sid`），源库分不出「手机记的」还是「手环记的」→ 统一标 `source=mi_fitness`，**不要说成"手环记录"** |
| **活动量可能被重复累加** | 手机 + 手环同一天各自上报会被加总，出现异常大值时脚本会打 ⚠️，**要在回复里如实转述** |
| **睡眠分期不完整** | 上游返回的字段有限（通常有浅睡 / REM / 夜间清醒，**无深睡**）→ 不要因为缺深睡就说"数据不全" |
| **睡眠归属日 = 起床日** | 与小米运动健康 App 一致。报告口径是「报告日 D 展示 D 夜 → D+1 晨」 |
| **云端拉取失败不中断** | 脚本会用源库现有数据继续 → 要如实说"本次未能联网拉取"，不要假装同步成功 |
| **家用体脂秤不准** | 体脂率 / 肌肉量 / 内脏脂肪等**一律只作趋势参考**，绝不作为诊断或判断依据 |

## 六、不要过度承诺

- 不要说"帮你监测健康" → 是**记录与趋势**，不是监测
- 不要说"精准热量核算" → 营养是**估算**，误差可达数百 kcal
- 不要在体重没变化时说"平台期" → 先看是否测量条件一致（同一时间 / 同一台秤 / 空腹）
