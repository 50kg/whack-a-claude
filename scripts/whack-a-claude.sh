#!/usr/bin/env bash
# whack-a-claude.sh — Claude 封号后的本机环境重置（备份/浏览器/卸载/删除/重装/还原/验证）
# 用法: bash whack-a-claude.sh <backup|browser|uninstall|delete|install|restore|verify|all> [backup_dir]
set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-$HOME/Claude_Backup}"
H="$HOME"
LAD="$(cygpath "$LOCALAPPDATA")"
# 优先 pwsh（PowerShell 7）；部分系统 5.1 组件损坏（如 Get-FileHash 缺失）会导致 install.ps1 失败
PS_EXE="$(command -v pwsh.exe 2>/dev/null || command -v pwsh 2>/dev/null || command -v powershell.exe 2>/dev/null || echo powershell.exe)"

# 个人内容：备份这些，之后原样恢复
BAK_DIRS=(skills commands hooks scripts plugins plans tasks projects file-history)
BAK_FILES=(history.jsonl settings.json settings.local.json .credentials.json)

# 删除清单（CLI + 桌面版全部痕迹，均为 Claude 专属路径）
DEL_ITEMS=(
  "$H/.claude"
  "$H/.claude.json"
  "$H/.claude.json.backup"
  "$H/.local/bin/claude.exe"
  "$H/.local/share/claude"
  "$LAD/claude-cli-nodejs"
  "$LAD/Claude"
  "$LAD/Claude-3p"
  "$LAD/Packages/Claude_pzs8sxrjxfjjc"
)

say() { printf '\n=== %s ===\n' "$*"; }

latest_backup() {
  ls -d "$BACKUP_ROOT"/backup-* 2>/dev/null | sort | tail -1
}

confirm() { # confirm <提示> —— YES=1 环境变量可跳过交互
  printf '%s 输入 YES 确认: ' "$1"
  [ "${YES:-}" = "1" ] && return 0
  local a; read -r a; [ "$a" = "YES" ]
}

phase_backup() {
  local BK="$BACKUP_ROOT/backup-$(date +%Y%m%d-%H%M%S)"
  say "1/7 备份 -> $BK"
  mkdir -p "$BK/.claude"
  local d f n=0
  for d in "${BAK_DIRS[@]}"; do
    [ -d "$H/.claude/$d" ] && { cp -r "$H/.claude/$d" "$BK/.claude/"; n=$((n+1)); }
  done
  for f in "${BAK_FILES[@]}"; do
    [ -f "$H/.claude/$f" ] && { cp "$H/.claude/$f" "$BK/.claude/"; n=$((n+1)); }
  done
  for f in "$H/.claude"/*.md; do
    [ -f "$f" ] && cp "$f" "$BK/.claude/" && n=$((n+1))
  done
  [ -f "$H/.claude.json" ]        && cp "$H/.claude.json" "$BK/"        # 仅存档，含账号数据
  [ -f "$H/.claude.json.backup" ] && cp "$H/.claude.json.backup" "$BK/" # 仅存档
  [ "$n" -ge 3 ] || { echo "!! 备份项异常少 ($n)，中止"; exit 1; }
  echo "OK: $n 项, $(du -sh "$BK" | cut -f1)"
  echo "BACKUP_DIR=$BK"
}

# 浏览器扫描/删除共用的域名匹配规则：仅命中域名含 claude 或 anthropic 的条目
phase_browser() {
  say "2/7 浏览器清理 (Chrome/Edge 的 claude.ai + anthropic.com 站点数据)"
  local BK="${1:-$(latest_backup)}" br UD prof db idb out found=0
  # 浏览器必须关闭，否则 SQLite 锁死（Edge 有后台进程，也要查）
  local browser_running=""
  if tasklist //FI "IMAGENAME eq chrome.exe" 2>/dev/null | grep -qi "chrome.exe"; then browser_running="$browser_running Chrome"; fi
  if tasklist //FI "IMAGENAME eq msedge.exe" 2>/dev/null | grep -qi "msedge.exe"; then browser_running="$browser_running Edge"; fi
  if [ -n "$browser_running" ]; then
    confirm "浏览器正在运行（$browser_running），需要强制关闭。" || { echo "已取消"; exit 1; }
    taskkill //F //IM chrome.exe >/dev/null 2>&1 || true
    taskkill //F //IM msedge.exe >/dev/null 2>&1 || true
    sleep 3
  fi
  # --- 第一遍：只读扫描，列出将删除的内容 ---
  echo "扫描匹配项（只读，尚未删除）:"
  for br in "Google/Chrome" "Microsoft/Edge"; do
    UD="$LAD/$br/User Data"
    [ -d "$UD" ] || continue
    for prof in "$UD"/Default "$UD"/Profile*; do
      [ -d "$prof" ] || continue
      for idb in "$prof/IndexedDB"/*claude* "$prof/IndexedDB"/*anthropic*; do
        [ -e "$idb" ] && { echo "  [IndexedDB] $br/$(basename "$prof")/$(basename "$idb")"; found=1; }
      done
      for db in "$prof/Network/Cookies" "$prof/Login Data"; do
        [ -f "$db" ] || continue
        out=$(python - "$db" <<'PY'
import sqlite3, sys
try:
    con = sqlite3.connect('file:' + sys.argv[1].replace('\\', '/') + '?mode=ro', uri=True)
except Exception as e:
    print(f'  [warn ] scan skipped {sys.argv[1]}: {e}')
    sys.exit(0)
try:
    for r in con.execute("SELECT DISTINCT host_key FROM cookies WHERE host_key LIKE '%claude%' OR host_key LIKE '%anthropic%'"):
        print('  [cookie]', r[0])
except Exception:
    pass
try:
    for r in con.execute("SELECT origin_url FROM logins WHERE origin_url LIKE '%claude%' OR origin_url LIKE '%anthropic%' OR signon_realm LIKE '%claude%' OR signon_realm LIKE '%anthropic%'"):
        print('  [login ]', r[0])
except Exception:
    pass
con.close()
PY
)
        [ -n "$out" ] && { echo "$out"; found=1; }
      done
    done
  done
  [ "$found" = "1" ] || { echo "无匹配项，跳过。"; return 0; }
  confirm "以上条目将被删除（Cookies/Login Data 原文件先备份到 $BK/browser/）。" || { echo "已取消"; exit 1; }
  # --- 第二遍：备份原文件后按域名删行 ---
  for br in "Google/Chrome" "Microsoft/Edge"; do
    UD="$LAD/$br/User Data"
    [ -d "$UD" ] || continue
    for prof in "$UD"/Default "$UD"/Profile*; do
      [ -d "$prof" ] || continue
      echo "-- $br / $(basename "$prof")"
      rm -rf "$prof/IndexedDB"/*claude* "$prof/IndexedDB"/*anthropic* 2>/dev/null || true
      local tag
      for db in "$prof/Network/Cookies" "$prof/Login Data"; do
        [ -f "$db" ] || continue
        tag="$(basename "$prof")-$(basename "$db")"
        [ -n "$BK" ] && { mkdir -p "$BK/browser"; cp "$db" "$BK/browser/$tag" 2>/dev/null || true; }
        python - "$db" <<'PY'
import sqlite3, sys
db = sys.argv[1]
try:
    con = sqlite3.connect(db, timeout=10)
except Exception as e:
    print(f'   [warn] delete skipped {db}: {e}')
    sys.exit(0)
n = 0
try:
    n += con.execute("DELETE FROM cookies WHERE host_key LIKE '%claude%' OR host_key LIKE '%anthropic%'").rowcount
except sqlite3.OperationalError:
    pass
try:
    n += con.execute("DELETE FROM logins WHERE origin_url LIKE '%claude%' OR origin_url LIKE '%anthropic%' OR signon_realm LIKE '%claude%' OR signon_realm LIKE '%anthropic%'").rowcount
except sqlite3.OperationalError:
    pass
try:
    con.commit()
except Exception as e:
    print(f'   [warn] commit failed {db}: {e}')
con.close()
print(f"   {db}: {n} rows deleted")
PY
      done
    done
  done
  cat <<'EOF'
NOTE: Local Storage 是全站混合 leveldb，脚本无法按站点外科清除。
      如要彻底：打开 Chrome → chrome://settings/content/all → 搜 claude.ai / anthropic.com 删除。
EOF
}

phase_uninstall() {
  say "3/7 卸载桌面版 (MSIX)"
  taskkill //F //IM Claude.exe 2>/dev/null || true
  "$PS_EXE" -NoProfile -Command \
    "if (Get-AppxPackage -Name 'Claude') { Get-AppxPackage -Name 'Claude' | Remove-AppxPackage; 'removed' } else { 'not installed' }"
}

phase_delete() {
  local BK="${1:-$(latest_backup)}"
  say "4/7 删除残留"
  [ -n "$BK" ] && [ -f "$BK/.claude/settings.json" ] || {
    echo "!! 找不到有效备份 ($BK)，拒绝删除。先跑 backup。"; exit 1; }
  echo "使用备份: $BK ($(du -sh "$BK" | cut -f1))"
  confirm "将删除 ${#DEL_ITEMS[@]} 个路径。" || { echo "已取消"; exit 1; }
  local p
  for p in "${DEL_ITEMS[@]}"; do
    [ -e "$p" ] && { rm -rf "$p" && echo "deleted: $p"; } || echo "absent : $p"
  done
}

# npmmirror 镜像手动安装（国内最快通道）：元数据→sha1校验→解压→官方布局
install_from_npmmirror() {
  local META VER SHA TGZ TMPD
  echo "npmmirror: 查询最新版..."
  META="$(curl -s --max-time 15 "https://registry.npmmirror.com/@anthropic-ai/claude-code-win32-x64/latest")" || return 1
  VER="$(printf '%s' "$META" | python -c "import json,sys; print(json.load(sys.stdin)['version'])" 2>/dev/null)"
  SHA="$(printf '%s' "$META" | python -c "import json,sys; print(json.load(sys.stdin)['dist']['shasum'])" 2>/dev/null)"
  [ -n "$VER" ] || { echo "!! 元数据解析失败"; return 1; }
  echo "npmmirror: $VER"
  TGZ="claude-code-win32-x64-$VER.tgz"
  TMPD="${TMPDIR:-/tmp}/whack-a-claude-dl"
  mkdir -p "$TMPD"
  curl -L --retry 3 --connect-timeout 10 -C - \
    -o "$TMPD/$TGZ" "https://registry.npmmirror.com/@anthropic-ai/claude-code-win32-x64/-/$TGZ" || return 1
  if [ -n "$SHA" ]; then
    (cd "$TMPD" && echo "$SHA  $TGZ" | sha1sum -c -) || { echo "!! sha1 校验失败"; return 1; }
  fi
  python - "$TMPD/$TGZ" "$H" "$VER" <<'PY'
import sys, tarfile, shutil, os
tgz, home, ver = sys.argv[1], sys.argv[2], sys.argv[3]
xdir = os.path.join(os.path.dirname(tgz), 'x')
with tarfile.open(tgz) as t:
    t.extractall(xdir)
src = os.path.join(xdir, 'package', 'claude.exe')
vdir = os.path.join(home, '.local', 'share', 'claude', 'versions', ver)
os.makedirs(vdir, exist_ok=True)
os.makedirs(os.path.join(home, '.local', 'bin'), exist_ok=True)
dst = os.path.join(vdir, 'claude.exe')
shutil.copy2(src, dst)
binp = os.path.join(home, '.local', 'bin', 'claude.exe')
try:
    if os.path.exists(binp):
        os.remove(binp)
    os.link(dst, binp)
except OSError:
    shutil.copy2(dst, binp)
print(f'npmmirror: claude {ver} installed -> ~/.local/bin/claude.exe')
PY
}

phase_install() {
  say "5/7 安装最新 Claude Code CLI"
  if [ -n "$PS_EXE" ] && "$PS_EXE" -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex" \
     && [ -f "$H/.local/bin/claude.exe" ]; then
    echo "install.ps1 OK ($PS_EXE)"
  elif install_from_npmmirror; then
    :
  else
    echo "!! npmmirror 也失败，最后走 npm"
    npm i -g @anthropic-ai/claude-code
  fi
  [ -f "$H/.local/bin/claude.exe" ] && echo "claude.exe 就位" || echo "!! 未找到 ~/.local/bin/claude.exe（可能走了 npm，路径不同）"
}

phase_restore() {
  local BK="${1:-$(latest_backup)}"
  say "6/7 还原 <- $BK"
  [ -f "$BK/.claude/settings.json" ] || { echo "!! 备份无效: $BK"; exit 1; }
  mkdir -p "$H/.claude"
  cp -r "$BK/.claude/." "$H/.claude/"
  echo "配置/数据已拷回"
  if [ -f "$BK/.claude.json" ]; then
    python - "$BK/.claude.json" "$H/.claude.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
d.pop('oauthAccount', None)   # 被封账号身份：丢弃
d.pop('userID', None)         # 旧设备 ID：丢弃，CLI 会生成全新的
json.dump(d, open(sys.argv[2], 'w', encoding='utf-8'), ensure_ascii=False)
print('sanitized .claude.json written (mcpServers/projects/onboarding 保留, oauthAccount+userID 已剔除)')
PY
  else
    echo "!! 备份里无 .claude.json，跳过（CLI 首次运行会自建）"
  fi
}

phase_verify() {
  say "7/7 验证"
  local CL="$H/.local/bin/claude.exe"
  [ -f "$CL" ] || CL="$(command -v claude || true)"
  [ -n "$CL" ] && "$CL" --version || echo "!! claude 不可用"
  if [ -f "$H/.claude.json" ]; then
    local c
    c=$(grep -c '"oauthAccount"' "$H/.claude.json" || true)
    [ "$c" = "0" ] && echo "OK: .claude.json 无账号信息" || echo "!! .claude.json 仍含 oauthAccount ($c)"
  fi
  cat <<'EOF'
================ 手动事项与注意事项 ================
[桌面版重装 —— 用户手动]
  Microsoft Store 搜 "Claude" (认准发布者 Anthropic) 或官网 https://claude.ai/download
  装完先别登录, 下面环境全部就绪后再登新号
[网络]
  - 干净住宅 IP 并固定不变; 避开机房 IP / 共享 VPN 出口
  - 网页 + 桌面版 + CLI 三端全程走同一条出口 IP
  - 系统时区改成与 IP 一致; 堵 WebRTC 泄露; 关浏览器"安全 DNS"
  - 注册后 24-72 小时是风控观察期, 别换节点别折腾
[支付]
  - 必须全新支付卡/账号: 同一张卡甚至同卡段 BIN 会串号连坐
  - 账单地址与 IP 所在国家一致
  - 注册当天别付费: 先免费低频用 3-5 天再充值; 不要退款/撤卡
[账号信息]
  - 全新邮箱 (自己注册的 Gmail 最稳) + 新手机号 (不要用接码平台)
  - 旧邮箱注销后 30 天内不能用于重建账号 (官方规定)
  - 别用 "Continue with Google" 登录旧 Google 账号
  - 触发 Persona KYC 人工审核基本等于放弃 (国内证件难过)
[设备与指纹 —— 比 Cookie 更关键]
  - 新 Chrome Profile / 无痕窗口换不掉 Canvas/WebGL/字体指纹
  - 主流做法: 指纹浏览器 (AdsPower/比特) 新建环境 + 独立 IP; 或换不常用浏览器/设备
  - "清理了还是秒封" 多半是设备指纹已被锁定
  - (可选) chrome://settings/content/all 搜 claude.ai 删 Local Storage (脚本清不到)
[养号]
  - 注册后先免费低频用 3-5 天, 别急着充值
==================================================
EOF
}

case "${1:-}" in
  backup)    phase_backup ;;
  browser)   phase_browser "${2:-}" ;;
  uninstall) phase_uninstall ;;
  delete)    phase_delete "${2:-}" ;;
  install)   phase_install ;;
  restore)   phase_restore "${2:-}" ;;
  verify)    phase_verify ;;
  all)       phase_backup && phase_browser "${2:-}" && phase_uninstall && phase_delete "${2:-}" \
             && phase_install && phase_restore "${2:-}" && phase_verify ;;
  *) echo "usage: $0 <backup|browser|uninstall|delete|install|restore|verify|all> [backup_dir]"; exit 2 ;;
esac
