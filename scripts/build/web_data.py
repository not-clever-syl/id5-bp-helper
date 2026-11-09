# -*- coding: utf-8 -*-
"""导出 web 端动态 BP 计算所需的全部数据。

════════════════════════════════════════════════════════════════
输出
════════════════════════════════════════════════════════════════
data/web_bp.json

    meta          赛季、规则版本、阶段定义、结构惩罚常量
    roles         每个求生角色的定位 / 是否救人位 / 硬辅 / 软辅
    survivorPool  求生方出战角色池（可编辑，与战队无关）
    teams         各战队 roster（仅备查，不参与 BP）
    players       各监管选手的威胁分布（前 8）与强度
    cells[team][char][hunter]  局均逃脱分（含 global 兜底）
    banSurv       求生角色被 ban 率
    banHunt       监管被 ban 率
    prior         全局兜底分

前端拿到这份数据后，可以在浏览器里原地重算：
    - lineup_score(阵容, 监管) = 最低 3 人均分 + 结构惩罚
    - expected_vs_player(阵容, 选手) = 威胁前 3 监管的加权期望
不需要跑 α-β 搜索，也不需要重新请求后端。

════════════════════════════════════════════════════════════════
用法
════════════════════════════════════════════════════════════════
    python scripts/export_web_data.py
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bp_search_engine import (  # noqa: E402
    Engine, ROOT, SOLID, HARD_SUPPORT, MID_SUPPORT, SOFT_SUPPORT,
    PENALTY_NO_SOLID, PENALTY_MULTI_SOLID, PENALTY_NO_KITE,
    PENALTY_NO_SUPPORT, SHRINK_K,
)

OUT = ROOT / "data" / "web_bp.json"


def load_support_cls():
    """读取静态的辅助分类。

    辅助分类（硬/中/软辅）不随比赛结果变化，是事先定好的角色属性。
    来源优先级：
        data/support_cls.json   页面上保存过的分类
        现有 web_bp.json         上一次构建留下的分类
        引擎默认值               都没有时的兜底
    """
    p = ROOT / "data" / "support_cls.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        return (list(d.get("hard", [])), list(d.get("mid", [])),
                list(d.get("soft", [])))
    if OUT.exists():
        try:
            d = json.loads(OUT.read_text(encoding="utf-8"))
            cls = (d.get("meta") or {}).get("default_support") or {}
            if cls:
                return (list(cls.get("hard", [])), list(cls.get("mid", [])),
                        list(cls.get("soft", [])))
        except Exception:
            pass
    return sorted(HARD_SUPPORT), sorted(MID_SUPPORT), sorted(SOFT_SUPPORT)


def main():
    eng = Engine()
    hard_list, mid_list, soft_list = load_support_cls()

    # ── 角色定位 ──
    roles = {}
    all_chars = set(eng.positions)
    for t in eng.teams:
        all_chars |= set(eng.teams[t]["roster"])
    for c in sorted(all_chars):
        pos = eng.positions.get(c) or []
        # 分类以 support_cls.json（用户保存的）为准
        roles[c] = {
            "pos": pos,
            "solid": c in SOLID,
            "hard": c in hard_list,
            "mid": c in mid_list,
            "soft": c in soft_list,
        }

    # ── 分数表 ──
    # 前端要做与引擎一致的样本量收缩，所以必须导出样本数，
    # 只给一个裸分数是不够的（无法判断这个分数可不可信）。
    cells = {}
    for team, chars in eng.sc.items():
        cells[team] = {}
        for ch, hunters in chars.items():
            cells[team][ch] = {
                h: {"score": round(v["score"], 4),
                    "n": v.get("own_n", 0) or v.get("global_n", 0)}
                for h, v in hunters.items()
            }

    # 全联盟参考值：每个 (角色, 监管) 的全联盟均值，供收缩目标用
    global_ref = {}
    for chars in eng.sc.values():
        for ch, hunters in chars.items():
            for h, v in hunters.items():
                global_ref.setdefault(ch, {}).setdefault(h, []).append(v["global"])
    global_mean = {
        ch: {h: round(sum(vs) / len(vs), 4) for h, vs in hs.items()}
        for ch, hs in global_ref.items()
    }

    # ── 选手威胁分布 ──
    players = {}
    for p, info in eng.players.items():
        players[p] = {
            "raw_count": info.get("raw_count", {}),
            "threats": [
                {"char": t["char"], "p_use": round(t["p_use"], 4),
                 "threat": round(t["threat"], 4)}
                for t in info["threats"][:8]
            ],
        }

    # ── 阶段定义 ──
    stages = {str(g): [
        {"code": s[0], "kind": s[1], "count": s[2]} for s in eng._stages(g)
    ] for g in (1, 2, 3, 4, 5)}

    data = {
        "meta": {
            "season": "IVL 2026 秋季赛",
            "rule_version": "第十一版",
            "prior": eng.prior,
            "shrink_k": SHRINK_K,
            "penalty": {
                "no_solid": PENALTY_NO_SOLID,
                "multi_solid": PENALTY_MULTI_SOLID,
                "no_kite": PENALTY_NO_KITE,
                "no_support": PENALTY_NO_SUPPORT,
            },
            "stages": stages,
            "hunter_pool": eng.hunter_pool,
            # 默认分类：优先用页面上保存过的分类
            "default_support": {
                "hard": hard_list,
                "mid": mid_list,
                "soft": soft_list,
            },
        },
        "roles": roles,
        "survivorPool": sorted(set().union(*(set(v) for v in eng.rosters.values()))),
        "rosters": {t: v for t, v in eng.rosters.items()},
        "teams": {t: {"roster": v["roster"]} for t, v in eng.teams.items()},
        "players": players,
        "cells": cells,
        "globalRef": global_mean,
        "banSurv": {k: round(v, 4) for k, v in eng.ban_surv.items()},
        "banHunt": {k: round(v, 4) for k, v in eng.ban_hunt.items()},
        "strength": {k: round(v, 4) for k, v in eng.strength.items()},
    }

    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    print(f"已写入 {OUT}（{OUT.stat().st_size/1024:.0f} KB）")
    print(f"  战队 {len(data['teams'])}  选手 {len(data['players'])}  "
          f"角色 {len(roles)}")
    n_hard = sum(1 for r in roles.values() if r["hard"])
    n_mid = sum(1 for r in roles.values() if r["mid"])
    n_soft = sum(1 for r in roles.values() if r["soft"])
    print(f"  硬辅 {n_hard} 个，中辅 {n_mid} 个，软辅 {n_soft} 个")


if __name__ == "__main__":
    main()
