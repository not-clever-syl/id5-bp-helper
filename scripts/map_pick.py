# -*- coding: utf-8 -*-
"""地图选择偏好：哪支队伍在第几局选了什么图。

════════════════════════════════════════════════════════════════
口径
════════════════════════════════════════════════════════════════
图权按局次轮转（rules_v11.md 1.4）：

    第一局  主队选图
    第二局  客队选图
    第三局  主队选图
    第四局  客队选图
    第五局  主队选图
    加时    客队选图（BO3 加赛视同第四局）

所以「某队在第 N 局选了什么图」不能直接按局次统计 —— 必须先知道
该场他是不是主队，否则主客队的选图会混在一起。

主客队记在 bp_core.json 的顶层 matches 里：一场一项，home 是主队、
away 是客队，按比赛先后排列（可在「原始数据」页改）。

一场比赛的主客由 matches 里的顺序决定，不再按「队名对」查表 ——
同一对组合在一季里可能交手两次，第二循环主客互换，
用队名对当键会把两场混成一场。

地图不可重复选择，但可以重复禁用；本页只统计「选择」，不含禁图。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "data" / "bp_core.json"

# 局次 → 图权归谁
SLOTS = [
    ("第1局", "home"),
    ("第2局", "away"),
    ("第3局", "home"),
    ("第4局", "away"),
    ("第5局", "home"),
    ("加时", "away"),
]

# 地图显示顺序：按全季出场次数从多到少，页面列固定
MAP_ORDER = ["永眠镇", "湖景村", "月亮河公园", "不归林", "红教堂",
             "唐人街", "里奥的回忆", "军工厂", "圣心医院"]


def _matches(halves):
    """按 seq 回退切分场次。seq 每场都从 1 重新开始。"""
    out, cur = [], []
    for x in halves:
        if cur and x["seq"] <= cur[-1]["seq"]:
            out.append(cur)
            cur = []
        cur.append(x)
    if cur:
        out.append(cur)
    return out


def _slot_of(x):
    # 加时局的 game 记的是真实局号，但显示上要标出「加时」
    if x.get("tiebreak"):
        return "加时"
    return "第%d局" % x.get("game", 0)


def _load():
    """读 bp_core.json，返回 (halves, matches)。

    matches 一场一项 {home, away, teams}，home 是主队、away 是客队，
    按比赛先后排列，和 _matches(halves) 切出来的段一一对应。
    """
    d = json.loads(CORE.read_text(encoding="utf-8"))
    return d.get("halves", []), d.get("matches") or []


def match_homes() -> list:
    """每场的主队，按比赛先后排列。

    直接取 bp_core.json 顶层 matches 的 home 字段 —— 主客是逐场记的，
    不再按队名对查表（同一对组合一季可能交手两次，会撞）。
    """
    return [x.get("home") or "" for x in _load()[1]]


def stats() -> dict:
    """整季的选图偏好数据，按队组织。

    返回：
        teams   队名（字典序）
        maps    地图列顺序
        rows    {队名: [{slot, owner, has_right, counts{图: 次数}, total}]}
        totals  {队名: {图: 次数}}
        seats   {队名: {"home": n, "away": n}}
    """
    halves, matches = _load()
    teams = set()
    rows = {}
    totals = {}
    seats = {}

    for mi, m in enumerate(_matches(halves)):
        a = m[0]["survivor_team"]
        b = m[0]["hunter_team"]
        # 主客按场次顺序从 matches 取，不再用队名对查表
        home = matches[mi].get("home") if mi < len(matches) else ""
        if home not in (a, b):
            continue
        away = b if home == a else a

        # 每局只取上半局：一局上下半局地图相同
        per_slot = {}
        for x in m:
            if x.get("side") != "upper":
                continue
            per_slot[_slot_of(x)] = x.get("map")

        for team in (home, away):
            teams.add(team)
            seats.setdefault(team, {"home": 0, "away": 0})
            seats[team]["home" if team == home else "away"] += 1
            cur = rows.setdefault(team, {s: {"counts": {}, "total": 0}
                                         for s, _ in SLOTS})
            tot = totals.setdefault(team, {})
            for slot, owner in SLOTS:
                mp = per_slot.get(slot)
                # 没打到这一局（比如 2:0 结束），这行不算他「有图权」
                if not mp:
                    continue
                # 该局的图权是不是在这支队手上
                has = (owner == "home" and team == home) or \
                      (owner == "away" and team == away)
                if not has:
                    continue
                cur[slot]["total"] += 1
                cur[slot]["counts"][mp] = cur[slot]["counts"].get(mp, 0) + 1
                tot[mp] = tot.get(mp, 0) + 1

    # 本季没打到第 4、5 局（BO3），空行只会干扰阅读，直接去掉
    used = [s for s, _ in SLOTS
            if any(v["total"] for slots in rows.values() for v in [slots[s]])]

    out_rows = {}
    for team, slots in rows.items():
        lst = []
        for slot, owner in SLOTS:
            if slot not in used:
                continue
            d = slots[slot]
            lst.append({
                "slot": slot,
                "owner": owner,
                "has_right": d["total"] > 0,
                "counts": d["counts"],
                "total": d["total"],
            })
        out_rows[team] = lst

    return {
        "teams": _teams_all(sorted(teams)),
        "maps": MAP_ORDER,
        "rows": out_rows,
        "totals": totals,
        "seats": seats,
    }


def _teams_all(data_teams: list) -> list:
    """比赛里出现过的队伍 ∪ 登记过的（roster.json），合并去重。"""
    try:
        import roster_store
        extra = roster_store.load()["teams"]
    except Exception:
        extra = []
    return sorted(set(data_teams) | set(extra))


def one(team: str) -> dict:
    """单队视图。不认识队名时抛 KeyError。"""
    s = stats()
    if team not in s["rows"]:
        raise KeyError(f"未知队伍：{team}")
    return {
        "team": team,
        "teams": s["teams"],
        "maps": s["maps"],
        "rows": s["rows"][team],
        "totals": s["totals"].get(team, {}),
        "seats": s["seats"].get(team, {}),
    }


def main():
    s = stats()
    for team in s["teams"]:
        d = one(team)
        print(f"\n=== {team}  主 {d['seats']['home']} 客 {d['seats']['away']}")
        for r in d["rows"]:
            top = sorted(r["counts"].items(), key=lambda x: -x[1])
            if not r["has_right"]:
                print(f"  {r['slot']:<5} 无图权")
            elif top:
                print(f"  {r['slot']:<5} " +
                      "、".join(f"{k}×{v}" for k, v in top))
            else:
                print(f"  {r['slot']:<5} 有图权、但该局未打到")


if __name__ == "__main__":
    main()
