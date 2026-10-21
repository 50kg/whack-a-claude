# whack-a-claude

[![Tests](https://github.com/50kg/whack-a-claude/actions/workflows/test.yml/badge.svg)](https://github.com/50kg/whack-a-claude/actions/workflows/test.yml)

Claude 本机数据维护工具，支持 Windows / macOS。
**一个核心脚本，两个系统启动入口。** 设备指纹功能根据朋友提供的 `claude_fp_clear_win.ps1` 整合，单独放在菜单 11。

## 启动

推荐从 [Releases](https://github.com/50kg/whack-a-claude/releases/latest) 下载对应系统的 ZIP，**完整解压后**：

- Windows 10/11 x64：双击 `Start.bat`，无需 Python 或 Git Bash。
- macOS 15+：按 M 系列 / Intel 芯片选择 ARM64 / x64 包，双击 `Start.command`。
- 独立包未做商业签名或 Apple 公证，首次启动可能需要系统确认；只运行可信来源并核对 `.sha256` 的版本，不要关闭系统安全功能。

源码版需要 Python 3.10+，无需第三方包。下载源码后同样双击上述启动文件。
macOS 源码 ZIP 如未保留执行权限，先运行 `chmod +x Start.command`。
源码版目标为 Windows 10/11、macOS 13+；持续集成覆盖 Windows、macOS 15 Intel / ARM64。Windows ARM64 可尝试源码版，尚无独立包；Linux 暂不支持。

## 菜单

| 编号 | 功能 |
| --- | --- |
| 1 / 2 | 完整备份：全部聊天、CLI 配置、桌面数据 |
| 3 | 浏览器站点清理：先扫描、备份，再确认删除 |
| 4 | 卸载标准桌面版，要求最新完整备份 |
| 5 | 清理本机数据，要求备份与当前数据一致 |
| 6 | 使用官方安装器安装 CLI |
| 7 | 恢复个人配置、MCP 与聊天，先保存当前数据 |
| 8 | 重建桌面会话列表（实验功能） |
| 9 | 检查环境 |
| 10 | 完整备份 → 清理数据 → 安装 CLI → 恢复 |
| **11** | **设备指纹：扫描 / 更新 / 还原 / 保留历史更新** |
| 12 | 只扫描浏览器 |
| 0 | 退出 |

修改前先保存工作，完全退出 Claude 桌面版和终端 CLI；浏览器操作也要退出相关浏览器。程序不强制结束进程，修改需要输入 `YES`。
组合维护不清浏览器、不卸桌面版；CLI 安装失败或取消后仍会尝试恢复个人数据。

## 设备指纹子菜单

菜单 **11** 内：`1` 只扫描，`2` 更新，`3` 还原备份，`4` 更新但保留历史快照，`0` 返回。

更新包括 `.claude.json` 的 `userID` / `machineID`、标准桌面目录的 `ant-did`、Sentry `scope_v3.json` 中对应设备号与安装号，
以及备份后清理带 UUID 的 CLI 缓存目录和 `.claude/backups/.claude.json.backup.*` 历史副本。
保留 MCP 设置、skills、登录凭据、聊天记录和其他缓存。UUID 与 Base64 UUID 按原格式写回。
原脚本的功能已收进 Python 核心，Windows 不再额外调用 PowerShell 指纹脚本；macOS 使用对应本机目录。

每次更新前创建单独的 `identity-*` 指纹备份，逐文件校验；修改失败会尝试自动回退。
子菜单 3 会**原样还原本次涉及的文件**，包括当时的 `.claude.json`，还原前也保存当前版本。
备份后修改过的同名文件会被覆盖；需要保留的修改请先另存。旧版朋友脚本生成的备份需手动恢复，不能直接交给新版菜单。
此功能只修改本地标识，不保证影响服务端账号状态。

## 备份与恢复

默认备份目录为 `~/Claude_Backup`。可通过 `BACKUP_ROOT` 环境变量更改；不能与维护目录重叠。
备份包含凭据、Token 和聊天等敏感数据，**不要公开上传或分享**。SHA-256 用于检测损坏，不验证不可信备份的来源。

普通备份覆盖 `~/.claude`、`.claude.json`、`.claude.json.backup` 和标准桌面数据目录。
恢复个人目录、settings、全局 Markdown、MCP 配置与聊天，移除备份里的顶层账号 OAuth 和设备标识。
普通恢复与指纹原样还原是两个独立操作；前者合并文件，保留当前目录中备份没有的文件。
自定义 `CLAUDE_CONFIG_DIR` 和非标准安装位置需手动管理。程序不管理系统钥匙串、注册表或整个 MSIX 包目录，且拒绝符号链接和目录联接。

```sh
python whack_a_claude.py backup all
python whack_a_claude.py restore "/path/to/backup-directory"
python whack_a_claude.py identity --scan
python whack_a_claude.py identity --keep-history
python whack_a_claude.py identity-restore latest
```

`before-restore-*` 是普通恢复前快照，`before-identity-restore-*` 是指纹还原前快照，均可用对应恢复命令指定完整路径。
浏览器备份在 `browser-*`；完全退出浏览器后，按 `browser-manifest.json` 拷回原件，并移走对应数据库的 `-wal` / `-shm` 文件。
这会恢复整个数据库快照，涉及其他网站，请先另存当前文件。
会话列表快照在 `sessions-*`；完全退出 Claude，按 `sessions-manifest.json` 的 `created` 清单删除本次新增条目即可撤销。

浏览器只处理 `claude.ai`、`claude.com`、`anthropic.com` 及子域名的 Cookies、保存的登录项和 IndexedDB。
SQLite 备份包含 WAL；Local Storage、Safari 需手动按站点管理。
会话列表重建依赖桌面内部格式，升级后可能不兼容；先登录、创建会话并退出，再运行菜单 8。

## 项目结构

日常只需 `whack_a_claude.py`、`Start.bat`、`Start.command`；说明集中在本文件，保留 [MIT 许可](LICENSE)。
测试和自动打包工具收在隐藏目录 `.github`，运行程序不依赖它们。

```sh
python -m unittest discover -s .github/tests -v
```

源自 [wangteakwoo-wq/whack-a-claude](https://github.com/wangteakwoo-wq/whack-a-claude)。请勿在 Issue 上传真实凭据、备份或聊天。
安全问题请使用仓库的私密漏洞报告。账号访问问题请联系官方支持。
