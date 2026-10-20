# coding: utf-8
"""QuantLab 开发目录 -> 夸克网盘 定期增量备份（纯标准库脚本，不依赖 LLM 推理）。

背景（2026-10-05 立）：
  D:\\QuantLab 共 13.4 GB / 14709 文件，其中 10 GB 是 78 个 .parquet（特征面板、信号扫描等
  可从 D:/astock 重新生成的中间产物，AGENTS.md 记载已被 gitignore）+ 741 MB .pkl 缓存。
  这些每次重训都会重写，全量上传既慢又无价值。本脚本只备份「不可再生的知识资产」：
  源码 / 文档 / 配置 / 报告 / 模型资产 / git 历史（含未推送提交）。

方案：
  - manifest（state.json）记录每个纳入文件的 (size, mtime_ns)，跨次运行做差分
  - full 模式：打包全部纳入文件（约 1.0 GB）+ git bundle，锚点快照；首次运行强制 full
  - auto 模式：每月走一次 full（判据=state 的 last_full 是否落在当月），其余日走
    increment（只打包新增/修改文件，不含 git bundle）
  - 每次归档内含 MANIFEST.txt（文件清单 + git HEAD + 归档类型），保证可还原可校验
  - 上传成功后原子推进 manifest（失败不动 state，避免丢变更）

一致性铁律（2026-10-05 修正）：
  A) 归档 sha256 必须在 **zip 关闭之后** 对最终文件计算（关闭前算的是残缺包的摘要）。
     摘要不能写进归档内部的 MANIFEST（自引用悖论），改记在 backup.log 与 state.json。
  B) 任一文件写入 zip 失败 -> 整个归档作废并中止，**不推进 manifest**。
     否则该文件会被误标为「已备份」而永久漏备份。
  C) full 模式的 git bundle 失败 -> 致命中止，不推进 manifest。
     仓库有 41 个未推送提交，缺了 bundle 的 full 不构成可还原的锚点快照。

安全红线（2026-10-05 修正，务必保持）：
  1) SECRETS 名单内的凭据文件一律排除，**禁止上传到网盘**。这些文件被 .gitignore 排除，
     正是因为含真实密钥。排除动作写日志，便于审计。
  2) STATE_DIR 自身必须排除：work/ 下是本脚本产出的 zip（最大约 700 MB），
     若纳入扫描会导致「归档包含上一个归档」的指数级膨胀。
  3) 首次运行（无 state）强制 full，避免把首次全量误标成增量。

CLI 契约（quarkclouddrive skill 硬约束）：
  upload / create-folder 的 --parent-fid 是**选填**参数，用户未指定目录时必须省略，
  严禁自行补 "0"（"0" 仅代表根目录）。本脚本首次运行时省略该参数用服务端默认目录，
  之后复用 create-folder 返回的真实 FID。--session-input/--session-id 仅用于质量追踪。

用法：
  python quark_backup.py                    # auto：本月未全量则 full，否则 increment
  python quark_backup.py --full             # 强制全量快照
  python quark_backup.py --increment        # 强制增量
  python quark_backup.py --dry-run          # 只列出将要打包/上传的内容，不落盘不上传
  python quark_backup.py --audit            # 打印排除明细（含凭据排除审计），不打包
  python quark_backup.py --skip-upload      # 只在本地生成归档（排查用）
  python quark_backup.py --state-dir <dir>  # 改 manifest 位置

退出码：0 成功/无变更，1 上传失败，2 前置条件不满足，3 备份自身异常。
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = "D:/QuantLab"
SKILL_DIR = os.path.expanduser("~/.trae-cn/skills/quarkclouddrive")
QUARK_CLI = os.path.join(SKILL_DIR, "scripts", "quark-drive.cjs")

STATE_DIR = os.path.join(REPO, "data", "quark_backup")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
LOG_FILE = os.path.join(STATE_DIR, "backup.log")
LOCK_FILE = os.path.join(STATE_DIR, "backup.lock")
WORK_DIR = os.path.join(STATE_DIR, "work")

# 夸克网盘备份目录名（放在 CLI 默认目录下，不传 --parent-fid）
REMOTE_ROOT_NAME = "QuantLab_backup"

SESSION_ID = "1791185752-a7k3m9"
SESSION_INPUT = "建个自动化任务，把当前的开发目录备份到夸克网盘上去，定期更新改动"

PYTHON_TAG = "%d.%d.%d" % sys.version_info[:3]

# ---------------------------------------------------------------------------
# 排除规则
# ---------------------------------------------------------------------------

# 可再生产物：中间数据面板 / 缓存 / 工具内部目录。命中即跳过。
EXCLUDE_DIRS = {
    ".git",              # 单独处理：.git 用 git bundle 纳入（保留历史且体积小）
    "__pycache__", ".pytest_cache", ".mypy_cache", "node_modules",
    "data/cache", "data/quark_backup",   # data/quark_backup 是本脚本自身产物，必须排除
    ".mimocode", ".codegraph", ".freebuff", ".omo", ".zcode", ".commandcode",
    ".trae", ".workbuddy", ".workbuddy-ai", ".trae-html-share-packages",
    ".engram", ".quarkclouddrive",
    "browse", ".tmp", ".tmp_quark", "research_external", "mcp",
    "memory",            # agent 运行时记忆，非知识资产
}

EXCLUDE_SUFFIX = (".pyc", ".pyo", ".log", ".err", ".pid", ".tmp", ".lock",
                  ".pack", ".idx")

# 时间戳备份文件：foo.bak_20260814_205119 / foo.bak（.gitignore 已排除 *.bak_*）
BAK_RE = re.compile(r"\.bak(_\d{8}(_\d{6})?)?$", re.IGNORECASE)

# 单文件超过此大小视为数据产物（模型 txt 资产 32 MB 保留），跳过
MAX_FILE_MB = 64

# ---------------------------------------------------------------------------
# 凭据排除红线：以下文件含真实密钥/令牌，禁止上传到网盘
# （均为 .gitignore 排除项，属本地私密配置，恢复时需在本机重建）
# ---------------------------------------------------------------------------
SECRET_FILES = {
    ".mcp.json",
    "config/data_source_keys.json",
    "config/.web_search_keys.yaml",
    "config/web_search_keys.yaml",
    "config/qmt_account.yaml",
    ".env",
}
SECRET_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"^\.env(\..+)?$",
        r"^.*_keys\.json$",
        r"^.*keys\.yaml$",
        r"^.*secret.*\.(json|ya?ml|txt)$",
        r"^.*credential.*\.(json|ya?ml|txt)$",
        r"^.*\.pem$",
        r"^.*\.p12$",
        r"^id_rsa",
        r"^.*token.*\.json$",
        r"^.*cookie.*\.txt$",
    )
]

# 本地 work 目录最多保留归档数（失败重试时防止磁盘被撑满）
MAX_LOCAL_ARCHIVES = 2


def is_secret(rel_path, name):
    """判断文件是否为含凭据文件。返回 (是否敏感, 命中原因)。"""
    if rel_path in SECRET_FILES:
        return True, "凭据白名单"
    for pat in SECRET_PATTERNS:
        if pat.search(name):
            return True, "凭据命名模式 %s" % pat.pattern
    return False, ""


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        # 日志轮转，避免无限增长
        try:
            if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5 * 1024 * 1024:
                os.replace(LOG_FILE, LOG_FILE + ".1")
        except OSError:
            pass
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def excluded(rel_path, name, size):
    """判断单个文件是否排除。rel_path 为相对 REPO 的 posix 路径。"""
    parts = rel_path.split("/")
    for i in range(len(parts) - 1):
        prefix = "/".join(parts[: i + 1])
        if prefix in EXCLUDE_DIRS:
            return True
    if any(name.endswith(s) for s in EXCLUDE_SUFFIX):
        return True
    if BAK_RE.search(name):
        return True
    if size > MAX_FILE_MB * 1024 * 1024:
        return True
    return False


def scan(exclude_list=()):
    """扫描纳入备份的文件。

    返回 (files, stats, denied)：
      files  = {rel_posix: (size, mtime_ns)}
      stats  = {"big": n, "excluded": n, "secret": n}
      denied = [(rel, reason)]  被凭据规则排除的文件（审计用）
    """
    files = {}
    stats = {"big": 0, "excluded": 0, "secret": 0}
    denied = []
    skip = set(exclude_list)
    for root, dirs, names in os.walk(REPO):
        rel_root = os.path.relpath(root, REPO).replace("\\", "/")
        if rel_root == ".":
            rel_root = ""
        # 目录级排除（同时防止 os.walk 进入 data/quark_backup 自包含）
        dirs[:] = [d for d in dirs
                   if ("%s/%s" % (rel_root, d) if rel_root else d) not in EXCLUDE_DIRS
                   and d != ".git"]
        for n in names:
            full = os.path.join(root, n)
            rel = "%s/%s" % (rel_root, n) if rel_root else n
            # 任意层级的 .git 都不进归档（历史统一由 git bundle 承载）
            if ".git" in rel.split("/"):
                continue
            if rel in skip:
                stats["excluded"] += 1
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue

            secret, reason = is_secret(rel, n)
            if secret:
                stats["secret"] += 1
                denied.append((rel, reason))
                continue
            if excluded(rel, n, st.st_size):
                if st.st_size > MAX_FILE_MB * 1024 * 1024:
                    stats["big"] += 1
                else:
                    stats["excluded"] += 1
                continue
            files[rel] = (st.st_size, st.st_mtime_ns)
    return files, stats, denied


def git_info():
    """取 git HEAD / 分支 / 未推送提交数 / 工作区改动数。仓库不可用时返回占位。"""

    def run(*args):
        try:
            p = subprocess.run(["git", "-C", REPO] + list(args),
                               capture_output=True, text=True, timeout=120,
                               encoding="utf-8", errors="replace")
            return p.stdout.strip() if p.returncode == 0 else ""
        except Exception:
            return ""

    head = run("rev-parse", "HEAD")
    branch = run("rev-parse", "--abbrev-ref", "HEAD")
    dirty = len(run("status", "--porcelain").splitlines())
    ahead = run("rev-list", "--count", "@{u}..HEAD")
    return {"head": head, "head_short": head[:8], "branch": branch,
            "dirty_files": dirty, "unpushed": ahead}


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"files": {}, "last_full": None, "last_run": None, "runs": 0}
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            st = json.load(fh)
        if not isinstance(st, dict):
            raise ValueError("state 根节点不是对象")
        st.setdefault("files", {})
        st.setdefault("runs", 0)
        return st
    except Exception as exc:
        log("state.json 解析失败（%s），按首次运行处理（原文件保留为 .corrupt）" % exc)
        try:
            os.replace(STATE_FILE, STATE_FILE + ".corrupt")
        except OSError:
            pass
        return {"files": {}, "last_full": None, "last_run": None, "runs": 0}


def save_state(state):
    """原子写 state.json。"""
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, STATE_FILE)


def git_bundle(path):
    """打 git bundle 保住完整历史（含未推送提交）。失败返回 None。"""
    try:
        p = subprocess.run(["git", "-C", REPO, "bundle", "create", path, "--all"],
                           capture_output=True, text=True, timeout=3600,
                           encoding="utf-8", errors="replace")
        if p.returncode == 0 and os.path.exists(path):
            return os.path.getsize(path)
        log("git bundle 失败：%s" % (p.stderr or p.stdout)[:200])
        if os.path.exists(path):
            os.remove(path)
        return None
    except Exception as exc:
        log("git bundle 异常：%s" % exc)
        return None
    finally:
        if os.path.exists(path) and os.path.getsize(path) == 0:
            os.remove(path)


def sha256_of(path, chunk=4 * 1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class BackupFatal(Exception):
    """归档构建失败：必须中止且不推进 manifest。"""


def build_archive(kind, changed, stamp, with_bundle):
    """打包变更文件（+ 可选 git bundle）+ MANIFEST.txt。

    返回 (归档路径, 成功文件数, git信息, 最终sha256, 失败清单)。
    失败清单非空表示归档不完整；full 缺 bundle 视为致命。调用方须据此决定是否推进 manifest。
    """
    os.makedirs(WORK_DIR, exist_ok=True)
    arc = os.path.join(WORK_DIR, "QuantLab_%s_%s.zip" % (kind, stamp))
    gi = git_info()
    added = 0
    failures = []
    bundle_mb = 0.0
    bundle_state = "not included (increment)"
    t0 = time.time()
    try:
        with zipfile.ZipFile(arc, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for rel in sorted(changed):
                full = os.path.join(REPO, rel.replace("/", os.sep))
                try:
                    zf.write(full, rel)
                    added += 1
                except Exception as exc:
                    failures.append((rel, "写入失败: %s" % exc))
                    log("打包失败 %s：%s" % (rel, exc))
            if with_bundle:
                bundle_path = os.path.join(WORK_DIR, "repo.bundle")
                bsize = git_bundle(bundle_path)
                if not bsize:
                    # 铁律 C：full 缺 bundle = 不可还原的锚点，致命
                    failures.append(("_git/repo.bundle", "git bundle 生成失败"))
                    bundle_state = "FAILED"
                else:
                    bundle_mb = bsize / 1048576.0
                    # bundle 已是压缩过的 pack，再 deflate 纯属浪费 CPU
                    zf.write(bundle_path, "_git/repo.bundle",
                             compress_type=zipfile.ZIP_STORED)
                    os.remove(bundle_path)
                    bundle_state = "included (%.1f MB)" % bundle_mb
            else:
                log("增量归档：不含 git bundle（历史由每月 full 快照保全）")
            manifest = [
                "QuantLab backup archive",
                "kind      : %s" % kind,
                "created   : %s" % stamp,
                "repo      : %s" % REPO,
                "python    : %s" % PYTHON_TAG,
                "git HEAD  : %s (%s)" % (gi["head_short"], gi["branch"]),
                "unpushed  : %s commits" % (gi["unpushed"] or "0"),
                "dirty     : %s files" % gi["dirty_files"],
                "files     : %d / %d" % (added, len(changed)),
                "bundle    : %s" % bundle_state,
                "note      : 本归档 sha256 记录在 backup.log 与 state.json（摘要无法自引用）。",
                "",
                "恢复：解压覆盖到目标目录；git 历史用 git clone _git/repo.bundle 还原。",
                "注意：含凭据的本地私密配置（见脚本 SECRETS 名单）不在归档内，需在本机重建。",
                "",
                "--- file list ---",
            ]
            manifest += sorted(changed)
            zf.writestr("MANIFEST.txt", "\n".join(manifest) + "\n")
    except Exception as exc:
        log("归档写入异常：%r" % exc)
        try:
            os.remove(arc)
        except OSError:
            pass
        raise BackupFatal("归档写入异常：%r" % exc)

    # 铁律 A：关闭后再算摘要，才是最终归档的真实 sha256
    digest = sha256_of(arc)
    log("归档写入完成：%d 文件，耗时 %.0fs，sha256=%s"
        % (added, time.time() - t0, digest))
    return arc, added, gi, digest, failures


def prune_local_archives(keep_current=None):
    """清理 work/ 下过多的旧归档，防止失败重试撑满磁盘。"""
    try:
        items = sorted(glob.glob(os.path.join(WORK_DIR, "QuantLab_*.zip")),
                       key=os.path.getmtime, reverse=True)
        for old in items[max(MAX_LOCAL_ARCHIVES, 1):]:
            if keep_current and os.path.abspath(old) == os.path.abspath(keep_current):
                continue
            try:
                os.remove(old)
                log("清理旧归档：%s" % os.path.basename(old))
            except OSError:
                pass
    except Exception as exc:
        log("清理旧归档异常（忽略）：%s" % exc)


def run_cli(args, timeout=10800):
    cmd = ["node", QUARK_CLI] + list(args) + [
        "--session-input", SESSION_INPUT, "--session-id", SESSION_ID]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.stdout or ""
    except subprocess.TimeoutExpired:
        log("CLI 超时（%ds）：%s" % (timeout, " ".join(args[:3])))
        return ""
    except Exception as exc:
        log("CLI 异常：%s" % exc)
        return ""


def parse_ndjson(text):
    """取最后一条 type=result 行。"""
    res = None
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("type") == "result":
            res = obj
    return res or {}


def create_remote_dir(dir_name, parent_fid=None):
    """创建（幂等）远端文件夹，返回 (fid, full_path)。parent_fid=None 时不传该参数。

    skill 硬约束：用户未指定目录时必须省略 --parent-fid，禁止自行补 "0"。
    """
    args = ["create-folder", "--dir-path", dir_name]
    if parent_fid:
        args += ["--parent-fid", parent_fid]
    r = parse_ndjson(run_cli(args))
    if r.get("code") == 0 and r.get("data", {}).get("fid"):
        return r["data"]["fid"], r.get("data", {}).get("full_path", "")
    log("创建远端目录 %s 失败：code=%s msg=%s"
        % (dir_name, r.get("code"), (r.get("msg") or "")[:150]))
    return None, ""


def acquire_lock():
    """粗粒度互斥锁，防止上一次未跑完时新一次并发破坏 manifest。"""
    os.makedirs(STATE_DIR, exist_ok=True)
    for attempt in (0, 1):
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, ("%d %s" % (os.getpid(), time.strftime("%Y-%m-%d %H:%M:%S"))).encode())
            os.close(fd)
            return True
        except FileExistsError:
            # 陈旧锁（>6h）视为异常退出残留
            try:
                age = time.time() - os.path.getmtime(LOCK_FILE)
            except OSError:
                age = 0
            if attempt == 0 and age > 6 * 3600:
                log("发现陈旧锁（%.1f 小时），清理后重试" % (age / 3600.0))
                try:
                    os.remove(LOCK_FILE)
                except OSError:
                    pass
                continue
            log("已有备份进程在运行（锁文件存在），本次退出")
            return False
        except OSError as exc:
            log("加锁失败：%s" % exc)
            return False
    return False


def release_lock():
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


def main():
    global STATE_DIR, STATE_FILE, LOG_FILE, LOCK_FILE, WORK_DIR

    ap = argparse.ArgumentParser(description="QuantLab -> 夸克网盘 定期增量备份")
    ap.add_argument("--full", action="store_true", help="强制全量快照")
    ap.add_argument("--increment", action="store_true", help="强制增量")
    ap.add_argument("--dry-run", action="store_true", help="只报告不落盘不上传")
    ap.add_argument("--audit", action="store_true", help="打印排除明细（含凭据审计）")
    ap.add_argument("--skip-upload", action="store_true", help="只生成本地归档")
    ap.add_argument("--exclude", action="append", default=[], metavar="REL",
                    help="永久排除某个相对路径（写入 state，可重复）。"
                         "仅在文件被其他进程长期占用、导致打包必然失败时使用，"
                         "属显式取舍：被排除文件此后不再备份")
    ap.add_argument("--state-dir", default=STATE_DIR, help="manifest / 日志 / work 根目录")
    args = ap.parse_args()

    if args.state_dir != STATE_DIR:
        STATE_DIR = args.state_dir
        STATE_FILE = os.path.join(STATE_DIR, "state.json")
        LOG_FILE = os.path.join(STATE_DIR, "backup.log")
        LOCK_FILE = os.path.join(STATE_DIR, "backup.lock")
        WORK_DIR = os.path.join(STATE_DIR, "work")

    if not os.path.isdir(REPO):
        log("SKIP：仓库目录不存在 %s" % REPO)
        return 2
    need_cli = not (args.dry_run or args.audit or args.skip_upload)
    if need_cli:
        if not os.path.exists(QUARK_CLI):
            log("SKIP：夸克 CLI 未找到 %s" % QUARK_CLI)
            return 2
        if not shutil.which("node"):
            log("SKIP：PATH 中找不到 node，无法调用夸克 CLI（S4U 环境下常见）")
            return 2

    state = load_state()
    # --exclude 是持久化取舍：合并进 state，后续运行沿用
    merged_exclude = sorted(set(state.get("exclude", [])) | set(args.exclude))
    if merged_exclude != state.get("exclude", []):
        log("排除名单新增 %d 项：%s"
            % (len(merged_exclude) - len(state.get("exclude", [])),
               ", ".join(sorted(set(merged_exclude) - set(state.get("exclude", [])))) or "-"))
    state["exclude"] = merged_exclude

    cur, stats, denied = scan(merged_exclude)
    old = state.get("files", {})
    first_run = not old

    log("=" * 70)
    log("QuantLab->夸克网盘备份 | python %s | 首次运行=%s" % (PYTHON_TAG, first_run))
    log("凭据排除：%d 个文件（含真实密钥，永不上传）" % stats["secret"])
    for rel, reason in denied:
        log("   [凭据排除] %s  (%s)" % (rel, reason))

    # 模式选择：首次运行强制 full（避免把首次全量误标为增量）
    # 全量节奏 = 每月一次。判据用 state 的 last_full 是否落在当月，而非「今天是 1 号」：
    # 1 号掉线/关机/任务未跑时，「是否 1 号」会让整月没有全量锚点，且缺口不可自愈；
    # 「当月是否已全量」则下次运行自动补上，最坏只晚一天。
    cur_month = time.strftime("%Y%m")
    month_done = (state.get("last_full") or "")[:6] == cur_month

    if args.full or first_run:
        kind = "full"
    elif args.increment:
        kind = "increment"
    else:
        kind = "full" if not month_done else "increment"

    if kind == "increment":
        changed = {k: v for k, v in cur.items()
                   if k not in old or old[k][0] != v[0] or old[k][1] != v[1]}
    else:
        changed = dict(cur)

    cur_mb = sum(v[0] for v in cur.values()) / 1048576.0
    chg_mb = sum(v[0] for v in changed.values()) / 1048576.0
    log("模式=%s  纳入文件=%d (%.1f MB)  本次变更=%d (%.1f MB)  跳过=%d(大文件%d/其他%d)"
        % (kind, len(cur), cur_mb, len(changed), chg_mb,
           stats["excluded"] + stats["big"], stats["big"], stats["excluded"]))
    gi = git_info()
    log("git HEAD=%s(%s) 未推送=%s 工作区改动=%d"
        % (gi["head_short"], gi["branch"], gi["unpushed"] or "0", gi["dirty_files"]))

    if args.audit:
        log("--- audit: 将被打包的目录分布（前 25）---")
        buckets = {}
        for rel in changed:
            top = rel.split("/")[0] if "/" in rel else "(root)"
            buckets[top] = buckets.get(top, 0) + 1
        for top, cnt in sorted(buckets.items(), key=lambda kv: -kv[1])[:25]:
            log("   %-40s %d" % (top, cnt))
        log("audit 结束，未落盘未上传")
        return 0

    if not changed and kind == "increment":
        log("无变更，跳过本次备份")
        return 0

    if args.dry_run:
        log("dry-run：前 20 个待备份文件")
        for rel in sorted(changed)[:20]:
            log("   %s (%.1f KB)" % (rel, changed[rel][0] / 1024.0))
        log("dry-run 结束，未落盘未上传")
        return 0

    if not acquire_lock():
        return 0
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        arc, added, gi, digest, failures = build_archive(
            kind, changed, stamp, with_bundle=(kind == "full"))
        size_mb = os.path.getsize(arc) / 1048576.0
        log("归档已生成：%s（%d 文件，%.1f MB，sha256=%s）"
            % (os.path.basename(arc), added, size_mb, digest[:16]))

        # 铁律 B：归档不完整 -> 绝不推进 manifest（否则这些文件永久漏备份）
        if failures:
            log("=" * 70)
            log("归档不完整（%d 项失败），本次**不推进 manifest**，下次运行将重试：" % len(failures))
            for rel, reason in failures[:50]:
                log("   [FAILED] %s  (%s)" % (rel, reason))
            if len(failures) > 50:
                log("   ... 另有 %d 项" % (len(failures) - 50))
            log("若某文件被其他进程长期占用导致必然失败，可显式取舍：")
            log("   python quark_backup.py --exclude \"<相对路径>\"")
            log("作废本地归档：%s" % arc)
            try:
                os.remove(arc)
            except OSError:
                pass
            return 1

        if args.skip_upload:
            log("skip-upload：保留本地归档 %s" % arc)
            prune_local_archives(arc)
            return 0

        # 首次运行用服务端默认目录（不传 --parent-fid）；之后复用真实 FID
        root_fid = state.get("remote_root_fid") or ""
        root_path = state.get("remote_root_path") or ""
        if not root_fid:
            root_fid, root_path = create_remote_dir(REMOTE_ROOT_NAME)
            if not root_fid:
                log("无法确定备份根目录，终止（未推进 manifest）")
                prune_local_archives(arc)
                return 1
            log("备份根目录已就绪：%s（FID=%s）" % (root_path or REMOTE_ROOT_NAME, root_fid))

        sub_fid, sub_path = create_remote_dir(stamp, parent_fid=root_fid)
        if not sub_fid:
            log("无法创建本次归档子目录，终止（未推进 manifest）")
            prune_local_archives(arc)
            return 1

        t0 = time.time()
        res = parse_ndjson(run_cli(["upload", arc, "--parent-fid", sub_fid]))
        ok = res.get("code") == 0
        log("上传%s：code=%s msg=%s 耗时=%.0fs"
            % ("成功" if ok else "失败", res.get("code"),
               (res.get("msg") or "")[:120], time.time() - t0))
        if not ok:
            log("上传失败，manifest 不推进（下次重跑仍覆盖本次变更）")
            log("本地归档保留：%s" % arc)
            prune_local_archives(arc)
            return 1

        data = res.get("data", {})
        remote_desc = data.get("fullPath") or sub_path or ""
        log("上传明细：成功数=%s 秒传=%s 远端目录=%s"
            % (data.get("successCount"), data.get("instantUpload"),
               remote_desc or "(默认目录)"))

        # 仅在上传成功后推进 manifest（原子写）
        state["files"] = {k: list(v) for k, v in cur.items()}
        state["exclude"] = merged_exclude
        state["last_run"] = stamp
        state["last_kind"] = kind
        state["last_remote"] = {"dir": remote_desc, "fid": sub_fid,
                                "successCount": data.get("successCount"),
                                "sha256": digest, "size_mb": round(size_mb, 1),
                                "files": added, "bundle": kind == "full"}
        state["remote_root_fid"] = root_fid
        state["remote_root_path"] = root_path
        state["runs"] = state.get("runs", 0) + 1
        if kind == "full":
            state["last_full"] = stamp
        save_state(state)
        log("manifest 已推进（累计 %d 次，last_full=%s）"
            % (state["runs"], state.get("last_full")))

        try:
            os.remove(arc)
        except OSError:
            pass
        prune_local_archives()
        return 0
    except BackupFatal as exc:
        log("备份中止（manifest 未推进）：%s" % exc)
        return 3
    finally:
        release_lock()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BackupFatal as exc:
        log("备份异常终止：%s" % exc)
        sys.exit(3)
    except Exception as exc:
        log("备份异常终止：%r" % exc)
        sys.exit(3)