# -*- coding: utf-8 -*-
"""BP 查询视图：求生战队 × 求生角色 × 监管角色 的折合得分。

回答的实际问题：
    我方(求生战队 T) 打算选求生角色 s，
    对方可能拿出监管角色 c，
    我们大概能拿几分？

════════════════════════════════════════════════════════════════
为何需要「全局 + 队伍收缩」
════════════════════════════════════════════════════════════════
三维格子 (T, s, c) 中 76% 只有 1 场，直接取均值必然过拟合。
因此用贝叶斯收缩：

    score(T,s,c) = w · own(T,s,c) + (1-w) · global(s,c)

    own    该队在该组合上的实测均值（经选手熟练度修正）
    global 全联盟同组合的均值（同样经修正）
    w      可信度权重 = n / (n + K)

    n 为该队在 (s,c) 上的样本量
    K 为收缩强度，取 K=3（与选手效应的 λ 一致）

这样：
    n=1 时 w=0.25，主要参考全局
    n=3 时 w=0.50，两者各半
    n=6 时 w=0.67，主要看本队

════════════════════════════════════════════════════════════════
选手熟练度修正（来自 adjusted_scores.json）
════════════════════════════════════════════════════════════════
每个半局的得分先加上 adjust(p,c)：
    对方选手用这个角色打得比行业标准差 → 我方得分被低估 → 上调
    对方选手用这个角色打得比行业标准好 → 我方得分被高估 → 下调

对策卡（<20 场）的 adjust 恒为 0，不参与估计。
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
K_SHRINK = 3.0


def build(core=None, adj_data=None):
    """返回查询视图字典；core / adj_data 不给就读磁盘。"""
    if core is None:
        core = json.loads((ROOT / "data" / "bp_core.json").read_text(encoding="utf-8"))
    if adj_data is None:
        adj_data = json.loads((ROOT / "data" / "adjusted_scores.json").read_text(encoding="utf-8"))

    adj_map = adj_data["player_char_adjust"]
    main_chars = set(adj_data["meta"]["main_chars"])
    counter_chars = set(adj_data["meta"]["counter_pick_chars"])

    rows = []
    for r in core["halves"]:
        p = r.get("hunter_player")
        key = f"{p}@{r['hunter']}"
        adj = adj_map.get(key, {}).get("adjust", 0.0)
        y = r["escape"]           # 逃脱人数口径
        rows.append({
            "team": r["survivor_team"],
            "opp": r["hunter_team"],
            "cv": r["survivors"],
            "bc": r["hunter"],
            "y": y,
            "adj_y": y + adj,
            "is_counter": r["hunter"] in counter_chars,
        })

    # ---------- 全局 (s, c) ----------
    glob = defaultdict(list)
    for r in rows:
        for s in r["cv"]:
            glob[(s, r["bc"])].append(r["adj_y"])

    GLOBAL = {k: sum(v) / len(v) for k, v in glob.items()}
    GLOBAL_N = {k: len(v) for k, v in glob.items()}

    # ---------- 队伍 (T, s, c) ----------
    own = defaultdict(list)
    for r in rows:
        for s in r["cv"]:
            own[(r["team"], s, r["bc"])].append(r["adj_y"])

    # ---------- 构建查询视图 ----------
    table = {}
    for (t, s, c), vs in own.items():
        n = len(vs)
        own_mean = sum(vs) / n
        g = GLOBAL.get((s, c))
        if g is None:
            final = own_mean
            w = 1.0
        else:
            w = n / (n + K_SHRINK)
            final = w * own_mean + (1 - w) * g
        table.setdefault(t, {}).setdefault(s, {})[c] = {
            "score": round(final, 3),
            "own": round(own_mean, 3),
            "own_n": n,
            "global": round(g, 3) if g is not None else None,
            "global_n": GLOBAL_N.get((s, c)),
            "weight": round(w, 3),
            "counter_pick": c in counter_chars,
        }

    # ---------- 补充：该队没打过、但全局有的组合 ----------
    # 对 BP 很有用：即便没交手过，也能给出参考值
    for t in list(table):
        for s in list(table[t]):
            for (gs, gc), gv in GLOBAL.items():
                if gs == s and gc not in table[t][s]:
                    table[t][s][gc] = {
                        "score": round(gv, 3),
                        "own": None,
                        "own_n": 0,
                        "global": round(gv, 3),
                        "global_n": GLOBAL_N[(gs, gc)],
                        "weight": 0.0,
                        "counter_pick": gc in counter_chars,
                        "from_global_only": True,
                    }

    out = {
        "meta": {
            "description": "求生战队 × 求生角色 × 监管角色 的折合得分（BP 查询视图）",
            "usage": "score 表示该队用该求生角色、面对该监管角色时的预期局均逃脱人数（0~4），2.00 为平局线",
            "k_shrink": K_SHRINK,
            "formula": "score = w*own + (1-w)*global, w = n/(n+K)",
            "main_chars": sorted(main_chars),
            "counter_pick_chars": sorted(counter_chars),
            "note": "own_n=0 表示该队未与该监管角色交手过，score 直接取全局值",
        },
        "scores": table,
    }

    n_cells = sum(len(cs) for t in table.values() for cs in t.values())
    n_own = sum(1 for t in table.values() for cs in t.values()
                for v in cs.values() if v["own_n"] > 0)
    return out, {"teams": len(table), "main_chars": len(main_chars),
                 "counter_chars": len(counter_chars),
                 "cells": n_cells, "own_cells": n_own}


def write(out):
    OUT = ROOT / "data" / "bp_query.json"
    OUT.write_text(json.dumps(json.loads(json.dumps(out, ensure_ascii=False)),
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写入 {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


def main():
    out, stat = build()
    write(out)
    print(f"  战队 {stat['teams']}")
    print(f"  主力角色 {stat['main_chars']}  对策卡 {stat['counter_chars']}")
    print(f"  格子 {stat['cells']}，其中有本队实测的 {stat['own_cells']}")


if __name__ == "__main__":
    main()
