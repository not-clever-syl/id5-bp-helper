# -*- coding: utf-8 -*-
"""ban 位情报：从 bp_core.json 的真实 ban 行为里提取的两个信号。

背景（2026-11-06 的回测结论，脚本 bt1~bt17 / mx1~mx3 / up3~up4）：
    1. 监管 ban 谁与「分数表克制力」几乎无关 —— 真实被 ban 角色在
       克制力榜上的排名均匀分布（中位 15/25，top5 内仅 17.3%）。
       旧版按「克制力之和」生成的候选 ban 对，端到端命中率仅 1.5%。
    2. ban 行为由三个因素驱动：
           热门度  —— 出场率 vs 被ban率相关 0.677，ban 的是常上场的角色
           监管指纹 —— 每个监管有固定习惯（跛脚羊首ban古董商 50% 等）
           功能位  —— 牵制 41% / 辅助 35%，ban 的是阵容核心
    3. 「对手前两个 ban 位」预测改用：
           score(pair, H) = 2.0×指纹次数 + 1.0×单角色PMI + 0.05×热门度
       rolling 无后视回测（top8 候选命中真实 ban 对）：
           旧口径 1.5% → 新口径 ~25.5%（随机基线 17.8%）
    4. 「互斥对」= 某求生面对某监管时出场率显著低于赛季平均。
       只做行为统计提示（报告的对位情报卡），不进分数表 ——
       回测（up4）证实把互斥信号加进分数预测 MAE 不降（−0.0009，噪声级）。

本模块只读 bp_core.json，与引擎解耦；统计进程内缓存。
"""
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_cache = None


def stats():
    """全局 ban / 出场统计。首次调用加载 bp_core.json，之后走缓存。

    返回 dict：
        halves_n      半局总数
        play          Counter：求生角色赛季出场次数（全联盟）
        g_single      Counter：各求生被监管方 ban 的总次数
        g_ban_total   监管方 ban 次数总和
        hunter_halves Counter：各监管角色出场半局数
        ban_by        {监管: Counter(被ban求生)}（全部 4 个 ban 位）
        pair_fp       {监管: Counter((ban1, ban2) 排序对)} —— 前 2 个 ban 位
        pair_play     {(求生, 监管): 出场次数}
        pair_ban      {(求生, 监管): 被ban次数}
        pair_esc      {(求生, 监管): [escape, ...]}
    """
    global _cache
    if _cache is not None:
        return _cache
    core = json.loads((ROOT / "data" / "bp_core.json").read_text(encoding="utf-8"))
    halves = core.get("halves") or []
    S = {
        "halves_n": len(halves),
        "play": Counter(),
        "g_single": Counter(),
        "g_ban_total": 0,
        "hunter_halves": Counter(),
        "ban_by": defaultdict(Counter),
        "pair_fp": defaultdict(Counter),
        "pair_play": Counter(),
        "pair_ban": Counter(),
        "pair_esc": defaultdict(list),
    }
    for h in halves:
        hh = h.get("hunter") or ""
        S["hunter_halves"][hh] += 1
        hb = h.get("hunter_bans") or []
        S["g_ban_total"] += len(hb)
        for c in hb:
            S["g_single"][c] += 1
            S["ban_by"][hh][c] += 1
            S["pair_ban"][(c, hh)] += 1
        if len(hb) >= 2:
            S["pair_fp"][hh][tuple(sorted(hb[:2]))] += 1
        for c in (h.get("survivors") or []):
            S["play"][c] += 1
            S["pair_play"][(c, hh)] += 1
            S["pair_esc"][(c, hh)].append(h.get("escape"))
    _cache = S
    return S


def pmi(pair, hunter, S=None):
    """该监管比全联盟更爱 ban 这两个角色的程度（对数比之和）。

    Dirichlet 平滑（α=0.5，词表规模按 30 算 → 加 15），
    与回测脚本 bt15 的口径一致。
    没有该监管的 ban 记录时返回强负值，让排序自然靠后。
    """
    S = S or stats()
    bb = S["ban_by"].get(hunter)
    if not bb:
        return -3.0 * len(pair)
    n = max(sum(bb.values()), 1)
    g_tot = max(S["g_ban_total"], 1)
    sc = 0.0
    for c in pair:
        p_ch = (bb.get(c, 0) + 0.5) / (n + 15.0)
        p_c = (S["g_single"].get(c, 0) + 0.5) / (g_tot + 15.0)
        sc += math.log(p_ch / p_c)
    return sc


def pair_score(pair, hunter, S=None):
    """单监管视角的 ban 对打分（回测最优配比：指纹 2 / PMI 1 / 热门 0.05）。"""
    S = S or stats()
    fp = S["pair_fp"].get(hunter, {}).get(tuple(sorted(pair)), 0)
    return (2.0 * fp
            + 1.0 * pmi(pair, hunter, S)
            + 0.05 * (S["play"].get(pair[0], 0) + S["play"].get(pair[1], 0)))


def matchup_alerts(eng, team, player, gone=None,
                   min_hunter_halves=10, min_char_plays=20, top=8):
    """互斥对情报：某求生面对某监管时出场率显著低于其赛季平均。

    判定（与回测脚本 mx2/mx3 一致）：
        - 该监管出场 ≥10 半局、该求生赛季出场 ≥20 次（样本太少不算）
        - 单侧 z 检验 z < −1.645（p<0.05）：对 H 出场率显著低于总出场率
    归因（区分「对手怕它」还是「我方不敢选」）：
        demand = 实际出场 + 被ban，fill = demand / 期望出场
        fill ≥ 0.70 → 被ban拦截：对手主动 ban 掉（对手怕它）
        fill ≤ 0.35 → 求生回避：连 ban 都没人 ban，我方自己不选
        其余 → 混合
    gone：本场已用掉的角色（全局禁选），两边都不再提示。
    """
    S = stats()
    gone = set(gone or ())
    dist = (eng.players.get(player) or {}).get("dist") or {}
    cand_h = [h for h, p in dist.items() if p > 0 and h not in gone]
    pool = [c for c in eng.pool_of(team) if c not in gone]
    N = max(S["halves_n"], 1)

    rows = []
    for hh in cand_h:
        nh = S["hunter_halves"].get(hh, 0)
        if nh < min_hunter_halves:
            continue
        for X in pool:
            plays = S["play"].get(X, 0)
            if plays < min_char_plays:
                continue
            rate_all = plays / N
            k = S["pair_play"].get((X, hh), 0)
            p1 = k / nh
            se = (rate_all * (1 - rate_all) / nh) ** 0.5
            if se <= 0:
                continue
            z = (p1 - rate_all) / se
            if z >= -1.645:
                continue
            banned = S["pair_ban"].get((X, hh), 0)
            expect = rate_all * nh
            fill = (k + banned) / expect if expect else 1.0
            esc_list = S["pair_esc"].get((X, hh)) or []
            esc = sum(esc_list) / len(esc_list) if esc_list else None
            if fill >= 0.70:
                kind = "对手会ban"
                note = (f"{hh} 的 {nh} 局里 {X} 被ban {banned} 次、只出场 {k} 次"
                        f"（出场+被ban ≈ 期望的 {fill:.0%}）—— 多半是对手主动ban掉")
            elif fill <= 0.35:
                kind = "我方回避"
                note = (f"{hh} 的 {nh} 局里 {X} 只出场 {k} 次、没被ban过"
                        f"（合计仅期望的 {fill:.0%}）—— 我方一直没选它")
            else:
                kind = "混合"
                note = (f"{hh} 的 {nh} 局里 {X} 出场 {k} 次、被ban {banned} 次"
                        f"（合计 ≈ 期望的 {fill:.0%}）")
            rows.append({
                "surv": X, "hunter": hh, "kind": kind,
                "z": round(z, 2), "play": k, "banned": banned,
                "expect": round(expect, 1), "fill": round(fill, 2),
                "esc": round(esc, 2) if esc is not None else None,
                "esc_n": len(esc_list),
                "note": note,
            })
    # z 越负越显著；同分时被ban多的（对手主动拦截）排前
    rows.sort(key=lambda r: (r["z"], -r["banned"]))
    return rows[:top]
