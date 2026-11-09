# -*- coding: utf-8 -*-
"""地图视图的数据表，以及「某个格子由哪些半局组成」的明细查询。

两个用途：
  1. 总榜选了地图时，表格只用这张图的半局算：每格 = 实测平均逃生人数，
     更贴近「在这张图上这套组合到底打成什么样」。
  2. 点开任意一格，列出组成这一格的全部半局。

只读 bp_core.json，算完即返，不落盘、不留缓存。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_HALVES = None


def _halves() -> list:
    global _HALVES
    if _HALVES is None:
        p = ROOT / "data" / "bp_core.json"
        _HALVES = json.loads(p.read_text(encoding="utf-8")).get("halves", [])
    return _HALVES


def _game_no(h: dict) -> int:
    """局次。加时赛的 game 记的是真实局号（4 或 5），跟正常局一起算。"""
    g = h.get("game")
    return int(g) if g else 4


def _mean(vals: list) -> float:
    return round(sum(vals) / len(vals), 4) if vals else 0.0


def table(map_name: str) -> dict:
    """某张图的「求生 × 监管」实测表。

    行列只保留这张图上真正出场过的角色：没出场过的角色在这张图上
    没有任何半局，留着只会是整行整列的空白。

    每格 score 是这些半局的平均逃生人数，n 是样本局数。样本很薄
    （不少格子只有 1 局），所以 n 必须一起给出去，让页面标出来。
    """
    sub = [h for h in _halves() if h.get("map") == map_name]
    if not sub:
        return {"map": map_name, "halves": 0, "survivors": [],
                "hunters": [], "cells": {}}

    surv_n = {}
    hunt_n = {}
    cells = {}
    for h in sub:
        hh = h.get("hunter") or ""
        if not hh:
            continue
        hunt_n[hh] = hunt_n.get(hh, 0) + 1
        esc = int(h.get("escape") or 0)
        for c in (h.get("survivors") or []):
            surv_n[c] = surv_n.get(c, 0) + 1
            cells.setdefault(c, {}).setdefault(hh, []).append(esc)

    out_cells = {}
    for c, row in cells.items():
        out_cells[c] = {}
        for hh, vals in row.items():
            out_cells[c][hh] = {"score": _mean(vals), "n": len(vals)}

    # 出场多的排前面：教练看表时最先要找的是这张图的常客
    def _order(cnt):
        return sorted(cnt.items(), key=lambda x: (-x[1], x[0]))

    return {
        "map": map_name,
        "halves": len(sub),
        "escape": _mean([int(h.get("escape") or 0) for h in sub]),
        "survivors": [{"char": c, "n": n} for c, n in _order(surv_n)],
        "hunters": [{"char": h, "n": n} for h, n in _order(hunt_n)],
        "cells": out_cells,
    }


def halves(map_name: str = "", survivor: str = "", hunter: str = "",
           player: str = "") -> dict:
    """符合筛选条件的半局，按赛季时间顺序。

    四个条件都是可选的，同时给就是取交集：
        map_name  地图
        survivor  求生角色（该角色出现在这四个求生位里就算）
        hunter    监管角色
        player    监管选手

    排序用数组原始顺序：bp_core 的 halves 是按比赛时间排的，
    而 seq 字段是每场比赛内部的序号（1~8 循环），不能拿来排序。
    """
    out = []
    for i, h in enumerate(_halves()):
        if map_name and h.get("map") != map_name:
            continue
        if survivor and survivor not in (h.get("survivors") or []):
            continue
        if hunter and h.get("hunter") != hunter:
            continue
        if player and h.get("hunter_player") != player:
            continue
        out.append({
            "order": i,
            "seq": h.get("seq") or 0,
            "game": _game_no(h),
            "tiebreak": bool(h.get("tiebreak")),
            "map": h.get("map") or "",
            "hunter_player": h.get("hunter_player") or "",
            "hunter": h.get("hunter") or "",
            "hunter_team": h.get("hunter_team") or "",
            "survivor_team": h.get("survivor_team") or "",
            "survivors": list(h.get("survivors") or []),
            "bans_survivor": list(h.get("hunter_bans") or []),
            "bans_hunter": list(h.get("survivor_bans") or []),
            "escape": int(h.get("escape") or 0),
            "score": h.get("score") or "",
        })
    out.sort(key=lambda r: r["order"])
    for r in out:
        r.pop("order", None)
    return {"count": len(out), "halves": out}
