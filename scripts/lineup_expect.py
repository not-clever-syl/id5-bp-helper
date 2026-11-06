# -*- coding: utf-8 -*-
"""阵容 × 监管选手 → 每个监管角色的期望得分。

════════════════════════════════════════════════════════════════
口径（与引擎完全一致，不另立一套）
════════════════════════════════════════════════════════════════
对给定阵容 L 与监管角色 H：
    基础分 = L 里「局均逃脱最低的 3 人」的均值
             （4 人时等于剔掉最强的那个；不足 4 人按 4 人归一，缺口记 0）
    结构项 = 救人位 0 个 -0.45、≥2 个每多 1 个 -1.20、无牵制型 -0.30
    最终分 = 基础分 - 结构项

结构项与 H 无关，所以各监管之间只差基础分那一块。
分数单位是「局均逃脱人数」，2.00 是居中值。

════════════════════════════════════════════════════════════════
两种信息一起给
════════════════════════════════════════════════════════════════
per_hunter   逐监管：这套阵容面对每个监管角色各自的得分
per_player   选手加权：该选手实际会选的监管，按威胁前 3、用使用次数加权，
             得出「这个选手大概率把这套阵容打成多少」
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bp_search_engine import (  # noqa: E402
    Engine, SOLID, PENALTY_NO_SOLID, PENALTY_MULTI_SOLID, PENALTY_NO_KITE,
)


def structure_penalty(eng: Engine, chars) -> dict:
    """阵容的结构扣分（与 lineup_score 的扣分部分逐项对应）。"""
    solid = [c for c in chars if c in SOLID]
    kite = [c for c in chars
            if c not in SOLID and "牵制型" in (eng.positions.get(c) or [])]
    pen = 0.0
    items = []
    if not solid:
        pen += PENALTY_NO_SOLID
        items.append({"kind": "no_solid", "desc": "没有救人位", "deduct": PENALTY_NO_SOLID})
    elif len(solid) >= 2:
        d = PENALTY_MULTI_SOLID * (len(solid) - 1)
        pen += d
        items.append({"kind": "multi_solid", "desc": f"救人位 {len(solid)} 个",
                      "deduct": round(d, 4)})
    if not kite:
        pen += PENALTY_NO_KITE
        items.append({"kind": "no_kite", "desc": "没有牵制型", "deduct": PENALTY_NO_KITE})
    return {"total": round(pen, 4), "items": items,
            "solid": solid, "kite": kite}


def lineup_vs_hunter(eng: Engine, chars, hunter: str) -> dict:
    """阵容面对单个监管角色的得分，并列出每个人各自的分数。

    取值口径（2026-11-06 定）：吃选定监管选手的折合分表
    （data/hunter/<选手>.json，evaluate() 载入后由 eng.cell()
    第一优先级返回）—— 这张表回答的是「这套阵容面对这位选手
    会怎样」。表里没有的格子由 cell 兜底回退 global/prior。
    """
    cells = []
    for c in chars:
        s = eng.cell("", c, hunter)
        cells.append({"char": c, "score": round(s, 4), "solid": c in SOLID})
    vals = sorted(c["score"] for c in cells)
    n = len(vals)
    if n >= 4:
        base = sum(vals[:3]) * 0.3 + vals[3] * 0.1   # 最低3×0.3 + 最高×0.1
    elif n == 0:
        base = eng.prior
    else:
        base = sum(vals) * 0.3        # 不足 4 人按 0.3 权重计，缺口记 0
    pen = structure_penalty(eng, chars)["total"]
    return {
        "hunter": hunter,
        "base": round(base, 4),
        "score": round(base - pen, 4) if n else round(base, 4),
        "crew": cells,
    }


def evaluate(eng: Engine, chars, player: str,
             hunters: list | None = None, map_name: str | None = None) -> dict:
    """主入口。player 必选：整张表吃该选手的折合分。

    hunters 不给就只算该选手用过的监管。

    口径（2026-11-06 定）：载入选手折合分表
    （data/hunter/<选手>.json → scores_override），所有格子吃
    该选手的实测折合分，不做无选手的全局评估。

    map_name：本局地图。它不改任何格子的分数，只回答两个问题：
      · 这位选手在这张图上更可能拿哪几个监管（player.top3 的权重）
      · 哪些监管在这张图上基本不会出现（per_hunter 里标 mult）
    逐监管的 per_hunter 表本身不受影响 —— 「这套阵容面对某个监管
    能打成多少」是阵容与监管的客观关系，跟地图无关。
    """
    chars = [c for c in chars if c]
    struct = structure_penalty(eng, chars)

    # 载入选手折合分表。表里没有的格子自然回退到 global（cell 兜底）。
    eng.load_player_scores(player)

    if hunters is None:
        hunters = [t["char"] for t in eng.players[player]["threats"]]

    rows = [lineup_vs_hunter(eng, chars, h) for h in hunters]
    # 分数越低说明这个监管越克制这套阵容，教练视角按「最危险」排前面
    rows.sort(key=lambda r: r["score"])
    for r in rows:
        if map_name:
            r["mult"] = round(eng.map_mult(map_name, r["hunter"]), 4)

    out = {
        "lineup": chars,
        "map": map_name or "",
        "structure": struct,
        "per_hunter": rows,
        "summary": {
            "worst": rows[0]["hunter"] if rows else None,
            "worst_score": rows[0]["score"] if rows else None,
            "best": rows[-1]["hunter"] if rows else None,
            "best_score": rows[-1]["score"] if rows else None,
            "avg": round(sum(r["score"] for r in rows) / len(rows), 4) if rows else None,
        },
    }

    # 选手加权：该选手实际会选的监管，按威胁前 3、用使用次数加权。
    # 给了地图就按「威胁 × 地图倍数」重排再取前 3，与引擎叶子口径一致。
    r = eng.expected_vs_player("", chars, player, map_name=map_name)
    out["player"] = {
        "name": player,
        "expect": round(r["expect"], 4),
        "top3": [{"char": t["char"], "w": t["w"],
                  "w_map": t.get("w_map"),
                  "mult": t.get("mult", 1.0),
                  "score": round(t["score"], 4)} for t in r["top3"]],
    }

    # 地图上的职能倾向：只做提示，不进分数
    # （实测乘进排序只带来 +0.5% 的 top8 提升，已被角色级地图份额吸收）
    if map_name:
        out["role_mix"] = eng.map_surv_role_mix(map_name)
    return out
