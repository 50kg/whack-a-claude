---
name: whack-a-claude
description: Claude 账号被封后，把本机 Claude 环境（Claude Code CLI + 桌面版 + 浏览器站点数据）重置到"全新机器"状态并恢复个人数据。流程：备份个人配置与聊天记录 → 清浏览器站点数据 → 卸载桌面版 → 删除全部账号痕迹 → 重装最新 CLI → 还原备份并生成无账号信息的 .claude.json。附带重新注册的完整注意事项（支付/网络/指纹/养号）。Use when the user mentions "claude 封号", "claude 清理", "重置 claude", "claude 重新注册", "claude 被封", "打地鼠", or invokes /whack-a-claude.
trigger: /whack-a-claude
---

# /whack-a-claude

Claude 封号后的本机环境重置 + 个人数据保全。**先备份，没备份绝不动手删。**

## 适用场景
账号被封、准备注册新号，需要清除本机全部账号痕迹，同时保住 skills / 插件 / commands / MCP 配置与授权 / 聊天记录。

## 环境要求
- Windows + Git Bash + python（脚本用 python 处理 SQLite/JSON）
- 备份默认写到 `~/Claude_Backup`（即 `%USERPROFILE%\Claude_Backup`），可用环境变量 `BACKUP_ROOT` 改到别的盘（如 `BACKUP_ROOT=/e/Claude_Backup bash scripts/whack-a-claude.sh backup`）
- 痕迹清单是 Claude 官方安装的标准默认路径；换台机器先核对一遍再跑（官方脚本装的在 `~/.local`，npm 装的路径不同）

## Golden rules
1. **备份先行。** 任何删除动作之前必须存在当次完整备份（时间戳目录，非空）。脚本 delete/all 阶段有强制检查，不要绕过。
2. **只扔身份，不扔家当。** 绝不恢复的字段：`oauthAccount`、`userID`、`telemetry/`、`sessions/` 等遥测使用痕迹。其余个人内容原样恢复。
3. **不伪造凭据。** `settings.json`（含 `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL` 中继配置）是用户自己的数据，原样恢复即回到"无需登录"状态；不要伪造账号身份，也不要去网上找破解模板。
4. **桌面版是 MSIX 商店应用**（包名 `Claude`，数据目录 `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc`），用 Appx 卸载，不是普通 exe 卸载。
5. **浏览器清理前必须关闭 Chrome/Edge**（SQLite 锁死会删不动）；脚本会先询问再强制结束进程。删除是**两遍式**：第一遍只读扫描、逐条列出命中的域名条目，用户确认后才执行第二遍删除；改 Cookies / Login Data 前会自动把原文件备份进当次备份目录的 `browser/` 子目录。匹配规则仅为域名含 `claude` / `anthropic`，不碰历史、书签、扩展、缓存、Local Storage 和其他网站。
6. **恢复必须在重装 CLI 之后**（CLI 首次运行会重建目录骨架并生成全新 userID，恢复的 sanitized .claude.json 里不能带旧 userID）。
7. **每步给用户看输出**， destructive 阶段执行前向用户复述一遍要做什么，得到确认再跑。

## 痕迹清单（标准默认路径，执行前先核对）
- CLI：`~/.claude\`、`~/.claude.json`、`~/.claude.json.backup`、`~/.local/bin/claude.exe`、`~/.local/share/claude/`、`%LOCALAPPDATA%\claude-cli-nodejs\`
- 桌面版：`%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\`、`%LOCALAPPDATA%\Claude-3p\`（含设备指纹 `ant-did`）、`%LOCALAPPDATA%\Claude\`
- 浏览器：`%LOCALAPPDATA%\Google\Chrome\User Data\<Profile>\` 下的 `Network\Cookies`、`Login Data`、`IndexedDB\*claude*`（Edge 同路径结构，脚本一并扫）
- 通常无需清：注册表、Windows 凭据管理器（社区流传的"清注册表"属玄学，无来源支持）

## 工作流（脚本逐阶段执行）
```bash
# 0. 先核对环境：tasklist 确认无 claude 进程在跑
bash scripts/whack-a-claude.sh backup      # 1. 备份到 $BACKUP_ROOT\backup-<时间戳>\，输出摘要
bash scripts/whack-a-claude.sh browser     # 2. 清 Chrome/Edge 的 claude 站点数据（会要求关闭浏览器）
bash scripts/whack-a-claude.sh uninstall   # 3. 卸载桌面版（MSIX）
bash scripts/whack-a-claude.sh delete      # 4. 删除 CLI + 桌面版全部残留（需输入 YES）
bash scripts/whack-a-claude.sh install     # 5. 装最新 CLI（pwsh 跑 install.ps1 → npmmirror 镜像 → npm 三级兜底）
bash scripts/whack-a-claude.sh restore     # 6. 还原备份 + 生成净化版 .claude.json
bash scripts/whack-a-claude.sh verify      # 7. 验证 + 打印重新注册注意事项
```
也可 `bash scripts/whack-a-claude.sh all` 一把梭（delete 前仍会要求输入 YES）。

## 备份内容（全部经核实可安全恢复）
| 内容 | 来源 | 说明 |
|------|------|------|
| skills / commands / hooks / scripts / plugins | `~/.claude\` 同名目录 | 个人资产 |
| settings.json / settings.local.json | `~/.claude\` | 含鉴权/代理 env 配置，原样恢复 |
| .credentials.json | `~/.claude\` | 仅 MCP 服务器 OAuth，无 Claude 账号 token |
| projects/ history.jsonl file-history/ plans/ tasks/ | `~/.claude\` | 聊天记录与项目状态；恢复后 `--resume` 可续聊 |
| *.md（CLAUDE.md 等） | `~/.claude\` | 全局记忆 |
| .claude.json / .claude.json.backup | `~/` | **仅存档**，含被封账号 oauthAccount，恢复时只取净化字段 |
| Cookies / Login Data 原文件 | 浏览器各 Profile | browser 阶段改前自动备份到 `browser/` 子目录 |

## 手动事项与注意事项（脚本管不到的部分）

### 重装桌面版（用户手动）
卸载和残留清理由脚本完成，重装请自行操作：Microsoft Store 搜 `Claude`（认准发布者 Anthropic），或官网 <https://claude.ai/download> 下载安装。**装完先别登录**，确认下面的环境项全部就绪后再登新号。

### 网络（社区认为权重仅次于支付）
- 用干净住宅 IP，**固定不变**；机房 IP、共享 VPN 出口（Nord/Surfshark 池）注册时容易秒封
- 网页、桌面版、CLI 三端全程走**同一条出口 IP**——多端多出口会被判账号共享/盗号
- 系统时区改成与 IP 一致（桌面版和 CLI 读系统时区，改完别再切回来）
- 堵 WebRTC 真实 IP 泄露（WebRTC Leak Prevent 类插件）；关掉 Chrome/Edge 的"安全 DNS"
- 注册后 24–72 小时是风控观察期，期间别换节点、别折腾

### 支付（社区公认连坐权重最高）
- 必须全新支付卡/支付账号：同一张卡、甚至同卡段 BIN 会把新旧号串起来连坐
- 账单地址与 IP 所在国家一致
- **注册当天别付费**：先免费低频用 3–5 天再充值，"环境太好 + 当天掏钱"反而触发风控
- 不要退款/撤卡，Stripe 追溯会连带封号

### 账号信息
- 全新邮箱（自己注册的 Gmail 最稳）；官方规定同一邮箱注销后 30 天内不能用于重建账号
- 新手机号，不要用接码平台
- 别用 "Continue with Google" 登录旧 Google 账号，会带出旧身份
- 可能触发 Persona KYC 人工审核（低质量邮箱、IP 跳变、高频使用会诱发），国内证件基本过不了，触发≈放弃

### 设备与指纹（比 Cookie 更关键）
- 新建 Chrome Profile、无痕窗口都换不掉 Canvas/WebGL/字体指纹
- 社区主流做法：指纹浏览器（AdsPower、比特浏览器）新建环境并单独挂新 IP；退而求其次换一个不同内核、平时没用的浏览器；最彻底是换设备
- 多次被封后设备指纹可能已被锁定，"清理了还是秒封"多半是这原因
- （可选）Chrome Local Storage 是全站混合 leveldb，脚本无法按站点清除。要彻底：`chrome://settings/content/all` 搜 `claude.ai` / `anthropic.com` 删一次

### 其他
- 若想让 Claude Code 也拥有本 skill：restore 后 `cp -r ~/.agents/skills/whack-a-claude ~/.claude/skills/`

## 已知坑
- install 三级兜底：pwsh 跑官方 install.ps1 → npmmirror 镜像手动装（国内最快，sha1 校验，布局与官方一致）→ npm。脚本优先调 pwsh（PowerShell 7），没有才用 powershell.exe（5.1）；精简/优化过的系统 5.1 组件可能损坏（如 Get-FileHash 缺失）导致 install.ps1 失败
- MCP 服务器授权存在 `.credentials.json`，恢复后免重新授权；若漏恢复需逐个重授
- 旧聊天记录按原项目路径恢复，`--resume` 可继续；换过项目路径则对应会话列表不出现
- 浏览器清理只删 claude/anthropic 域名的行，不影响其他网站登录态；但浏览器被强制关闭会丢未保存的页面状态，跑之前提醒用户保存
