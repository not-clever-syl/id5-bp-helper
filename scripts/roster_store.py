# -*- coding: utf-8 -*-
"""自定义队伍与监管选手名单。

来源：原始数据页的「队伍 / 选手」管理入口写进 data/roster.json。
bp_core.json 里出现过的队伍/选手始终有效（那是比赛事实），
这里登记的是「还没打过比赛」的新队伍 / 新选手，以及选手→队伍的归属。

两处依赖这里：
    * serve.py   /api/core 的 teams（数据提取 ∪ 登记队伍）、
                 /api/roster_edit 的读写
    * core_check.py  选手→队伍映射（roster 优先，hunter_players 兜底）

结构：
    {"teams": ["ACT", ...], "players": [{"name": "Jin", "team": "GR"}]}
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "data" / "roster.json"


def load() -> dict:
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    teams = d.get("teams")
    if not isinstance(teams, list):
        teams = []
    players = d.get("players")
    if not isinstance(players, list):
        players = []
    return {
        "teams": [t for t in teams if isinstance(t, str) and t.strip()],
        "players": [{"name": p.get("name") or "", "team": p.get("team") or ""}
                    for p in players if isinstance(p, dict) and p.get("name")],
    }


def save(data: dict) -> None:
    clean = load()
    clean["teams"] = data.get("teams", clean["teams"])
    clean["players"] = data.get("players", clean["players"])
    PATH.write_text(json.dumps(clean, ensure_ascii=False, indent=1),
                    encoding="utf-8")


def used_names(halves: list) -> dict:
    """bp_core 里实际用到的队伍名和选手名（改名/删除前查引用）。"""
    teams = set()
    players = set()
    for h in halves:
        if not isinstance(h, dict):
            continue
        for k in ("survivor_team", "hunter_team"):
            if h.get(k):
                teams.add(h[k])
        if h.get("hunter_player"):
            players.add(h["hunter_player"])
    return {"teams": teams, "players": players}


def rename(halves: list, kind: str, old: str, new: str) -> list:
    """把 halves 里所有 old 替换成 new（就地改）。kind 是 team / player。"""
    keys = {"team": ("survivor_team", "hunter_team"),
            "player": ("hunter_player",)}[kind]
    n = 0
    for h in halves:
        if not isinstance(h, dict):
            continue
        for k in keys:
            if h.get(k) == old:
                h[k] = new
                n += 1
    return halves
