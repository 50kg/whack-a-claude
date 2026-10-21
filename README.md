# whack-a-claude

[![Tests](https://github.com/50kg/whack-a-claude/actions/workflows/test.yml/badge.svg)](https://github.com/50kg/whack-a-claude/actions/workflows/test.yml)

**清理 Claude 的本机缓存与标识，换号后把旧聊天重新挂回左侧会话列表。**

这个脚本围绕两件事：

- **清理旧账号的本地残留**：统一处理相关缓存、本地设备标识和浏览器里的 Claude 站点数据，保留聊天、skills、插件和 MCP 配置。
- **换号后找回左侧会话列表**：旧聊天文件还在，但新账号的左侧列表看不到时，根据本机记录为新账号补建列表索引。

如果你希望通过清理本地残留来避免再次封号，需要明确：**脚本只处理本机文件和站点数据，不能保证避免封号，也不能解除服务端封禁。**

支持 Windows 和 macOS，一个核心脚本，双击启动。

## 下载与启动

从 [Releases](https://github.com/50kg/whack-a-claude/releases/latest) 下载对应系统的 ZIP，**完整解压**后启动：

| 系统 | 下载包 | 启动文件 |
| --- | --- | --- |
| Windows 10/11 x64 | `whack-a-claude-windows-x64.zip` | 双击 `Start.bat` |
| macOS 15+，M 系列芯片 | `whack-a-claude-macos-arm64.zip` | 双击 `Start.command` |
| macOS 15+，Intel 芯片 | `whack-a-claude-macos-x64.zip` | 双击 `Start.command` |

独立版本无需安装 Python 或 Git Bash。尚未做商业签名或 Apple 公证，首次运行可能需要系统确认；核对下载包旁的 `.sha256`，按系统提示允许运行可信版本，不要关闭系统安全功能。

## 菜单

```text
1. 完整备份
2. 清除缓存 + 清除指纹 + 清除浏览器
3. 还原备份
4. 换号重建会话列表
5. 验证
0. 退出
```

## 只想清理 Claude

保存工作，完全退出 Claude 桌面版、终端中的 Claude Code，以及 Chrome / Edge / Brave 的相关进程，然后选择 **2**。

脚本先列出本机实际命中的项目，输入 `YES` 后执行：

| 清理项 | 实际操作 |
| --- | --- |
| 清除缓存 | 删除带 UUID 片段的 `claude-cli-nodejs` 缓存子目录，以及 `.claude/backups/.claude.json.backup.*` 配置历史副本 |
| 清除指纹 | 更新 `.claude.json` 中的 `userID` / `machineID`、桌面目录的 `ant-did`，处理 Sentry 副本里的相关标识；不修改电脑硬件编号 |
| 清除浏览器 | 删除 Chrome / Edge / Brave 中 `claude.ai`、`claude.com`、`anthropic.com` 及子域名的 Cookies、保存的登录条目和 IndexedDB |

“清除浏览器”只清相关站点数据，不卸载浏览器。它会影响这些站点的网页登录，**也会删除浏览器保存的相关账号、密码条目**，范围覆盖标准浏览器用户配置档案，不只当前窗口。

**清理不自动备份，也不要求已有备份。** 需要备份时，单独选择菜单 1。清理失败会报告可能已经完成的部分，不提供自动回退。

### 哪些数据会保留

- 聊天正文和现有会话列表索引。
- skills、commands、插件、MCP 配置与授权、个人设置和项目代码。
- `.credentials.json` 和 `.claude.json` 中的账号 OAuth 字段。
- 其他网站的数据，以及浏览器书签、浏览历史。

桌面版 / CLI 的登录凭据文件不会被删除，但更新本地标识后，客户端是否要求重新登录，不能保证。
Local Storage 和 Safari 不在自动清理范围内，需要在浏览器设置中按站点管理。

## 换号后，旧聊天还在但左侧列表空了

聊天正文保存在本机 `.claude/projects` 中，桌面端左侧列表另外使用账号 / 组织对应的索引。换号后，旧聊天文件可能还在，但新账号下面没有相应的列表条目。

**换号重建会话列表**会读取这些已有记录，为选定的新账号 / 组织目录补建索引，保留已有条目，重复运行不会重复添加同一会话。

建议按这个顺序操作：

1. **换号前选择 1「完整备份」**，保存个人数据和旧会话索引；菜单 4 会使用这份完整备份。
2. 如需清理本机残留，选择 2。清理本身不会顺带备份。
3. 打开 Claude 桌面版，登录新账号，创建一个会话，然后完全退出 Claude。
4. 重新打开本脚本，选择 **4「换号重建会话列表」**，选择完整备份和目标账号 / 组织目录。
5. 打开 Claude，确认左侧旧会话是否出现。菜单 5 可查看本机记录及索引数量，辅助检查。

这个操作补建的是**仍然存在于本机的聊天文件**的索引，不会找回已删除的聊天正文，也不迁移云端账号数据。桌面版本升级后，内部索引格式可能变化。

重建前会保存原索引快照到 `sessions-*`。需要撤销时，完全退出 Claude，按 `sessions-manifest.json` 的 `created` 清单删除本次新增条目。

## 完整备份与还原备份

**完整备份**包括聊天记录、skills、插件、MCP 配置与授权、个人设置、登录凭据和标准桌面数据，不包括浏览器站点数据库。
默认位置为 `~/Claude_Backup`，可通过环境变量 `BACKUP_ROOT` 更改。每份备份会逐文件校验，只有完整复制成功才标记完成。

**还原备份**用于找回文件或迁移后的恢复，不需要每次换号都执行。它恢复个人配置与聊天，剔除备份中的顶层旧账号 OAuth 和设备标识；同名文件以备份为准，备份没有的当前文件保留。
旧版指纹备份按原文件还原。还原前仍会保存当前版本，供回退使用。

备份里可能包含 Token、登录凭据和聊天，**不要公开上传或分享**。SHA-256 用于检测损坏，不验证不可信备份的来源。

## 验证

显示本机数据位置、聊天文件和桌面索引数量，检查配置 JSON 是否能正常读取。它不会启动或检查 CLI，不判断账号是否被封，也不保证聊天内容完整。

## 源码运行

源码版需要 Python 3.10+，不需要第三方 Python 包。双击 `Start.bat` / `Start.command` 即可。
macOS 源码 ZIP 如未保留执行权限，先运行 `chmod +x Start.command`。

```sh
python whack_a_claude.py backup all
python whack_a_claude.py clean --scan
python whack_a_claude.py clean
python whack_a_claude.py clean --keep-history
python whack_a_claude.py restore "/path/to/backup-directory"
python whack_a_claude.py sessions
python whack_a_claude.py verify
```

`--scan` 只列清理范围，不修改文件；`--keep-history` 清理时保留配置历史副本。
仅处理标准安装路径，自定义 `CLAUDE_CONFIG_DIR` 和非标准安装位置需要手动管理。源码版目标为 Windows 10/11、macOS 13+；CI 覆盖 Windows、macOS 15 Intel / ARM64。Windows ARM64 尚无独立包，Linux 暂不支持。

测试和打包工具集中在 `.github`，运行程序不依赖它们：

```sh
python -m unittest discover -s .github/tests -v
```

源自 [wangteakwoo-wq/whack-a-claude](https://github.com/wangteakwoo-wq/whack-a-claude)，保留 [MIT 许可](LICENSE)。安全问题请使用仓库的私密漏洞报告，不要在 Issue 上传真实凭据、备份或聊天。
