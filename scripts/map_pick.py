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
该场他是不是主队，否则主客队的选图会混在一起。这里用 HOME_PAIRS
记下每场的主队，再按上表判断这一局的图权归谁。

地图不可重复选择，但可以重复禁用；本页只统计「选择」，不含禁图。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "data" / "bp_core.json"

# 每场的主队（第一局握有图权的队伍）。键是先排序后的两队名。
# 赛季内同一对组合只交手一次，所以用队名对做键是唯一的。
HOME_PAIRS = {
    ("ACT", "ASC"): "ASC",
    ("ACT", "DOU5"): "ACT",
    ("ACT", "FPX.ZQ"): "FPX.ZQ",
    ("ACT", "GR"): "GR",
    ("ACT", "MRC"): "ACT",
    ("ACT", "TE"): "ACT",
    ("ACT", "北京WBG"): "北京WBG",
    ("ACT", "成都GG"): "成都GG",
    ("ACT", "成都Wolves"): "成都Wolves",
    ("ASC", "DOU5"): "DOU5",
    ("ASC", "FPX.ZQ"): "ASC",
    ("ASC", "GR"): "ASC",
    ("ASC", "MRC"): "ASC",
    ("ASC", "TE"): "TE",
    ("ASC", "北京WBG"): "北京WBG",
    ("ASC", "成都GG"): "ASC",
    ("ASC", "成都Wolves"): "成都Wolves",
    ("DOU5", "FPX.ZQ"): "DOU5",
    ("DOU5", "GR"): "GR",
    ("DOU5", "MRC"): "DOU5",
    ("DOU5", "TE"): "TE",
    ("DOU5", "北京WBG"): "北京WBG",
    ("DOU5", "成都GG"): "成都GG",
    ("DOU5", "成都Wolves"): "DOU5",
    ("FPX.ZQ", "GR"): "GR",
    ("FPX.ZQ", "MRC"): "MRC",
    ("FPX.ZQ", "TE"): "TE",
    ("FPX.ZQ", "北京WBG"): "FPX.ZQ",
    ("FPX.ZQ", "成都GG"): "FPX.ZQ",
    ("FPX.ZQ", "成都Wolves"): "FPX.ZQ",
    ("GR", "MRC"): "GR",
    ("GR", "TE"): "TE",
    ("GR", "北京WBG"): "北京WBG",
    ("GR", "成都GG"): "成都GG",
    ("GR", "成都Wolves"): "成都Wolves",
    ("MRC", "北京WBG"): "北京WBG",
    ("MRC", "成都GG"): "成都GG",
    ("MRC", "成都Wolves"): "MRC",
    ("TE", "北京WBG"): "TE",
    ("TE", "成都GG"): "TE",
    ("TE", "成都Wolves"): "TE",
    ("北京WBG", "成都GG"): "成都GG",
    ("北京WBG", "成都Wolves"): "成都Wolves",
    ("成都GG", "成都Wolves"): "成都GG",
}

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
    return "加时" if x.get("tiebreak") else "第%d局" % x.get("game", 0)


def _load():
    return json.loads(CORE.read_text(encoding="utf-8")).get("halves", [])


def stats() -> dict:
    """整季的选图偏好数据，按队组织。

    返回：
        teams   队名（字典序）
        maps    地图列顺序
        rows    {队名: [{slot, owner, has_right, counts{图: 次数}, total}]}
        totals  {队名: {图: 次数}}
        seats   {队名: {"home": n, "away": n}}
    """
    halves = _load()
    teams = set()
    rows = {}
    totals = {}
    seats = {}

    for m in _matches(halves):
        pair = {m[0]["survivor_team"], m[0]["hunter_team"]}
        key = tuple(sorted(pair))
        home = HOME_PAIRS.get(key)
        if not home:
            continue
        away = next(t for t in key if t != home)

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
        "teams": sorted(teams),
        "maps": MAP_ORDER,
        "rows": out_rows,
        "totals": totals,
        "seats": seats,
    }


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
