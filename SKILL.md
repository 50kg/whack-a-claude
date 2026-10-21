---
name: whack-a-claude
description: Back up, restore and maintain local Claude data on Windows and macOS. Use when the user explicitly requests local Claude maintenance or invokes /whack-a-claude.
---

# whack-a-claude

使用 `whack_a_claude.py`，或双击 `Start.bat` / `Start.command`。先阅读 README。

- 先退出 Claude 再完整备份；删除前必须验证备份与当前数据一致。
- 尊重具体修改的交互确认，不修改程序或利用环境变量跳过。
- 不强制结束进程、不管理非标准目录、不跟随符号链接。
- 只恢复个人配置与聊天，不回填账号 OAuth、旧设备标识和遥测缓存。
- 会话列表重建是实验功能，依赖本机登录生成的目录与桌面内部格式。
- 不输出凭据、Token、浏览器数据库或聊天正文。
- 不承诺改变服务端账号状态；账号问题请使用官方支持渠道。

检查：`python whack_a_claude.py verify`。
备份：`python whack_a_claude.py backup all`。
浏览器只读扫描：`python whack_a_claude.py browser --scan`（先退出浏览器）。
