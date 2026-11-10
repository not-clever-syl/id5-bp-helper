# -*- coding: utf-8 -*-
"""把赛事数据页导出的 CSV 转成 bp_core.json 的半局结构。

CSV 是逐半局的宽表：一行 = 一个半局，同一个场次的上下半局各一行。
列名很多，这里只取本工具用得到的那些。

字段对应
────────
    CSV                        bp_core
    ──────────────────────────────────────────────────────────
    屠选 / 屠名                hunter / hunter_player
    使用角色1~4                survivors（去空）
    人BAN*                     hunter_bans   —— 求生方被 ban 的角色
    屠BAN*                     survivor_bans —— 监管方被 ban 的角色
    地图                       map
    主分 / 客分                score + escape + kill
    场次号                     game（「加」= 加时，记 4 且 tiebreak）
    屠队主客                   hunter_team 是主队还是客队，用来定上半局

两个容易踩错的地方
──────────────────
1. ban 列的编号「人BAN1-1 / 人BAN2-1 / …」里的第一个数字是**该半局内的
   ban 轮次**，不是第几局，所以整个半局的 ban 要跨所有轮次列合并。
2. 比分是 5 分制（0/1/2/3/5），5 分代表四跑/四抓，
   换算成逃脱人数时要变成 4 而不是 5。

队伍名和选手名都做归一化：CSV 里可能是缩写（WBG）或小写（mhxm），
统一按 bp_core.json 里已有的写法对齐，避免同一个队/人被当成两个。
"""
from __future__ import annotations

import csv
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "data" / "bp_core.json"

# 5 分制 → 逃脱人数
SURV_COUNT = {0: 0, 1: 1, 2: 2, 3: 3, 5: 4}
# 列名里出现但表示「本局没有」的占位符
PLACEHOLDERS = {"", "-", "—", "无"}


# ── 名字归一化 ────────────────────────────────────────────────
def _core_names():
    """bp_core 里已有的队伍名和选手名，用来对齐 CSV 的写法。"""
    try:
        d = json.loads(CORE.read_text(encoding="utf-8"))
    except Exception:
        return set(), {}
    teams, players = set(), {}
    for h in d.get("halves") or []:
        for k in ("survivor_team", "hunter_team"):
            if h.get(k):
                teams.add(h[k])
        p = h.get("hunter_player")
        if p:
            players[p.lower()] = p
    return teams, players


def _team_map(extra_teams=None):
    """CSV 队伍名 → 正式队名。

    先按 bp_core 里已有的全名建映射（全名本身 + 末段缩写都算），
    再叠加调用方给的额外映射（比如登记过的新队）。
    """
    teams, _ = _core_names()
    out = {}
    for t in teams:
        out[t] = t
        # 「北京WBG」这种带前缀的，允许 CSV 只用后半段
        for sep in ("·", " ", ""):
            if sep and sep in t:
                out[t.split(sep)[-1]] = t
        if len(t) > 2:
            out.setdefault(t[-3:], t)
    # 常见缩写：CSV 里的简称和全称对不上，只能写死
    shorthand = {"WBG": "北京WBG", "GG": "成都GG", "Wolves": "成都Wolves"}
    for short, full in shorthand.items():
        if full in teams:
            out[short] = full
    for k, v in (extra_teams or {}).items():
        out[k] = v
    return out


def _player_map():
    """CSV 选手名（小写）→ 正式写法。

    来源：bp_core 里已有的写法 ∪ 自定义名单（roster.json）——
    新登记但还没打过比赛的选手只在 roster 里。
    """
    _, players = _core_names()
    try:
        import roster_store
        for p in roster_store.load()["players"]:
            n = (p.get("name") or "").strip()
            if n:
                players.setdefault(n.lower(), n)
    except Exception:
        pass
    return players


def _canon_team(raw, tmap):
    n = (raw or "").strip()
    return tmap.get(n, n)


def _canon_player(raw, pmap):
    n = (raw or "").strip()
    return pmap.get(n.lower(), n)


# ── CSV 读取 ─────────────────────────────────────────────────
def read_rows(path):
    """读 CSV 文件，返回 (行字典列表, 列名列表)。兼容 UTF-8 BOM / GBK。"""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("CSV 编码无法识别（试过 UTF-8 / GBK）")
    return read_rows_from_text(text)


def read_rows_from_text(text):
    """从字符串解析 CSV，返回 (行字典列表, 列名列表)。"""
    if text.startswith("\ufeff"):
        text = text[1:]
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return [], []
    head = [c.strip() for c in rows[0]]
    out = []
    for r in rows[1:]:
        if not any(x.strip() for x in r):
            continue
        out.append({k: (r[i] if i < len(r) else "") for i, k in enumerate(head)})
    return out, head


def _g(row, key):
    return (row.get(key) or "").strip()


def _game_no(row):
    """场次号 → 数字。加时局记 6，排在最后，落库时再折成 4。"""
    raw = _g(row, "场次号")
    return int(raw) if raw.isdigit() else 6


def _multi_cols(row, head, prefix):
    """把 prefix 开头的所有列合并成一个列表。

    ban 列的编号里带的是该半局内的 ban 轮次，所以整个半局要把
    所有轮次列拼起来才是完整 ban 列表。
    """
    out = []
    for col in head:
        if not col.startswith(prefix):
            continue
        v = (row.get(col) or "").strip()
        if v and v not in PLACEHOLDERS and not v.startswith("全局"):
            out.append(v)
    return out


# ── 转换 ─────────────────────────────────────────────────────
def to_halves(rows, head, team_extra=None):
    """CSV 行 → 半局列表。

    seq 是**每场从 1 重新开始**的场内序号（全项目都靠「seq 变小」来切分
    比赛：core_check.split_matches、map_pick、页面 raw.html），
    所以这里不能给整张表编一个全局连续的号。
    """
    halves, _matches = to_core(rows, head, team_extra)
    return halves


def to_core(rows, head, team_extra=None):
    """CSV 行 → (半局列表, 主客数组)。

    主客数组一场一项 {home, away, teams}，按比赛先后排列，
    和 halves 用 seq 切出来的场次一一对应。home 取自 CSV 的「主场」列。
    """
    tmap = _team_map(team_extra)
    pmap = _player_map()

    groups, order = defaultdict(list), []
    for r in rows:
        key = (_g(r, "日期"), _g(r, "主场"), _g(r, "客场"))
        if key not in groups:
            order.append(key)
        groups[key].append(r)

    halves = []
    matches = []
    for key in order:
        seq = 0                                     # 每场一节，从 1 重开
        grp = groups[key]
        # 场次升序；同一场次内按 CSV 原始顺序（上、下半局）
        grp = sorted(grp, key=_game_no)
        home = _canon_team(_g(grp[0], "主场"), tmap)
        away = _canon_team(_g(grp[0], "客场"), tmap)
        matches.append({"home": home, "away": away, "teams": [home, away]})

        by_game = defaultdict(list)
        for r in grp:
            by_game[_game_no(r)].append(r)

        for game in sorted(by_game):
            for i, r in enumerate(by_game[game]):
                seq += 1
                extra = game > 5                     # 加时
                real_game = 4 if extra else game

                hteam = _canon_team(_g(r, "屠队"), tmap)
                # 求生方是另一方
                ste = away if hteam == home else home

                survs = [_g(r, f"使用角色{k}") for k in (1, 2, 3, 4)]
                survs = [c for c in survs if c and c not in PLACEHOLDERS]

                hs = int(_g(r, "主分") or 0)
                as_ = int(_g(r, "客分") or 0)
                s, k = (hs, as_) if ste == home else (as_, hs)

                halves.append({
                    "seq": seq,
                    "game": real_game,
                    "side": "upper" if i == 0 else "lower",
                    "tiebreak": extra,
                    "survivor_team": ste,
                    "hunter_team": hteam,
                    "survivors": survs,
                    "hunter": _g(r, "屠选"),
                    "hunter_player": _canon_player(_g(r, "屠名"), pmap),
                    "hunter_bans": _multi_cols(r, head, "人BAN"),
                    "survivor_bans": _multi_cols(r, head, "屠BAN"),
                    "score": "%d:%d" % (s, k),
                    "escape": SURV_COUNT.get(s, s),
                    "kill": SURV_COUNT.get(k, k),
                    "map": _g(r, "地图"),
                })
    return halves, matches


def convert(path, team_extra=None):
    """读取 CSV 并转成半局列表。"""
    rows, head = read_rows(path)
    if not rows:
        raise ValueError("CSV 里没有数据行")
    return to_halves(rows, head, team_extra)


# ── 命令行走一遍（调试用）──────────────────────────────────────
def main():
    path = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "tmp" / "query.csv")
    halves = convert(path)
    print("半局 %d，涉及 %d 支队伍" % (
        len(halves),
        len({h["survivor_team"] for h in halves}
            | {h["hunter_team"] for h in halves})))
    print("首条：", json.dumps(halves[0], ensure_ascii=False))


if __name__ == "__main__":
    main()
