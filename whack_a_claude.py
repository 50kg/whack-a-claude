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
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

VERSION = "2.2.3"
PERSONAL = {"skills", "commands", "hooks", "scripts", "plugins", "plans", "tasks",
            "projects", "file-history", "history.jsonl", "settings.json", "settings.local.json"}
IDENTITY = {"oauthAccount", "userID", "machineID"}
DOMAINS = {"claude.ai", "anthropic.com", "claude.com"}


class MaintenanceError(Exception):
    pass


@contextlib.contextmanager
def console_selection():
    """Enable mouse selection/paste in this Windows console, then restore its mode."""
    enabled = False
    if os.name == "nt" and sys.stdin.isatty():
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetConsoleMode.restype = wintypes.BOOL
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.SetConsoleMode.restype = wintypes.BOOL
        handle = kernel.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = wintypes.DWORD()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            # ENABLE_EXTENDED_FLAGS is required for ENABLE_QUICK_EDIT_MODE.
            enabled = bool(kernel.SetConsoleMode(handle, mode.value | 0x0080 | 0x0040))
    try:
        yield enabled
    finally:
        if enabled:
            kernel.SetConsoleMode(handle, mode.value)


class BackupProgress:
    """Throttled single-line progress on terminals; readable milestones in logs."""
    def __init__(self):
        self.stream = sys.stdout
        self.terminal = self.stream.isatty()

    @contextlib.contextmanager
    def phase(self, label, total_files=None, total_bytes=None):
        self.label = label
        self.total_files, self.total_bytes = total_files, total_bytes
        self.files = self.bytes = self.width = 0
        self.started = time.monotonic()
        self.last = float("-inf")
        self.render(force=True)
        try:
            yield self
        except BaseException:
            self.render(force=True)
            raise
        else:
            self.render(force=True, done=True)
        finally:
            if self.terminal:
                print(file=self.stream, flush=True)

    def advance(self, *, files=0, size=0):
        self.files += files
        self.bytes += size
        self.render()

    def render(self, *, force=False, done=False):
        now = time.monotonic()
        if not force and now - self.last < (0.15 if self.terminal else 5):
            return
        self.last = now
        if self.total_files is None:
            bar = "[  扫描中  ]" if not done else "[ 扫描完成 ]"
            count = f"{self.files} 个文件"
        else:
            total = self.total_bytes or self.total_files
            current = self.bytes if self.total_bytes else self.files
            ratio = min(current / total, 1) if total else 0
            # Only a successfully finished phase may report 100%.
            percent = 100 if done else min(int(ratio * 100), 99)
            filled = percent // 10
            bar = "[" + "#" * filled + "-" * (10 - filled) + f"] {percent:3d}%"
            count = f"{self.files}/{self.total_files} 个文件"
        line = f"{bar} {self.label} | {count} {self.bytes / (1024 * 1024):.1f} MiB | {int(now - self.started)}s"
        if self.terminal:
            print("\r" + line + " " * max(0, self.width - len(line)), end="", file=self.stream, flush=True)
            self.width = len(line)
        else:
            print(line, file=self.stream, flush=True)


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


def inventory(path, *, follow_links=False, forbidden=None, progress=None):
    """Hash contents; only read-only backup scans may follow nested links."""
    path = fs_path(path)
    if is_link(path):
        raise MaintenanceError(f"拒绝操作符号链接或目录联接: {path}")
    if not path.exists():
        return None
    result = {}
    nodes = [(path, frozenset())]
    while nodes:
        node, ancestors = nodes.pop()
        if progress is not None:
            progress.advance()
        if is_link(node):
            if not follow_links:
                raise MaintenanceError(f"数据中存在链接，请先移到普通目录: {node}")
            # Do not treat arbitrary Windows reparse points as ordinary links.
            if not node.is_symlink() and not (os.name == "nt" and
                    getattr(node.lstat(), "st_reparse_tag", 0) == 0xA0000003):
                raise MaintenanceError(f"不支持的重解析点: {node}")
            try:
                node.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise MaintenanceError(f"链接失效或形成循环，无法完整备份: {node}") from exc
            if forbidden is not None and node.is_dir() and inside(fs_path(forbidden), node):
                raise MaintenanceError(f"链接指向备份目录或其上级，无法备份: {node}")
        rel = node.relative_to(path).as_posix()
        if node.is_dir():
            stat = node.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity in ancestors:
                raise MaintenanceError(f"目录链接形成循环，无法完整备份: {node}")
            result[rel] = None
            branch = ancestors | {identity}
            nodes.extend((child, branch) for child in node.iterdir())
        elif node.is_file():
            digest = hashlib.sha256()
            with node.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                    if progress is not None:
                        progress.advance(size=len(block))
            result[rel] = digest.hexdigest()
            if progress is not None:
                progress.advance(files=1)
        else:
            raise MaintenanceError(f"不支持的文件类型: {node}")
    return result


def copy_path(source, target, *, follow_source_links=False, progress=None, totals=(None, None)):
    source, target = fs_path(source), fs_path(target)
    # Recheck links here, including destination ancestors, before any writes.
    with progress.phase("复制前复查", *totals) if progress else contextlib.nullcontext():
        inventory(source, follow_links=follow_source_links, forbidden=target, progress=progress)
    for parent in [target, *target.parents]:
        if is_link(parent):
            raise MaintenanceError(f"目标路径包含链接: {parent}")
    def copy_file(src, dst):
        if progress is None:
            return shutil.copy2(src, dst)
        # Report bytes within large files too, rather than waiting for copy2 to return.
        with open(src, "rb") as reader, open(dst, "wb") as writer:
            for block in iter(lambda: reader.read(1024 * 1024), b""):
                writer.write(block)
                progress.advance(size=len(block))
        shutil.copystat(src, dst)
        progress.advance(files=1)
        return dst

    with progress.phase("复制文件", *totals) if progress else contextlib.nullcontext():
        if source.is_dir():
            if target.exists():
                inventory(target)
            shutil.copytree(source, target, dirs_exist_ok=True, copy_function=copy_file)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            copy_file(source, target)


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
    print("正在检查运行状态，准备完整备份…", flush=True)
    require_closed(env)
    root = new_archive(env, prefix)
    entries = {}
    progress = BackupProgress()
    for key, source in env.data.items():
        if not source.exists() and not is_link(source):
            continue
        print(f"\n正在备份: {source}", flush=True)
        with progress.phase("扫描源文件"):
            before = inventory(source, follow_links=True, forbidden=env.backup_root, progress=progress)
        if before is None:
            continue
        totals = (progress.files, progress.bytes)
        target = root / "payload" / key
        copy_path(source, target, follow_source_links=True, progress=progress, totals=totals)
        with progress.phase("校验备份", *totals):
            if inventory(target, progress=progress) != before:
                raise MaintenanceError(f"备份期间文件发生变化，备份无效: {source}")
        with progress.phase("复查源文件", *totals):
            if inventory(source, follow_links=True, forbidden=env.backup_root, progress=progress) != before:
                raise MaintenanceError(f"备份期间文件发生变化，备份无效: {source}")
        entries[key] = {"source": str(source), "files": before}
    if not entries:
        raise MaintenanceError("没有找到 Claude 数据，未生成有效备份。")
    print("正在保存备份清单…", flush=True)
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
        if fresh and (entry.get("source") != str(env.data[key]) or
                      inventory(env.data[key], follow_links=True, forbidden=env.backup_root) != entry["files"]):
            raise MaintenanceError("本机数据已变化，必须重新完整备份后再删除。")
    if fresh:
        for key, path in env.data.items():
            if path.exists() and key not in entries:
                raise MaintenanceError(f"备份未覆盖新出现的数据: {path}")
    return manifest


def object_json(path):
    data = read_json(path)
    if not isinstance(data, dict):
        raise MaintenanceError(f"JSON 顶层必须为对象，未执行操作: {path}")
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


def display_browser_plan(plan):
    for browser, path, rows in plan:
        print(f"  {browser}: {path} ({len(rows) if rows else 'IndexedDB'} 项)")
        for row in rows or []:
            print(f"    {row[0]}: {row[2]}")


def apply_browser_plan(env, plan):
    require_closed(env, browsers=True)
    if browser_plan(env) != plan:
        raise MaintenanceError("浏览器扫描结果已变化，请重新清理。")
    for _, path, rows in plan:
        if rows is None:
            remove_path(path, {p for _, p, r in plan if r is None})
        else:
            with contextlib.closing(sqlite3.connect(path, timeout=2)) as conn:
                with conn:
                    for table in {r[0] for r in rows}:
                        conn.executemany(f"DELETE FROM {table} WHERE rowid=?", [(r[1],) for r in rows if r[0] == table])


def clean(env, scan=False, keep_history=False):
    if env.system == "Windows" and "systemprofile" in str(env.home).lower():
        raise MaintenanceError("请使用自己的 Windows 用户账号运行，不能以 SYSTEM 身份清理。")
    require_closed(env)
    require_closed(env, browsers=True)
    changes = identity_plan(env, keep_history)
    browsers = browser_plan(env)
    print("\n----------------------------------------\n清理范围（不自动备份）\n----------------------------------------")
    for change in changes:
        label = '删除' if change['content'] is None else '更新本地标识'
        print(f"  {label}: {change['path']}")
    display_browser_plan(browsers)
    if not changes and not browsers:
        print("没有找到需要清理的项目。")
        return
    if scan:
        print("扫描完成，未修改文件，也未创建备份。")
        return
    confirm("将直接清理列出的项目，不自动备份；保留个人配置、MCP、skills 和聊天。浏览器相关站点会退出登录。")
    require_closed(env)
    require_closed(env, browsers=True)
    for change in changes:
        if inventory(change['path']) != change['before']:
            raise MaintenanceError("确认期间本机数据已变化，未开始清理，请重试。")
    if browser_plan(env) != browsers:
        raise MaintenanceError("确认期间浏览器数据已变化，未开始清理，请重试。")
    try:
        for change in changes:
            target, content = change['path'], change['content']
            if content is None:
                remove_path(target, {c['path'] for c in changes if c['content'] is None})
            else:
                atomic_bytes(target, content)
        for change in changes:
            expected = None if change['content'] is None else {'.': hashlib.sha256(change['content']).hexdigest()}
            if inventory(change['path']) != expected:
                raise MaintenanceError(f"清理后校验失败: {change['path']}")
        apply_browser_plan(env, browsers)
    except BaseException:
        print("清理未完成，部分项目可能已经修改；本次没有自动备份。")
        raise
    print("清理完成，未创建备份。Local Storage、Safari 请在浏览器设置中按站点管理。")


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


def verify(env):
    print(f"whack-a-claude {VERSION} | {env.system} {platform.machine()}")
    print(f"用户目录: {env.home}\n备份目录: {env.backup_root}")
    count = len(list((env.home / '.claude/projects').glob('*/*.jsonl')))
    print(f"聊天记录文件: {count}（数量不代表内容完整性）")
    checks = [env.home / '.claude.json', env.home / '.claude/settings.json']
    for desktop in env.desktops:
        if desktop.exists():
            indexes = len(list((desktop / 'claude-code-sessions').glob('*/*/local_*.json')))
            print(f"桌面数据: {desktop} | 会话索引文件: {indexes}")
            checks.append(desktop / 'claude_desktop_config.json')
    for item in checks:
        if item.is_file():
            object_json(item)
            print(f"配置格式正常: {item}")
    print("验证完成。")


MENU = f"""
========================================
  whack-a-claude  v{VERSION}
----------------------------------------

  1. 完整备份
  2. 清除缓存 + 清除指纹 + 清除浏览器
  3. 还原备份
  4. 换号重建会话列表
  5. 验证

  0. 退出
========================================
"""
ALIASES = {'1': 'backup', '2': 'clean', '3': 'restore', '4': 'sessions', '5': 'verify', '0': 'exit'}


def choose_restore_source(env, requested=None):
    if requested and requested != 'latest':
        return Path(requested).expanduser().absolute()
    roots = [r for r in env.backup_root.iterdir() if r.is_dir() and
             ((r / 'manifest.json').is_file() or (r / 'identity-manifest.json').is_file())] if env.backup_root.exists() else []
    roots.sort(key=lambda r: r.stat().st_mtime_ns, reverse=True)
    if not roots:
        raise MaintenanceError('没有可还原的备份。')
    if requested == 'latest':
        return roots[0]
    for index, item in enumerate(roots, 1):
        kind = '指纹备份' if (item / 'identity-manifest.json').is_file() else '完整备份'
        print(f'  {index}) {item.name} [{kind}]')
    choice = input('选择要还原的备份编号（回车取消）: ').strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(roots):
        raise MaintenanceError('已取消。')
    return roots[int(choice) - 1]


def dispatch(env, command, requested=None, account=None, scan=False, keep_history=False):
    command = ALIASES.get(command, command)
    if scan and command != 'clean':
        raise MaintenanceError('--scan 仅适用于清理。')
    if command == 'backup':
        backup(env)
    elif command == 'clean':
        clean(env, scan, keep_history)
    elif command == 'restore':
        source = choose_restore_source(env, requested)
        if (source / 'identity-manifest.json').is_file():
            restore_identity(env, source)
        else:
            restore(env, source)
    elif command == 'sessions':
        rebuild_sessions(env, choose_backup(env, requested), account)
    elif command == 'verify':
        verify(env)
    else:
        raise MaintenanceError(f'未知操作: {command}。可用命令: backup / clean / restore / sessions / verify')


@console_selection()
def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Claude 缓存清理与换号会话列表重建脚本")
    parser.add_argument("command", nargs="?", help="backup/clean/restore/sessions/verify 或菜单编号")
    parser.add_argument("backup", nargs="?", help="备份目录或 latest；backup all 为兼容写法")
    parser.add_argument("--account", help="重建列表时使用的本机账号/组织目录")
    parser.add_argument("--scan", action="store_true", help="仅扫描清理范围，不修改文件")
    parser.add_argument("--keep-history", action="store_true", help="清理时保留历史快照")
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
                    if os.name == "nt":
                        print("拖动鼠标选中，按 Enter 复制；右键粘贴。选中时暂停输出，Esc 取消选中。")
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
