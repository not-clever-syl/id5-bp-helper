# -*- coding: utf-8 -*-
"""服务器端文件读写：给页面的「导入 / 导出」子窗口用。

页面不再用浏览器原生文件选择框，而是弹子窗口列文件、点一下就完成。
所以这里提供三个能力：

    list_files(kind, prefix, pattern)  列出某个用途下可用的文件
    read_json(rel)                     读一个 json（限定在允许的目录内）
    write_json(rel, obj, overwrite)    写一个 json（限定在允许的目录内）

约定：
    * 三类导出文件各有自己的目录，用户自选目录即可，不再统一堆在 data/gen/
        监管数据    data/hunter/    <监管选手id>_<数字>.json
        求生角色池  data/survivor/  <队伍名>_<数字>.json
        报告        data/reports/   <监管id>_<数字>_vs_<队伍名>_game<局数>.json
    * 预制数据在 data/ 根下，只读，写操作一律挡住
    * 文件名必须过滤：只允许中文、字母、数字、下划线、连字符、点，
      且必须以 .json 结尾，挡住路径分隔符、..、控制字符等

命名规范（由用户定）：
    监管数据    <监管选手id>_<数字>.json
    求生角色池  <队伍名>_<数字>.json
    报告        <监管id>_<版本>_vs_<队伍名>_<版本>_game<局数>_<时间戳>.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CUSTOM = DATA / "custom"

# 三类导出目录（用户定）
DIR_HUNTER = "data/hunter"     # 监管数据
DIR_SURVIVOR = "data/survivor" # 求生角色池
DIR_REPORTS = "data/reports"   # 报告（含生成的报告）

EXPORT_DIRS = [DIR_HUNTER, DIR_SURVIVOR, DIR_REPORTS]

# 导入的候选只来自这三个目录。
# data/ 根下的是预制数据（bp_core、bp_query、bp_tree…），
# 它们是数据源而不是「配置」，混进导入列表只会让人误选。
IMPORT_DIRS = list(EXPORT_DIRS)

# 预制数据所在目录，任何写操作都要挡住
PRESET_DIR = "data"

# 文件名允许的字符：中文、字母、数字、下划线、连字符、点
_NAME_OK = re.compile(r"^[一-鿿A-Za-z0-9_.-]+$")
_MAX_NAME = 120


def check_name(name: str) -> str:
    """过滤文件名。不合格直接抛错。"""
    name = str(name or "").strip()
    if not name:
        raise ValueError("文件名为空")
    if len(name) > _MAX_NAME:
        raise ValueError(f"文件名过长（最多 {_MAX_NAME} 个字符）")
    if "/" in name or "\\" in name:
        raise ValueError("文件名不能包含路径分隔符")
    if ".." in name:
        raise ValueError("文件名不能包含 ..")
    if any(ord(c) < 32 for c in name):
        raise ValueError("文件名不能包含控制字符")
    if not _NAME_OK.match(name):
        raise ValueError("文件名只能包含中文、字母、数字、下划线、连字符和点")
    if not name.lower().endswith(".json"):
        raise ValueError("只支持 .json 文件")
    return name


def check_dirname(name: str) -> str:
    """校验路径里的目录段。

    目录段不需要 .json 后缀，但同样要挡住路径分隔符、..、控制字符
    和奇怪字符，避免拼出意料之外的目录。
    """
    name = str(name or "").strip()
    if not name:
        raise ValueError("目录名为空")
    if len(name) > _MAX_NAME:
        raise ValueError(f"目录名过长（最多 {_MAX_NAME} 个字符）")
    if "/" in name or "\\" in name:
        raise ValueError("目录名不能包含路径分隔符")
    if ".." in name:
        raise ValueError("目录名不能包含 ..")
    if any(ord(c) < 32 for c in name):
        raise ValueError("目录名不能包含控制字符")
    if not _NAME_OK.match(name):
        raise ValueError("目录名只能包含中文、字母、数字、下划线、连字符和点")
    return name


def _safe(rel: str, allowed: list) -> Path:
    """把相对路径解析成绝对路径，并确认它在允许范围内。"""
    rel = str(rel or "").strip().replace("\\", "/")
    if not rel:
        raise ValueError("路径为空")
    if rel.startswith("/") or ".." in rel.split("/"):
        raise ValueError("非法路径")
    p = (ROOT / rel).resolve()
    try:
        p.relative_to(ROOT)
    except ValueError:
        raise ValueError("路径越界")
    # 路径的每一段：最后一段是文件名（必须 .json），其余是目录段（不用 .json）
    parts = p.relative_to(ROOT).parts
    for part in parts[:-1]:
        check_dirname(part)
    check_name(parts[-1])
    # 必须落在某个允许的目录里
    ok = False
    for d in allowed:
        base = (ROOT / d).resolve()
        try:
            p.relative_to(base)
            ok = True
            break
        except ValueError:
            continue
    if not ok:
        raise ValueError("该目录不允许访问")
    return p


# 列表里附带 meta 时只取这几个字段：够页面排成一行给人看，
# 不用把整份报告（几十 KB）塞进列表接口。
_META_KEYS = ("team", "player", "game", "map", "generated_at",
              "scores_source", "roster_source", "ban_quota")


def _read_meta(path: Path):
    """读一个报告文件的 meta，只留可读字段。读不出来就返回 None。"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    m = d.get("meta") if isinstance(d, dict) else None
    if not isinstance(m, dict):
        return None
    return {k: m.get(k) for k in _META_KEYS}


def list_files(kind: str = "import", prefix: str = "",
               pattern: str = "", dirs: list | None = None,
               with_meta: bool = False) -> dict:
    """列出候选文件。

    kind="import"：三个配置目录（data/hunter、data/survivor、data/reports）
                   下的 json。预制数据不进导入列表 —— 它们是数据源，
                   不是可导入的配置。
    kind="export"：同上的三个目录。
    dirs：显式指定要列的目录（相对项目根，如 "data/reports"）。
    prefix：只看文件名以此开头的（用于「这个监管/这个队伍的版本」）。
    pattern：额外的正则过滤（只允许安全的正则字符）。
    with_meta：给每项附上报告 meta，并按生成时间倒序排。
    """
    if dirs:
        dirs = [d for d in dirs if d in EXPORT_DIRS] or list(EXPORT_DIRS)
    else:
        dirs = EXPORT_DIRS
    rx = None
    if pattern:
        try:
            rx = re.compile(pattern)
        except re.error:
            raise ValueError("过滤规则不合法")

    items = []
    for d in dirs:
        base = ROOT / d
        if not base.exists():
            continue
        for f in sorted(base.iterdir()):
            if not f.is_file():
                continue
            try:
                check_name(f.name)
            except ValueError:
                continue          # 名字不合规的直接不列出
            if prefix and not f.name.startswith(prefix):
                continue
            if rx and not rx.match(f.name):
                continue
            rel = f.relative_to(ROOT).as_posix()
            try:
                size = f.stat().st_size
                mtime = f.stat().st_mtime
            except OSError:
                size, mtime = 0, 0
            item = {
                "path": rel,
                "name": f.name,
                "dir": d,
                "size": size,
                "mtime": mtime,
            }
            if with_meta:
                item["meta"] = _read_meta(f)
            items.append(item)
    if with_meta:
        # 新的在前。generated_at 是「YYYY-MM-DD HH:MM」，可直接按字符串比；
        # 没有这个字段的退回文件修改时间，仍然能排到合理位置。
        items.sort(key=lambda x: (((x.get("meta") or {}).get("generated_at") or ""),
                                  x.get("mtime") or 0), reverse=True)
        return {"files": items, "dirs": dirs}
    # 导出目录里的排前面
    items.sort(key=lambda x: (0 if x["dir"] in EXPORT_DIRS else 1, x["path"]))
    return {"files": items, "dirs": dirs}


def read_json(rel: str) -> dict:
    # 读的范围：三个配置目录。预制数据只由后端自己读，
    # 不通过这个接口暴露给页面的导入功能。
    p = _safe(rel, IMPORT_DIRS)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在：{rel}")
    if p.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("文件太大")
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(rel: str, obj, overwrite: bool = False) -> dict:
    """写一个 json。

    三道保护：
        1. 目标必须落在三个导出目录（data/hunter、data/survivor、data/reports）
           里 —— 预制数据所在的 data/ 根本不在允许范围，写不进去。
        2. 默认不覆盖已存在的文件；要覆盖必须显式 overwrite=True。
        3. 版本号 1 的文件是预制数据（由 build_preset_configs.py 生成），
           永远不允许被页面导出覆盖 —— 改动只能存成更高版本。
    """
    p = _safe(rel, EXPORT_DIRS)
    if p.parent.resolve() == (ROOT / PRESET_DIR).resolve():
        raise ValueError("不能写入预制数据目录")
    # 版本 1 = 预制数据，禁止覆盖
    if overwrite and ver_of(p.name) == 1:
        raise ValueError(f"{p.name} 是预制数据（版本 1），不允许覆盖；请另存为新版本")
    if p.exists() and not overwrite:
        raise FileExistsError(f"文件已存在：{rel}（换个名字，或勾选覆盖）")
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=1)
    p.write_text(text, encoding="utf-8")
    return {"path": p.relative_to(ROOT).as_posix(),
            "bytes": len(text.encode("utf-8"))}


def next_name(prefix: str, subdir: str = DIR_HUNTER) -> str:
    """按「前缀_序号」规则给出下一个可用文件名。

        监管数据    <监管选手id>_<数字>.json
        求生角色池  <队伍名>_<数字>.json

    序号取同前缀下已存在的最大序号 +1，保证不覆盖已有文件。
    """
    prefix = str(prefix or "export").strip()
    prefix = re.sub(r'[\\/:*?"<>|]+', "_", prefix)[:60] or "export"
    base = ROOT / subdir
    mx = 0
    if base.exists():
        pat = re.compile(r"^" + re.escape(prefix) + r"_(\d+)\.json$", re.I)
        for f in base.iterdir():
            m = pat.match(f.name)
            if m:
                mx = max(mx, int(m.group(1)))
    return f"{prefix}_{mx + 1}.json"


def _safe_stamp(stamp=None) -> str:
    """时间戳：YYYYmmdd-HHMMSS。"""
    import time
    if not stamp:
        stamp = time.strftime("%Y%m%d-%H%M%S")
    stamp = re.sub(r"[^0-9A-Za-z-]+", "", str(stamp))[:24]
    return stamp or time.strftime("%Y%m%d-%H%M%S")


def ver_of(source: str, default: int = 1) -> int:
    """从一个配置文件名里取数字版本。

        "YuM_2.json"  → 2
        "YuM.json"    → default
    """
    m = re.search(r"_(\d+)\.json$", str(source or ""), re.I)
    return int(m.group(1)) if m else default


def next_report_name(player: str, team: str, game, subdir: str = DIR_REPORTS,
                     stamp=None, hunter_ver: int = 1, team_ver: int = 1) -> str:
    """报告版本文件名（用户定的规范）：

        <监管id>_<版本>_vs_<队伍名>_<版本>_game<局数>_<时间戳>.json

    例：YuM_2_vs_ACT_1_game3_20261106-114012.json

    两个版本号分别来自两份配置文件的数字版本：
        监管那一版  ← 监管数据配置（data/hunter/）里的版本号
        队伍那一版  ← 求生角色池配置（data/survivor/）里的版本号

    时间戳保证不同时间的生成不会互相覆盖。
    """
    player = re.sub(r'[\\/:*?"<>|]+', "_", str(player or "player"))[:40] or "player"
    team = re.sub(r'[\\/:*?"<>|]+', "_", str(team or "team"))[:40] or "team"
    game = int(game or 1)
    stamp = _safe_stamp(stamp)
    hv, tv = int(hunter_ver or 1), int(team_ver or 1)
    return f"{player}_{hv}_vs_{team}_{tv}_game{game}_{stamp}.json"


def report_pattern(player: str, team: str, game) -> str:
    """报告版本的匹配规则（供 list_files 过滤）。"""
    player = re.sub(r'[\\/:*?"<>|]+', "_", str(player or "player"))[:40] or "player"
    team = re.sub(r'[\\/:*?"<>|]+', "_", str(team or "team"))[:40] or "team"
    game = int(game or 1)
    return (r"^" + re.escape(player) + r"_(\d+)_vs_" + re.escape(team)
            + r"_(\d+)_game" + str(game) + r"_[0-9A-Za-z-]+\.json$")
