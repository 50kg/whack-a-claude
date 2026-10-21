# whack-a-claude

[![Tests](https://github.com/50kg/whack-a-claude/actions/workflows/test.yml/badge.svg)](https://github.com/50kg/whack-a-claude/actions/workflows/test.yml)

Claude 本机数据工具，支持 Windows / macOS。一个核心脚本，两个系统启动入口。
清理、备份各自独立，**清理不自动备份**。

## 启动

从 [Releases](https://github.com/50kg/whack-a-claude/releases/latest) 下载对应系统的 ZIP，完整解压：

- Windows 10/11 x64：双击 `Start.bat`，无需 Python 或 Git Bash。
- macOS 15+：按 M 系列 / Intel 芯片选择 ARM64 / x64 包，双击 `Start.command`。
- 独立包未做商业签名或 Apple 公证，首次打开可能需要系统确认；核对 `.sha256` 后按系统提示处理，不要关闭系统安全功能。

源码版需要 Python 3.10+，无需第三方包，同样双击启动文件。
macOS 源码 ZIP 如未保留执行权限，先运行 `chmod +x Start.command`。
源码版目标为 Windows 10/11、macOS 13+；CI 覆盖 Windows、macOS 15 Intel / ARM64。Windows ARM64 尚无独立包，Linux 暂不支持。

## 主菜单

| 编号 | 名称 | 操作 |
| --- | --- | --- |
| 1 | 完整备份 | 保存全部聊天、个人配置和标准桌面数据，逐文件 SHA-256 校验 |
| 2 | 清理 | 统一处理本地标识、相关缓存、历史副本与浏览器站点数据，不自动备份 |
| 3 | 还原备份 | 选择已有备份，自动识别完整备份或旧版指纹备份 |
| 4 | 重建会话列表 | 根据已有聊天文件补建桌面列表索引，实验功能 |
| 5 | 验证 | 显示数据路径、聊天及索引数量，检查配置 JSON 格式；不检查或运行 CLI |
| 0 | 退出 | |

所有功能都在主菜单，没有二级菜单。安装 CLI、卸载程序、全量删除数据与“一把梭”已移除。
需要备份时请单独执行菜单 1；清理不要求已有备份，也不会创建浏览器备份、指纹备份或清理前快照。

## 清理范围

先保存工作，完全退出 Claude 桌面版、终端 CLI 和相关浏览器。程序列出范围，输入 `YES` 后直接清理：

- 更新 `.claude.json` 的 `userID` / `machineID`，保留其他设置和账号凭据。
- 更新标准桌面目录的 `ant-did`，同步处理 Sentry `scope_v3.json` 中对应的设备号和安装号。
- 删除带 UUID 的 CLI 缓存子目录和 `.claude/backups/.claude.json.backup.*` 历史副本。
- 删除 Chrome / Edge / Brave 中 `claude.ai`、`claude.com`、`anthropic.com` 及子域名的 Cookies、保存的登录项和 IndexedDB；这些网站会退出登录。

保留聊天、skills、MCP、个人设置和其他网站数据。不会删除整个配置目录，不改系统硬件编号、注册表或钥匙串。
此操作不自动备份、不提供自动回退；失败时会报告可能已完成的部分。Local Storage、Safari 需在浏览器设置中按站点管理。
清理逻辑整合了朋友提供的 `claude_fp_clear_win.ps1` 的本地标识处理功能，Windows / macOS 使用对应本机路径。

## 备份、还原与会话列表

默认备份目录为 `~/Claude_Backup`，可通过环境变量 `BACKUP_ROOT` 更改，不能与维护目录重叠。
完整备份覆盖 `~/.claude`、`.claude.json`、`.claude.json.backup` 和标准桌面数据，不包含浏览器站点数据库。
备份含凭据、Token 和聊天等敏感数据，**不要公开上传或分享**。SHA-256 检测损坏，不验证不可信备份的来源。

普通还原合并个人目录、settings、全局 Markdown、MCP 配置与聊天，移除备份中的顶层账号 OAuth 和设备标识。
同名文件以备份为准，备份没有的当前文件保留。旧版 `identity-*` 指纹备份按原文件还原。
还原操作仍会先保存当前版本；这与清理是否备份无关。还原前快照也可通过菜单 3 选择。
朋友原始脚本生成的旧格式备份需手动恢复。自定义 `CLAUDE_CONFIG_DIR` 和非标准安装位置需要手动管理。

重建会话列表依赖桌面内部格式；先登录桌面版、创建会话并完全退出，再运行菜单 4。
它只补建已有聊天的索引，不会恢复已删除的聊天正文，升级后可能不兼容。
索引快照在 `sessions-*`；完全退出 Claude，按 `sessions-manifest.json` 的 `created` 清单删除本次新增条目即可撤销。

```sh
python whack_a_claude.py backup all
python whack_a_claude.py clean
python whack_a_claude.py clean --scan
python whack_a_claude.py clean --keep-history
python whack_a_claude.py restore "/path/to/backup-directory"
python whack_a_claude.py verify
```

`--scan` 只列清理范围，不修改；`--keep-history` 在清理时保留历史快照。
旧的 `install`、`all`、`uninstall`、`identity` 等入口不再提供，菜单编号也已精简为 0–5。

## 项目结构

日常只需 `whack_a_claude.py`、`Start.bat`、`Start.command`，说明集中在本文件，保留 [MIT 许可](LICENSE)。
测试与打包工具集中在 `.github`，运行程序不依赖它们。

```sh
python -m unittest discover -s .github/tests -v
```

源自 [wangteakwoo-wq/whack-a-claude](https://github.com/wangteakwoo-wq/whack-a-claude)。
请勿在 Issue 上传真实凭据、备份或聊天；安全问题请使用私密漏洞报告，账号问题请联系官方支持。
