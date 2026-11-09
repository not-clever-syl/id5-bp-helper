# -*- coding: utf-8 -*-
"""由 bp_core 的派生数据产出「可直接导入的配置」到两个配置目录。

    data/hunter/<监管选手id>_<数字>.json      {"cells": {求生: {监管: {score, n}}}}
    data/survivor/<队伍名>_<数字>.json         {"teams": {队伍: [求生, ...]}}

这两份是页面「导入配置」直接吃的格式，导入即用。
它们全部来自 bp_core 的派生结果，所以重建时跟着一起刷新。

跑法：python scripts/build/presets.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

DATA = ROOT / "data"
DIR_HUNTER = DATA / "hunter"
DIR_SURVIVOR = DATA / "survivor"

SEASON = "IVL 2026 秋季赛"


def _read(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def _write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def build_hunter_configs(base=None, ps=None, tree=None) -> dict:
    """每位监管选手一份：基准表叠上该选手的实测折合分。"""
    import custom_data

    if base is None:
        base = _read("bp_query.json")["scores"]
    if ps is None:
        ps = _read("player_scores.json")
    if tree is None:
        tree = _read("bp_tree.json")

    players = ps.get("players") or {}
    p_hunters = ps.get("player_hunters") or {}

    # 显示范围 = 各队角色池的并集 × 选手实际用过的监管。
    survivors = list(custom_data.all_survivors(tree))
    hunters = list(custom_data.all_hunters(tree))

    def base_row(ch):
        """基准表的一行：{监管: {score, n}}（取第一个队的 global 值）。"""
        row = {}
        for chars in base.values():
            if ch not in chars:
                continue
            for h, v in chars[ch].items():
                sc = v.get("global")
                if sc is None:
                    sc = v.get("score")
                if sc is None:
                    continue
                row[h] = {"score": round(float(sc), 2),
                          "n": int(v.get("global_n", 0) or 0)}
            break
        return row

    out = {}

    # 每位监管选手：基准表 + 该选手的实测折合分。
    # 不产出「基准表」那份 —— 对手必须由具体选手的配置决定，
    # 没有选手就没法生成报告。
    for name in sorted(players):
        used = set(p_hunters.get(name) or [])
        if not used:
            continue
        own = players[name]
        row_cells = {ch: dict(base_row(ch)) for ch in survivors if base_row(ch)}
        for ch, hs in own.items():
            if ch not in row_cells:
                continue
            for h, v in hs.items():
                if h not in used:
                    continue
                sc = v.get("score") if isinstance(v, dict) else v
                if isinstance(sc, (int, float)):
                    row_cells[ch][h] = {
                        "score": round(float(sc), 2),
                        "n": int((v.get("own_n") or v.get("n") or 0)
                                 if isinstance(v, dict) else 0),
                    }
        out[name] = {"season": SEASON, "kind": "player",
                     "player": name, "hunters": sorted(used),
                     "cells": row_cells}
    return out


def build_survivor_configs(tree=None) -> dict:
    """每支队伍一份角色池。"""
    if tree is None:
        tree = _read("bp_tree.json")
    out = {}
    for team in sorted(tree["teams"]):
        roster = list(tree["teams"][team].get("roster") or [])
        if len(roster) < 4:
            continue
        out[team] = {"season": SEASON, "kind": "team",
                     "teams": {team: roster}}
    return out


def write(hunters=None, survivors=None) -> dict:
    """落盘，返回 {目录: 文件数}。"""
    hunters = hunters if hunters is not None else build_hunter_configs()
    survivors = survivors if survivors is not None else build_survivor_configs()
    for name, obj in hunters.items():
        _write(DIR_HUNTER / f"{name}_1.json", obj)
    for team, obj in survivors.items():
        _write(DIR_SURVIVOR / f"{team}_1.json", obj)
    return {"hunter": len(hunters), "survivor": len(survivors)}


def main():
    n = write()
    print(f"监管数据: {n['hunter']} 个 → data/hunter/")
    print(f"求生角色池: {n['survivor']} 个 → data/survivor/")


if __name__ == "__main__":
    main()
