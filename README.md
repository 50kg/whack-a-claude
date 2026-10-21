# whack-a-claude

[![Cross-platform tests](https://github.com/50kg/whack-a-claude/actions/workflows/test.yml/badge.svg)](https://github.com/50kg/whack-a-claude/actions/workflows/test.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Windows / macOS 上的 Claude **本机数据备份、恢复和维护工具**。
保留聊天记录、skills、插件、MCP 配置与个人设置，清理前必须验证完整备份。

## 双击启动

### 独立版本：无需 Python 或 Git Bash

从 [Releases](https://github.com/50kg/whack-a-claude/releases/latest) 下载 ZIP，**先完整解压**：

| 系统 | 下载包 | 双击入口 |
| --- | --- | --- |
| Windows 10 / 11，x64 | `whack-a-claude-windows-x64.zip` | `Start.bat` 或 `whack-a-claude.exe` |
| macOS 15+，M 系列芯片 | `whack-a-claude-macos-arm64.zip` | `Start.command` |
| macOS 15+，Intel | `whack-a-claude-macos-x64.zip` | `Start.command` |

独立版本未购买 Windows 签名证书或 Apple 公证，系统可能要求首次运行确认。
仅对从本仓库下载并核对校验值的版本，按系统提示允许运行；macOS 可在“系统设置 → 隐私与安全性”中处理。
不要关闭系统安全功能。每个发行包旁的 `.sha256` 文件可用于检测下载损坏。

### 源码版

需要 **Python 3.10+**，不需要第三方 Python 包。

```sh
git clone https://github.com/50kg/whack-a-claude.git
cd whack-a-claude
```

- Windows：双击 `Start.bat` 或 `启动.bat`，自动寻找 `py`、`python`、`python3`，支持中文和空格路径。
- macOS：双击 `Start.command`。源码 ZIP 如未保留执行权限，先运行 `chmod +x Start.command`。
- 缺少 Python 时，从 [python.org](https://www.python.org/downloads/) 安装后再双击；macOS 自带 Python 可能版本不足。
- Windows ARM64 可尝试源码版与原生 ARM64 Python；目前没有 ARM64 独立包，也未承诺完整验证。
- 源码版目标为 Windows 10/11、macOS 13+；CI 覆盖 Windows、macOS 15 Intel/ARM64。Linux 暂不支持。

## 菜单与建议顺序

先保存工作，**完全退出 Claude 桌面版和终端 CLI**。工具不会强制结束进程。

| 编号 | 操作 | 保护措施 |
| --- | --- | --- |
| 1 / 2 | 完整备份 | 全部聊天、CLI 配置、桌面数据；逐文件 SHA-256 校验 |
| 3 | 浏览器清理 | 扫描、确认、保存数据库与 IndexedDB，然后删除匹配项 |
| 4 | 卸载桌面版 | 必须有最新完整备份；标准 macOS App / Windows 当前用户 MSIX |
| 5 | 清理本机数据 | 校验备份与当前数据一致，列出路径，输入 `YES` |
| 6 | 官方安装 CLI | 官方安装器判断 CPU 架构；没有第三方镜像回退 |
| 7 | 恢复 | 保存当前数据后合并恢复；同名文件以备份为准 |
| 8 | 重建桌面会话列表 | **实验功能**；保存原索引后，仅新增条目 |
| 9 | 检查环境 | CLI 版本、数据路径与聊天记录数量 |
| 10 | 组合维护 | 完整备份 → 清理本机数据 → 安装 CLI → 恢复 |
| 11 | 本地标识维护 | 先备份，只更新现有 `userID` / `machineID` / `ant-did` |
| 12 | 只扫描浏览器 | 不修改浏览器数据 |
| 0 | 退出 | |

建议先执行 2，确认备份位置，再选择需要的操作。组合维护不清浏览器、不卸桌面版；关键步骤仍需确认。
组合维护中的 CLI 安装失败或取消后，仍会尝试恢复个人数据。若恢复也失败，保留输出中的备份路径，用菜单 7 重试。
浏览器操作需要先退出 Chrome、Edge、Brave（包括后台进程）。

## 数据范围

完整备份保存 `~/.claude`、`~/.claude.json`、`.claude.json.backup`，以及检测到的标准桌面数据、缓存和日志目录。
Windows 覆盖 Roaming、Local、标准 Store 包内的 Claude 数据；macOS 覆盖用户 `~/Library` 中的标准路径。
实际路径以菜单 5 列出的清单为准。自定义 `CLAUDE_CONFIG_DIR` 或非标准安装位置需要手动备份，本版本不会自动管理。

自动恢复的个人内容：

- skills、commands、hooks、scripts、plugins、plans、tasks、projects、file-history。
- history.jsonl、settings.json、settings.local.json，以及全局 Markdown 文件。
- `.claude.json` 的 MCP、项目与个人设置，移除顶层 `oauthAccount`、`userID`、`machineID`。
- `.credentials.json` 的 MCP 等字段，移除顶层 `claudeAiOauth`、`oauthAccount`。
- 标准桌面目录中的 `claude_desktop_config.json`。

其他文件完整保存在备份中，不自动回填。JSON 格式损坏时，在写入前中止恢复。
恢复会保留当前目录中备份没有的文件；要移除当前登录信息，应先执行菜单 5，再恢复。
不会删除 CLI 安装文件、系统钥匙串、Windows 凭据管理器或整个 MSIX 包目录，不改变系统指纹、注册表、网络或账号权限。
CLI 重装交给官方安装器；非标准桌面安装请使用系统的应用管理功能。

浏览器只匹配 `claude.ai`、`claude.com`、`anthropic.com` 及其子域名，处理 Cookies、保存的登录项与对应 IndexedDB。
不会因域名中包含 `claude` 而清理其他网站。SQLite 备份包含 WAL；数据库读取或备份失败会中止并报告。
Local Storage、Safari 请使用浏览器自带的按站点数据管理。

## 命令与备份回退

```sh
python whack_a_claude.py backup all
python whack_a_claude.py restore "/path/to/Claude_Backup/backup-..."
python whack_a_claude.py verify
python whack_a_claude.py browser --scan
python whack_a_claude.py identity --scan
```

备份默认位于 `~/Claude_Backup`，名称含微秒与随机后缀。复制并校验成功后才写入 `manifest.json`。
备份后打开过 Claude 导致数据变化时，删除前必须重新备份。
备份目录不能与维护目录重叠；程序拒绝符号链接和 Windows 目录联接。

更改备份位置，PowerShell：

```powershell
$env:BACKUP_ROOT = 'D:\My Claude Backups'
.\Start.bat
```

macOS：

```sh
BACKUP_ROOT="$HOME/My Claude Backups" ./Start.command
```

- 恢复前快照：`before-restore-*`。菜单 7 或 `restore "完整路径"` 可恢复其中个人数据。
  原始设备标识和账号凭据不会自动回填；精确回退标识时，从 `payload/config` 或相应桌面数据中手动取回原件。
- 浏览器备份：`browser-*`。完全退出浏览器，另存当前文件，按 `browser-manifest.json` 的对应关系拷回原件，
  并移走对应数据库同名 `-wal` / `-shm` 文件（如存在）。回退会恢复该数据库的整个快照，涉及所有网站。
- 会话索引：`sessions-*`。完全退出 Claude，按 `sessions-manifest.json` 的 `created` 清单仅删除本次新增条目即可回退。

**备份含凭据、Token、聊天与浏览器数据，不要公开上传、提交或分享。** SHA-256 检测损坏，不代表不可信备份安全。

## 会话列表与旧版兼容

聊天正文在 `.claude/projects`，桌面列表另有账号/组织索引。恢复正文后可尝试 `claude --resume`。
菜单 8 使用本机或完整备份的有效索引作模板，保留已有条目；重复运行不会重复挂载同一会话。
先登录桌面版、创建一个会话、完全退出，再执行该功能。它依赖桌面内部格式，升级后可能不兼容，不保证所有版本都能显示或续聊。

旧命令 `bash scripts/whack-a-claude.sh` 保留。菜单 1 和 `backup listed` 现在统一完整备份，避免遗漏。
旧版备份不会删除，但不能作为 v2 清理凭证，需重新备份。
旧指纹脚本入口保留；`YES=1`、`-Yes` 不再跳过确认。自动强退、忽略运行进程和旧版 JSON 报告不再支持。
`-Restore latest` 现在按 v2 个人数据恢复规则执行。

## 开发与验证

```sh
python -m unittest discover -s tests -v
```

测试在隔离临时目录运行，覆盖损坏与过期备份、账号字段净化、路径边界、浏览器域名匹配、失败中止和会话重建。
CI 使用 [GitHub 官方 runner](https://github.com/actions/runner-images#available-images)，
发行包通过 [PyInstaller](https://pyinstaller.org/) 在对应平台分别构建；支持范围以实际通过的 Actions 与 Release 记录为准。

阅读 [贡献指南](CONTRIBUTING.md)、[安全说明](SECURITY.md)、[更新记录](CHANGELOG.md)。
源自 [wangteakwoo-wq/whack-a-claude](https://github.com/wangteakwoo-wq/whack-a-claude)，保留 MIT 许可。
本工具只维护用户自己的本机数据，不保证影响服务端账号状态；账号问题请走官方支持与申诉渠道。

## License

[MIT](LICENSE)
