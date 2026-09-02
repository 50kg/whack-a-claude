# whack-a-claude

打地鼠：封一个，冒一个。

Claude 账号被封后的**本机环境一键重置 + 个人数据保全**工具。

以 AI 编程助手 skill 的形式提供（Kimi Code / Claude Code 均可加载），核心是一个可独立运行的 bash 脚本：备份 → 清浏览器 → 卸载桌面版 → 删残留 → 重装最新 CLI → 还原并生成无账号信息的配置，让本机回到"全新机器"状态，同时 skills、插件、commands、MCP 配置与授权、聊天记录一样不丢。

## 功能

| 阶段 | 做什么 |
|------|--------|
| `backup` | 个人配置与聊天记录备份到 `$BACKUP_ROOT/backup-<时间戳>/`（多次使用互不覆盖） |
| `browser` | 清理 Chrome/Edge 中 claude.ai / anthropic.com 的 Cookies、保存的登录、IndexedDB。**两遍式**：先只读扫描逐条列出命中项，确认后才删；原文件自动备份可回滚 |
| `uninstall` | 卸载 Claude 桌面版（MSIX 商店应用） |
| `delete` | 删除 CLI + 桌面版全部账号痕迹（9 个 Claude 专属路径，强制要求备份存在 + 输入 YES） |
| `install` | 安装最新 Claude Code CLI（官方 install.ps1，失败自动走 npm 兜底） |
| `restore` | 还原备份，生成剔除 `oauthAccount` / `userID` 的净化版 `.claude.json` |
| `verify` | 验证 CLI 可用、无账号残留，并打印重新注册的完整注意事项 |
| `all` | 以上全串行（关键节点仍需人工确认） |

## 删什么，保什么

**删（账号痕迹）**：`~/.claude`、`~/.claude.json(.backup)`、`~/.local/bin/claude.exe`、`~/.local/share/claude`、`%LOCALAPPDATA%\claude-cli-nodejs`、桌面版三个数据目录（含设备指纹 `ant-did`）、浏览器中 claude/anthropic 域名的站点数据。

**保（个人资产）**：skills、commands、plugins、hooks、scripts、settings（含鉴权/代理 env）、MCP 服务器 OAuth 授权、聊天记录（`projects/`，恢复后 `--resume` 可续聊）、文件编辑历史、计划任务、全局 CLAUDE.md、每项目信任与 MCP 配置。

**不碰**：项目代码、其他软件、浏览历史/书签/扩展/Local Storage、其他网站的登录态、注册表（社区流传的"清注册表"无来源支持）。

## 快速开始

环境要求：**Windows + Git Bash + python**。

作为独立脚本直接跑：

```bash
git clone https://github.com/wangteakwoo-wq/whack-a-claude.git
cd whack-a-claude
bash scripts/claude-reset.sh backup     # 先备份，永远从这一步开始
bash scripts/claude-reset.sh all        # 或一把梭（delete 前会要求确认）
bash scripts/claude-reset.sh verify     # 验证 + 打印重新注册注意事项
```

作为 skill 加载（推荐，AI 助手会按 SKILL.md 里的安全守则逐步执行并解释）：

```bash
# Kimi Code（用户级）
cp -r whack-a-claude ~/.agents/skills/
# 或 Claude Code（用户级）
cp -r whack-a-claude ~/.claude/skills/
```

然后在对话里 `/whack-a-claude` 或直接说"claude 被封了，帮我重置环境"。

## 配置

- 备份位置默认 `~/Claude_Backup`，用环境变量改：`BACKUP_ROOT=/e/backup bash scripts/claude-reset.sh backup`
- `YES=1` 可跳过交互确认（自动化场景，慎用）

## 清理之后（脚本管不到的部分）

本地清理只解决"扫地"。新账号不被连坐的关键在服务端信息全部换新，脚本 verify 阶段会打印完整清单，摘要：

- **支付**：全新卡（同卡段 BIN 都会连坐）；注册当天别付费，先免费养 3–5 天
- **网络**：干净住宅 IP 固定不变；网页/桌面版/CLI 三端同一出口；时区与 IP 一致；堵 WebRTC 泄露
- **账号**：全新邮箱（官方规定旧邮箱注销后 30 天内不能重建）+ 新手机号（非接码）；别关联旧 Google 账号
- **指纹**：新 Chrome Profile / 无痕换不掉设备指纹，用指纹浏览器新环境或换设备

详见 [SKILL.md](SKILL.md) 的"手动事项与注意事项"。

## 免责声明

本工具仅备份和删除**你自己机器上属于你自己的数据**，不包含任何破解、伪造凭据或绕过登录的功能，也不保证新注册账号不被再次封禁。重新注册和使用 Claude 需自行遵守 Anthropic 的服务条款，一切后果由使用者自负。

## License

[MIT](LICENSE)
