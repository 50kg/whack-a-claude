#!/usr/bin/env python3
"""Local Claude maintenance. Python standard library only; no service credentials."""
from __future__ import annotations

import argparse
import base64
import contextlib
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

VERSION = "2.1.0"
PERSONAL = {"skills", "commands", "hooks", "scripts", "plugins", "plans", "tasks",
            "projects", "file-history", "history.jsonl", "settings.json", "settings.local.json"}
IDENTITY = {"oauthAccount", "userID", "machineID"}
DOMAINS = {"claude.ai", "anthropic.com", "claude.com"}


class MaintenanceError(Exception):
    pass


def fs_path(path):
    """Use extended-length Windows paths without requiring a registry change."""
    path = Path(path).absolute()
    if os.name != "nt" or str(path).startswith("\\\\?\\"):
        return path
    text = str(path)
    return Path("\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text)


def read_json(path):
    with fs_path(path).open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def atomic_bytes(path, content):
    path = fs_path(path)
    for parent in [path, *path.parents]:
        if is_link(parent):
            raise MaintenanceError(f"写入路径不能包含链接: {parent}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".whack-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, data):
    atomic_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def inside(path, parent):
    return Path(path).resolve().is_relative_to(Path(parent).resolve())


def is_link(path):
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        return True
    # Python 3.10/3.11 do not expose Path.is_junction().
    if os.name == "nt" and path.exists():
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    return False


def inventory(path):
    """Exact file hashes + empty directories; refuse links/reparse points before copying."""
    path = fs_path(path)
    if is_link(path):
        raise MaintenanceError(f"拒绝操作符号链接或目录联接: {path}")
    if not path.exists():
        return None
    result = {}
    nodes = [path]
    while nodes:
        node = nodes.pop()
        if is_link(node):
            raise MaintenanceError(f"数据中存在链接，请先移到普通目录: {node}")
        rel = node.relative_to(path).as_posix()
        if node.is_dir():
            result[rel] = None
            nodes.extend(node.iterdir())
        elif node.is_file():
            digest = hashlib.sha256()
            with node.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            result[rel] = digest.hexdigest()
        else:
            raise MaintenanceError(f"不支持的文件类型: {node}")
    return result


def copy_path(source, target):
    source, target = fs_path(source), fs_path(target)
    # Recheck links here, including destination ancestors, before any writes.
    inventory(source)
    for parent in [target, *target.parents]:
        if is_link(parent):
            raise MaintenanceError(f"目标路径包含链接: {parent}")
    if source.is_dir():
        if target.exists():
            inventory(target)
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def remove_path(path, allowed, allow_nested_links=False):
    """Only an exact, precomputed target can be deleted."""
    path = Path(path)
    if path not in allowed or path.resolve() != path.absolute() or is_link(path):
        raise MaintenanceError(f"删除路径不在允许范围内: {path}")
    if not allow_nested_links:
        inventory(path)
    if path.is_dir():
        shutil.rmtree(fs_path(path))
    elif path.exists():
        fs_path(path).unlink()


class Environment:
    def __init__(self, home=None, system=None, local=None, roaming=None, backup_root=None):
        self.home = Path(home or Path.home()).resolve()
        self.system = system or platform.system()
        if self.system not in {"Windows", "Darwin"}:
            raise MaintenanceError("仅支持 Windows 和 macOS。")
        self.local = Path(local or os.environ.get("LOCALAPPDATA", self.home / "AppData/Local"))
        self.roaming = Path(roaming or os.environ.get("APPDATA", self.home / "AppData/Roaming"))
        self.backup_root = Path(backup_root or os.environ.get("BACKUP_ROOT", self.home / "Claude_Backup")).expanduser().absolute()
        self.data = {"claude": self.home / ".claude", "config": self.home / ".claude.json",
                     "config-backup": self.home / ".claude.json.backup"}
        if self.system == "Darwin":
            support = self.home / "Library/Application Support"
            self.desktops = [support / "Claude", support / "Claude-3p"]
            extras = [self.home / "Library" / p for p in (
                "Caches/claude-cli-nodejs", "Caches/com.anthropic.claudefordesktop",
                "Caches/com.anthropic.claudefordesktop.ShipIt", "Logs/Claude",
                "Preferences/com.anthropic.claudefordesktop.plist",
                "HTTPStorages/com.anthropic.claudefordesktop",
                "Saved Application State/com.anthropic.claudefordesktop.savedState")]
        else:
            self.desktops = [self.roaming / "Claude", self.roaming / "Claude-3p",
                             self.local / "Claude", self.local / "Claude-3p",
                             self.local / "Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude"]
            extras = [self.local / "claude-cli-nodejs"]
        for i, path in enumerate(self.desktops + extras):
            self.data[f"data-{i}"] = path
        # Native executable may be a version symlink on macOS; leave installations
        # to the official installer/uninstaller instead of recursively deleting them.
        for path in self.data.values():
            if not inside(path, self.home) or path == self.home:
                raise MaintenanceError(f"数据目录不在当前用户目录内: {path}")
            if inside(self.backup_root, path) or inside(path, self.backup_root):
                raise MaintenanceError("备份目录不能与待维护的数据目录重叠。")
            for parent in [path, *path.parents]:
                if parent == self.home:
                    break
                if is_link(parent):
                    raise MaintenanceError(f"数据路径不能包含链接: {parent}")

    def browser_roots(self):
        if self.system == "Darwin":
            root = self.home / "Library/Application Support"
            return {"Chrome": root / "Google/Chrome", "Edge": root / "Microsoft Edge",
                    "Brave": root / "BraveSoftware/Brave-Browser"}
        return {"Chrome": self.local / "Google/Chrome/User Data",
                "Edge": self.local / "Microsoft/Edge/User Data",
                "Brave": self.local / "BraveSoftware/Brave-Browser/User Data"}


def running(env, browsers=False):
    node_cli = False
    if env.system == "Windows":
        result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, check=True)
        rows = csv.reader(io.StringIO(result.stdout.decode("utf-8", errors="replace")))
        names = {row[0].lower() for row in rows if row}
        targets = {"chrome.exe", "msedge.exe", "brave.exe"} if browsers else {"claude.exe", "claude-code.exe"}
        if not browsers and "node.exe" in names:
            # npm-based Claude Code appears as node.exe, not claude.exe.
            probe = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                "$ErrorActionPreference='Stop'; $p=@(Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { $_.CommandLine -match '[/\\\\]@anthropic-ai[/\\\\]claude-code[/\\\\]' }); $p.Count"],
                capture_output=True, check=True)
            node_cli = int(probe.stdout.strip()) > 0
    else:
        result = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, check=True)
        names = {Path(line.strip()).name.lower() for line in result.stdout.splitlines()}
        targets = {"google chrome", "microsoft edge", "brave browser"} if browsers else {"claude", "claude-code"}
        # Chromium helper processes can outlive their main windows.
        names |= {target for target in targets if any(name.startswith(target + " helper") for name in names)}
        if not browsers and "node" in names:
            args = subprocess.run(["ps", "-axo", "args="], capture_output=True, text=True, check=True)
            node_cli = any(re.search(r"[/\\]@anthropic-ai[/\\]claude-code[/\\]", line) for line in args.stdout.splitlines())
    return sorted(names & targets) + (["node (Claude Code)"] if node_cli else [])


def require_closed(env, browsers=False):
    names = running(env, browsers)
    if names:
        raise MaintenanceError("请先保存工作并完全退出这些程序，再重试: " + ", ".join(names))


def confirm(message):
    print(message)
    if input("输入 YES 确认，其他输入取消: ").strip() != "YES":
        raise MaintenanceError("已取消，未执行该操作。")


@contextlib.contextmanager
def exclusive(env):
    lock = env.home / ".whack-a-claude.lock"
    if is_link(lock):
        raise MaintenanceError("锁文件不能是链接。")
    with lock.open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                stream.write(b"0")
                stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise MaintenanceError("已有一个维护程序在运行，请关闭后重试。") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def new_archive(env, prefix):
    # Check ancestors too: a backup root redirected into the data tree loses safety.
    for path in [env.backup_root, *env.backup_root.parents]:
        if is_link(path):
            raise MaintenanceError(f"备份路径不能包含链接: {path}")
    env.backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    root = env.backup_root / f"{prefix}-{stamp}-{uuid.uuid4().hex[:6]}"
    root.mkdir(mode=0o700)
    return root


def backup(env, prefix="backup"):
    require_closed(env)
    root = new_archive(env, prefix)
    entries = {}
    for key, source in env.data.items():
        before = inventory(source)
        if before is None:
            continue
        target = root / "payload" / key
        copy_path(source, target)
        if inventory(target) != before or inventory(source) != before:
            raise MaintenanceError(f"备份期间文件发生变化，备份无效: {source}")
        entries[key] = {"source": str(source), "files": before}
    if not entries:
        raise MaintenanceError("没有找到 Claude 数据，未生成有效备份。")
    write_json(root / "manifest.json", {"format": 2, "kind": "full", "version": VERSION,
               "home": str(env.home), "system": env.system, "entries": entries})
    print(f"完整备份完成（SHA-256 校验）: {root}")
    return root


def choose_backup(env, requested=None):
    if requested and requested != "latest":
        return Path(requested).expanduser().absolute()
    roots = sorted(env.backup_root.glob("backup-*"), reverse=True)
    roots = [root for root in roots if (root / "manifest.json").is_file()]
    if not roots:
        raise MaintenanceError("没有 v2 完整备份，请先选择完整备份。旧版备份请保留并手动导入。")
    if requested == "latest":
        return roots[0]
    for index, root in enumerate(roots, 1):
        print(f"  {index}) {root.name}")
    selection = input("选择备份编号（回车取消）: ").strip()
    if not selection.isdigit() or not 1 <= int(selection) <= len(roots):
        raise MaintenanceError("已取消选择备份。")
    return roots[int(selection) - 1]


def validate_backup(env, root, fresh=False):
    root = Path(root)
    inventory(root)  # reject links before trusting manifest or payload
    manifest = read_json(root / "manifest.json")
    if manifest.get("format") != 2 or manifest.get("kind") != "full" or not manifest.get("entries"):
        raise MaintenanceError("备份格式不兼容或备份未完成。")
    if manifest.get("system") != env.system:
        raise MaintenanceError("该备份来自不同操作系统；请手动迁移项目路径与桌面配置。")
    entries = manifest["entries"]
    if not isinstance(entries, dict) or not set(entries) <= set(env.data):
        raise MaintenanceError("备份包含未知的数据目录。")
    for key, entry in entries.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("files"), dict):
            raise MaintenanceError("备份清单结构异常。")
        if inventory(root / "payload" / key) != entry.get("files"):
            raise MaintenanceError(f"备份文件缺失或校验失败: {key}")
        if fresh and (entry.get("source") != str(env.data[key]) or inventory(env.data[key]) != entry["files"]):
            raise MaintenanceError("本机数据已变化，必须重新完整备份后再删除。")
    if fresh:
        for key, path in env.data.items():
            if path.exists() and key not in entries:
                raise MaintenanceError(f"备份未覆盖新出现的数据: {path}")
    return manifest


def reset_data(env, root):
    require_closed(env)
    validate_backup(env, root, fresh=True)
    for path in env.data.values():
        print(f"  {path}")
    confirm(f"将清理上面列出的本机数据。已验证备份: {root}")
    # User may keep the prompt open while applications change files.
    require_closed(env)
    validate_backup(env, root, fresh=True)
    for path in env.data.values():
        remove_path(path, set(env.data.values()))
    print("本机数据已清理。CLI 安装文件、系统钥匙串由官方安装/退出登录流程管理。")


def object_json(path):
    data = read_json(path)
    if not isinstance(data, dict):
        raise MaintenanceError(f"JSON 顶层必须为对象，未执行恢复: {path}")
    return data


def restore(env, root, ask=True):
    require_closed(env)
    manifest = validate_backup(env, root)
    payload = Path(root) / "payload"
    # Parse every JSON that will be sanitized before making any live changes.
    config = object_json(payload / "config") if "config" in manifest["entries"] else None
    credentials = payload / "claude/.credentials.json"
    creds = object_json(credentials) if credentials.exists() else None
    if ask:
        confirm("将合并恢复个人数据（同名文件以备份为准），账号字段会移除。恢复前自动保存当前数据。")
    require_closed(env)
    if any(path.exists() for path in env.data.values()):
        print(f"恢复前快照: {backup(env, 'before-restore')}")
    source = payload / "claude"
    if source.is_dir():
        for item in source.iterdir():
            if item.name in PERSONAL or (item.is_file() and item.suffix == ".md"):
                copy_path(item, env.home / ".claude" / item.name)
    if config is not None:
        write_json(env.home / ".claude.json", {k: v for k, v in config.items() if k not in IDENTITY})
    if creds is not None:
        write_json(env.home / ".claude/.credentials.json",
                   {k: v for k, v in creds.items() if k not in {"claudeAiOauth", "oauthAccount"}})
    for i, desktop in enumerate(env.desktops):
        settings = payload / f"data-{i}" / "claude_desktop_config.json"
        if settings.exists():
            copy_path(settings, desktop / settings.name)
    print("个人数据恢复完成；OAuth 登录信息及旧设备标识未从备份恢复。")
    print("会话索引仍在备份中。桌面端登录、创建一个会话并完全退出后，可选择重建列表。")


def domain_match(value):
    host = value.lower().strip().lstrip(".")
    if "://" in host:
        host = urlparse(host).hostname or ""
    return any(host == domain or host.endswith("." + domain) for domain in DOMAINS)


def idb_match(name):
    match = re.fullmatch(r"https?_([^_]+)_\d+\.indexeddb\.(?:leveldb|blob)", name, re.I)
    return bool(match and domain_match(match[1]))


def database_rows(db):
    result = []
    with contextlib.closing(sqlite3.connect(Path(db).as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "cookies" in tables:
            result.extend(("cookies", row[0], row[1]) for row in conn.execute("SELECT rowid,host_key FROM cookies") if domain_match(row[1]))
        if "logins" in tables:
            result.extend(("logins", row[0], row[1]) for row in conn.execute("SELECT rowid,origin_url,signon_realm FROM logins") if domain_match(row[1]) or domain_match(row[2]))
    return result


def browser_plan(env):
    plan = []
    for browser, root in env.browser_roots().items():
        if not root.exists():
            continue
        for profile in root.iterdir():
            if not profile.is_dir() or not (profile.name == "Default" or re.fullmatch(r"Profile \d+", profile.name)):
                continue
            for rel in ("Network/Cookies", "Cookies", "Login Data"):
                db = profile / rel
                if db.is_file():
                    # Refuse symlinked browser profiles too.
                    if any(is_link(p) for p in [db, *db.parents]):
                        raise MaintenanceError(f"浏览器路径包含链接: {db}")
                    rows = database_rows(db)
                    if rows:
                        plan.append((browser, db, rows))
            idb = profile / "IndexedDB"
            if idb.is_dir():
                for item in idb.iterdir():
                    if idb_match(item.name):
                        if any(is_link(p) for p in [item, *item.parents]):
                            raise MaintenanceError(f"浏览器路径包含链接: {item}")
                        inventory(item)
                        plan.append((browser, item, None))
    return plan


def clean_browser(env, scan=False):
    require_closed(env, browsers=True)
    plan = browser_plan(env)
    for browser, path, rows in plan:
        print(f"  {browser}: {path} ({len(rows) if rows else 'IndexedDB'} 项)")
        for row in rows or []:
            print(f"    {row[0]}: {row[2]}")
    if scan or not plan:
        print("扫描完成，未改动浏览器数据。")
        return
    confirm("仅删除列出的 Claude/Anthropic 域名站点数据和保存的登录项；先创建浏览器备份。")
    require_closed(env, browsers=True)
    if browser_plan(env) != plan:
        raise MaintenanceError("扫描结果已变化，请重新扫描。")
    root = new_archive(env, "browser")
    entries = []
    # Back up ALL databases using SQLite's backup API (including WAL content),
    # and ALL IndexedDB folders before deleting the first row/file.
    for index, (_, path, rows) in enumerate(plan):
        dest = root / str(index)
        if rows is None:
            copy_path(path, dest)
        else:
            with contextlib.closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as src:
                with contextlib.closing(sqlite3.connect(dest)) as dst:
                    src.backup(dst)
        entries.append({"source": str(path), "payload": str(index), "files": inventory(dest)})
    write_json(root / "browser-manifest.json", {"entries": entries})
    print(f"浏览器备份: {root}")
    require_closed(env, browsers=True)
    if browser_plan(env) != plan:
        raise MaintenanceError("备份期间浏览器数据变化，请重试。")
    for _, path, rows in plan:
        if rows is None:
            remove_path(path, {p for _, p, r in plan if r is None})
        else:
            with contextlib.closing(sqlite3.connect(path, timeout=2)) as conn:
                with conn:
                    for table in {r[0] for r in rows}:
                        # Table names are fixed constants, row IDs remain bound parameters.
                        conn.executemany(f"DELETE FROM {table} WHERE rowid=?", [(r[1],) for r in rows if r[0] == table])
    print("浏览器清理完成。Local Storage、Safari 请在浏览器设置中手动按站点管理。")


def install_cli(env):
    confirm("将从 claude.ai 下载并运行官方安装程序，需要网络连接。")
    import urllib.request
    windows = env.system == "Windows"
    url = "https://claude.ai/install.ps1" if windows else "https://claude.ai/install.sh"
    with urllib.request.urlopen(url, timeout=60) as response:
        if urlparse(response.url).scheme != "https":
            raise MaintenanceError("安装程序下载发生了非 HTTPS 跳转。")
        content = response.read(5 * 1024 * 1024 + 1)
    if not content or len(content) > 5 * 1024 * 1024:
        raise MaintenanceError("安装程序大小异常。")
    with tempfile.TemporaryDirectory(prefix="whack-install-") as temp:
        script = Path(temp) / ("install.ps1" if windows else "install.sh")
        script.write_bytes(content)
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)] if windows else ["/bin/bash", str(script)]
        subprocess.run(command, check=True)
    verify_cli(env, required=True)


def verify_cli(env, required=False):
    native = env.home / ".local/bin" / ("claude.exe" if env.system == "Windows" else "claude")
    binary = str(native) if native.exists() else shutil.which("claude")
    if binary:
        subprocess.run([binary, "--version"], check=True, timeout=30)
        return True
    if required:
        raise MaintenanceError("安装程序结束，但未找到可运行的 Claude CLI；请检查官方安装输出。")
    print("未找到 Claude CLI。")
    return False


def uninstall(env, root):
    require_closed(env)
    confirm("将卸载当前用户的 Claude 桌面程序；请先完成完整备份。")
    require_closed(env)
    validate_backup(env, root, fresh=True)
    if env.system == "Windows":
        subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                        "$ErrorActionPreference='Stop'; Get-AppxPackage -Name 'Claude' | Remove-AppxPackage -ErrorAction Stop"], check=True)
    else:
        app = Path("/Applications/Claude.app")
        # Standard app bundles contain framework symlinks. rmtree unlinks them
        # without following their targets; the bundle root itself cannot be a link.
        remove_path(app, {app}, allow_nested_links=True)
    print("桌面版卸载步骤完成；非标准安装请使用系统的应用管理。")


def rebuild_sessions(env, root, account=None):
    require_closed(env)
    manifest = validate_backup(env, root)
    candidates = []
    templates = []
    for i, desktop in enumerate(env.desktops):
        index = desktop / "claude-code-sessions"
        settings = desktop / "config.json"
        if settings.exists():
            acc = object_json(settings).get("lastKnownAccountUuid")
            if isinstance(acc, str) and re.fullmatch(r"[a-zA-Z0-9-]+", acc) and (index / acc).is_dir():
                candidates.extend(p for p in (index / acc).iterdir() if p.is_dir())
        templates.extend(index.glob("*/*/local_*.json"))
        if f"data-{i}" in manifest["entries"]:
            templates.extend((Path(root) / "payload" / f"data-{i}" / "claude-code-sessions").glob("*/*/local_*.json"))
    candidates = sorted(set(candidates))
    if account:
        target = Path(account).absolute()
        if not any(inside(target, desktop / "claude-code-sessions") and len(target.relative_to(desktop / "claude-code-sessions").parts) == 2 for desktop in env.desktops):
            raise MaintenanceError("账号目录必须是本机桌面会话目录中的账号/组织子目录。")
        if not target.is_dir():
            raise MaintenanceError("账号目录不存在，请先登录并创建会话。")
    else:
        if not candidates:
            raise MaintenanceError("请先登录桌面版、创建会话并完全退出，再重建列表。")
        for i, item in enumerate(candidates, 1):
            print(f"  {i}) {item}")
        choice = input("选择账号/组织目录编号: ")
        if not choice.isdigit() or not 1 <= int(choice) <= len(candidates):
            raise MaintenanceError("已取消。")
        target = candidates[int(choice) - 1]
    inventory(target)
    template = None
    for path in sorted(templates, key=lambda p: p.stat().st_mtime, reverse=True):
        candidate = object_json(path)
        if "cliSessionId" in candidate:
            template = candidate
            break
    if template is None:
        raise MaintenanceError("找不到有效索引模板，请在桌面版创建一个会话。")
    existing = {object_json(p).get("cliSessionId") for p in target.glob("local_*.json")}
    planned = []
    # Known fields only: never clone account credentials or runtime permissions.
    for path in sorted((env.home / ".claude/projects").glob("*/*.jsonl")):
        if path.stem in existing or path.stem.startswith("agent-"):
            continue
        cwd = title = first = model = None
        stamps = []
        with path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        continue
                    cwd = cwd or row.get("cwd")
                    if row.get("type") == "custom-title":
                        title = row.get("customTitle") or title
                    message = row.get("message") or {}
                    if not isinstance(message, dict):
                        continue
                    model = message.get("model") or model
                    if row.get("type") == "user" and first is None:
                        content = message.get("content")
                        if isinstance(content, list):
                            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
                        if isinstance(content, str):
                            first = " ".join(content.split())[:80]
                    stamp = row.get("timestamp")
                    if isinstance(stamp, str):
                        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                        if parsed.tzinfo is not None:
                            stamps.append(int(parsed.timestamp() * 1000))
                except (ValueError, TypeError):
                    continue
        if not cwd or not stamps:
            print(f"跳过不完整的记录: {path.name}")
            continue
        session_id = "local_" + str(uuid.uuid4())
        item = {"sessionId": session_id, "cliSessionId": path.stem, "cwd": cwd, "originCwd": cwd,
                "createdAt": min(stamps), "lastActivityAt": max(stamps), "lastFocusedAt": max(stamps),
                "title": title or first or path.stem, "titleSource": "auto", "isArchived": False,
                "model": model or template.get("model"), "gitAnchors": []}
        planned.append((target / (session_id + ".json"), item))
    if not planned:
        print("没有需要新增的会话索引。")
        return
    confirm(f"将向 {target} 新增 {len(planned)} 条会话索引（桌面端内部格式，版本升级可能不兼容）。")
    require_closed(env)
    snap = new_archive(env, "sessions")
    copy_path(target, snap / "index-before")
    write_json(snap / "sessions-manifest.json", {"target": str(target), "created": [str(p) for p, _ in planned]})
    created = []
    try:
        for path, item in planned:
            write_json(path, item)
            created.append(path)
    except Exception:
        for path in created:
            path.unlink()
        raise
    print(f"新增 {len(created)} 条，原索引快照: {snap}")


def identity_cache(env):
    return env.local / "claude-cli-nodejs" if env.system == "Windows" else env.home / "Library/Caches/claude-cli-nodejs"


def identity_allowed(env, path):
    """Accept only the friend's original scope, expanded to known desktop locations."""
    path = Path(path)
    if not inside(path, env.home) or path.resolve() != path.absolute():
        return False
    fixed = {env.home / ".claude.json"}
    for desktop in env.desktops:
        fixed.update({desktop / "ant-did", desktop / "sentry/scope_v3.json"})
    if path in fixed:
        return True
    if path.parent == env.home / ".claude/backups":
        return path.name.startswith(".claude.json.backup.")
    return path.parent == identity_cache(env) and bool(re.search(r"[0-9a-f]{8}-[0-9a-f]{4}", path.name, re.I))


def identity_plan(env, keep_history=False):
    changes = []

    def add(path, content):
        if not identity_allowed(env, path):
            raise MaintenanceError(f"指纹路径不在允许范围内: {path}")
        changes.append({"path": path, "content": content, "before": inventory(path)})

    config = env.home / ".claude.json"
    if config.exists():
        data = object_json(config)
        for key in ("userID", "machineID"):
            data[key] = os.urandom(32).hex()
        add(config, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    for desktop in env.desktops:
        device = desktop / "ant-did"
        old = new = None
        if device.exists():
            old = device.read_text(encoding="utf-8-sig").strip()
            new = str(uuid.uuid4())
            try:
                uuid.UUID(old)
                content = new.encode("ascii")
            except ValueError:
                try:
                    old = base64.b64decode(old, validate=True).decode("ascii")
                    uuid.UUID(old)
                except (ValueError, UnicodeError) as exc:
                    raise MaintenanceError(f"设备标识格式未知，拒绝改写: {device}") from exc
                content = base64.b64encode(new.encode("ascii"))
            add(device, content)
        sentry = desktop / "sentry/scope_v3.json"
        if sentry.exists():
            object_json(sentry)  # refuse malformed JSON before writing anything
            text = sentry.read_text(encoding="utf-8-sig")
            original = text
            if old:
                text = text.replace(old, new)
            match = re.search(r"\b[0-9a-fA-F]{32}\b", text)
            if match:
                text = text.replace(match[0], os.urandom(16).hex())
            if text != original:
                add(sentry, text.encode("utf-8"))
    cache = identity_cache(env)
    if cache.is_dir():
        for item in sorted(cache.iterdir()):
            if item.is_dir() and identity_allowed(env, item):
                add(item, None)
    if not keep_history:
        for item in sorted((env.home / ".claude/backups").glob(".claude.json.backup.*")):
            if item.is_file():
                add(item, None)
    return changes


def identity_snapshot(env, changes, prefix="identity"):
    root = new_archive(env, prefix)
    entries = []
    for index, change in enumerate(changes):
        path = change["path"]
        if not identity_allowed(env, path):
            raise MaintenanceError(f"指纹路径越界: {path}")
        before = inventory(path)
        if "before" in change and before != change["before"]:
            raise MaintenanceError("扫描后数据已变化，请重新扫描。")
        if before is not None:
            copy_path(path, root / "payload" / str(index))
            if inventory(root / "payload" / str(index)) != before or inventory(path) != before:
                raise MaintenanceError("指纹备份期间数据变化，未开始修改。")
        entries.append({"relative": path.relative_to(env.home).as_posix(), "files": before})
    write_json(root / "identity-manifest.json", {"format": 1, "kind": "identity", "system": env.system,
               "home": str(env.home), "entries": entries})
    return root


def validate_identity(env, root):
    root = Path(root)
    inventory(root)
    data = object_json(root / "identity-manifest.json")
    if data.get("format") != 1 or data.get("kind") != "identity" or data.get("system") != env.system or data.get("home") != str(env.home):
        raise MaintenanceError("该指纹备份无效或来自其他用户/系统。")
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        raise MaintenanceError("指纹备份为空或不完整。")
    seen = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("relative"), str):
            raise MaintenanceError("指纹备份清单结构异常。")
        relative = Path(entry["relative"])
        path = env.home / relative
        if relative.is_absolute() or ".." in relative.parts or not identity_allowed(env, path) or path in seen:
            raise MaintenanceError("指纹备份包含重复或越界路径。")
        seen.add(path)
        if "files" not in entry or (entry["files"] is not None and not isinstance(entry["files"], dict)):
            raise MaintenanceError("指纹备份校验字段异常。")
        if inventory(root / "payload" / str(index)) != entry["files"]:
            raise MaintenanceError(f"指纹备份损坏: {relative}")
    return entries


def apply_identity_snapshot(env, root):
    entries = validate_identity(env, root)
    for index, entry in enumerate(entries):
        path = env.home / entry["relative"]
        if entry["files"] is None:
            remove_path(path, {path})
        else:
            source = Path(root) / "payload" / str(index)
            if source.is_file():
                atomic_bytes(path, fs_path(source).read_bytes())
            else:
                # Removed cache directories may have been recreated. Replace only
                # this exact, allowed cache child, never the entire cache root.
                remove_path(path, {path})
                copy_path(source, path)
        if inventory(path) != entry["files"]:
            raise MaintenanceError(f"还原后校验失败: {path}")


def rotate_identity(env, scan=False, keep_history=False):
    if env.system == "Windows" and "systemprofile" in str(env.home).lower():
        raise MaintenanceError("请使用自己的 Windows 用户账号运行，不能以 SYSTEM 身份维护。")
    if not scan:
        require_closed(env)
    changes = identity_plan(env, keep_history)
    for change in changes:
        print(f"  {'备份后清理' if change['content'] is None else '更新'}: {change['path']}")
    if scan or not changes:
        print("扫描完成，未修改文件。" if changes else "没有找到可维护的本地标识。")
        return
    confirm("将更新本地设备标识，并备份后清理列出的缓存/历史副本；MCP、skills、登录凭据和聊天不变。")
    require_closed(env)
    root = identity_snapshot(env, changes)
    print(f"指纹原件备份: {root}")
    validate_identity(env, root)
    for change in changes:
        if inventory(change["path"]) != change["before"]:
            raise MaintenanceError("备份后数据变化，未开始修改，请重新扫描。")
    try:
        for change in changes:
            path, content = change["path"], change["content"]
            if content is None:
                remove_path(path, {c["path"] for c in changes if c["content"] is None})
            else:
                atomic_bytes(path, content)
        for change in changes:
            expected = None if change["content"] is None else {".": hashlib.sha256(change["content"]).hexdigest()}
            if inventory(change["path"]) != expected:
                raise MaintenanceError(f"指纹更新后校验失败: {change['path']}")
    except BaseException:
        print(f"修改未完成，正在从 {root} 回退。")
        apply_identity_snapshot(env, root)
        raise
    print("指纹维护完成，可在指纹子菜单中原样还原；不影响服务端账号状态。")


def restore_identity(env, requested=None):
    require_closed(env)
    if requested and requested != "latest":
        root = Path(requested).expanduser().absolute()
    else:
        roots = sorted((r for r in env.backup_root.glob("identity-*") if (r / "identity-manifest.json").is_file()), reverse=True)
        if not roots:
            raise MaintenanceError("没有本工具生成的指纹备份。")
        if requested == "latest":
            root = roots[0]
        else:
            for index, item in enumerate(roots, 1):
                print(f"  {index}) {item.name}")
            choice = input("选择指纹备份编号（回车取消）: ").strip()
            if not choice.isdigit() or not 1 <= int(choice) <= len(roots):
                raise MaintenanceError("已取消。")
            root = roots[int(choice) - 1]
    entries = validate_identity(env, root)
    confirm(f"原样恢复该次指纹维护涉及的文件（包括当时的 .claude.json）；先保存当前版本: {root}")
    require_closed(env)
    snapshot = identity_snapshot(env, [{"path": env.home / e["relative"]} for e in entries], "before-identity-restore")
    print(f"还原前快照: {snapshot}")
    try:
        apply_identity_snapshot(env, root)
    except BaseException:
        apply_identity_snapshot(env, snapshot)
        raise
    print("指纹备份已原样还原。")


def identity_menu(env):
    while True:
        print("\n设备指纹维护（根据朋友提供的脚本整合）\n  1) 只扫描\n  2) 更新指纹（自动备份）\n  3) 还原指纹备份\n  4) 更新指纹但保留历史快照\n  0) 返回主菜单")
        choice = input("请选择: ").strip()
        if choice == "0":
            return
        try:
            if choice in {"1", "2", "4"}:
                rotate_identity(env, scan=choice == "1", keep_history=choice == "4")
            elif choice == "3":
                restore_identity(env)
            else:
                print("无效选择。")
        except (MaintenanceError, OSError, ValueError) as exc:
            print(f"指纹维护未完成: {exc}")


def verify(env):
    print(f"whack-a-claude {VERSION} | {env.system} {platform.machine()}")
    print(f"用户目录: {env.home}\n备份目录: {env.backup_root}")
    verify_cli(env)
    count = len(list((env.home / ".claude/projects").glob("*/*.jsonl")))
    print(f"聊天记录文件: {count}（数量不代表内容完整性）")
    for desktop in env.desktops:
        if desktop.exists():
            print(f"桌面数据: {desktop}")
    print("本工具仅维护本机数据；账号问题请使用官方支持与申诉渠道。")


MENU = """
  whack-a-claude — 本机数据维护
  1 / 2  完整备份（含所有聊天和桌面数据）
  3      扫描并清理浏览器站点数据
  4      卸载桌面版
  5      清理本机数据（必须有最新完整备份）
  6      官方安装 Claude Code CLI
  7      恢复个人配置与聊天
  8      重建桌面会话列表（实验功能）
  9      检查环境
  10     备份 → 清理本机数据 → 安装 CLI → 恢复
  11     设备指纹（扫描 / 更新 / 还原备份）
  12     仅扫描浏览器（不修改）
  0      退出
"""
ALIASES = {"1": "backup", "2": "backup", "3": "browser", "4": "uninstall", "5": "delete",
           "6": "install", "7": "restore", "8": "sessions", "9": "verify", "10": "all",
           "11": "fingerprint", "12": "browser-scan", "0": "exit"}


def dispatch(env, command, requested=None, account=None, scan=False, keep_history=False):
    command = ALIASES.get(command, command)
    if command == "backup":
        backup(env)
    elif command in {"browser", "browser-scan"}:
        clean_browser(env, scan or command == "browser-scan")
    elif command == "uninstall":
        # Require complete fresh data before app uninstallation (MSIX may remove it).
        root = choose_backup(env, requested)
        validate_backup(env, root, fresh=True)
        uninstall(env, root)
    elif command == "delete":
        reset_data(env, choose_backup(env, requested))
    elif command == "install":
        install_cli(env)
    elif command == "restore":
        restore(env, choose_backup(env, requested))
    elif command == "sessions":
        rebuild_sessions(env, choose_backup(env, requested), account)
    elif command == "identity":
        rotate_identity(env, scan, keep_history)
    elif command == "identity-restore":
        restore_identity(env, requested)
    elif command == "fingerprint":
        identity_menu(env)
    elif command == "verify":
        verify(env)
    elif command == "all":
        root = backup(env)
        payload = root / "payload"
        for path in (payload / "config", payload / "claude/.credentials.json"):
            if path.exists():
                object_json(path)
        reset_data(env, root)
        try:
            install_cli(env)
        finally:
            # Installation cancellation/failure must not leave personal data deleted.
            restore(env, root, ask=False)
        verify(env)
    else:
        raise MaintenanceError(f"未知操作: {command}")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Claude 本机备份、恢复与维护工具")
    parser.add_argument("command", nargs="?", help="backup/browser/delete/install/restore/sessions/verify/all/identity/identity-restore 或菜单编号")
    parser.add_argument("backup", nargs="?", help="备份目录或 latest；backup all 为兼容写法")
    parser.add_argument("--account", help="重建列表时使用的本机账号/组织目录")
    parser.add_argument("--scan", action="store_true", help="仅扫描 browser/identity")
    parser.add_argument("--keep-history", action="store_true", help="更新指纹时保留历史快照")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)
    try:
        env = Environment()
        with exclusive(env):
            if args.command:
                if ALIASES.get(args.command) != "exit":
                    dispatch(env, args.command, args.backup, args.account, args.scan, args.keep_history)
            else:
                while True:
                    print(MENU)
                    choice = input("请选择: ").strip()
                    if choice == "0":
                        return 0
                    try:
                        dispatch(env, choice)
                    except (MaintenanceError, OSError, ValueError, subprocess.SubprocessError, sqlite3.Error) as exc:
                        print(f"操作未完成: {exc}")
        return 0
    except (MaintenanceError, OSError, ValueError, subprocess.SubprocessError, sqlite3.Error) as exc:
        print(f"操作未完成: {exc}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\n已退出。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
