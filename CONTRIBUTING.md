# 参与贡献

欢迎提交 Issue 和 Pull Request。运行环境为 Windows 或 macOS、Python 3.10+。

```sh
python -m unittest discover -s tests -v
python whack_a_claude.py --version
```

测试必须使用临时目录和模拟进程检查，不能清理开发者的真实 Claude 数据。
修改备份、删除、恢复、浏览器逻辑时，请增加失败场景测试。
必须保留：删除前校验完整备份、准确域名匹配、拒绝路径越界、错误返回非零状态。
不要在 Issue、截图或测试样本中上传真实凭据、账号 UUID、聊天记录或浏览器数据库。

代码和文档统一 UTF-8；Shell 使用 LF，BAT 使用 CRLF。不要添加个人电脑的绝对路径。
欢迎提供 Windows ARM64、不同 macOS 版本与 Claude 桌面版本的兼容性反馈。
