# -*- coding: utf-8 -*-
"""教练报告（以对手 ban 组合为轴的版本）。

═══════════════════════════════════════════════════════════════
报告页的阅读顺序
═══════════════════════════════════════════════════════════════

    ① 用户选择前两个 ban 位的组合（下拉菜单）
    ② 给出监管方可能考虑的监管前 4
    ③ 给出我方应 ban 哪几个监管
    ④ 给出我方前两手怎么选
    ⑤ 给出后面博弈树怎么选

与旧版（bp_intent_report.py）的区别：

    旧版：以「对手想打哪个监管」为轴 —— 先枚举监管，再反推对手会 ban 什么。
          报告页只能被动接受引擎给的几个方案。

    新版：以「对手前两个 ban 位」为轴 —— 这是教练在 BP 现场真正看到的
          信息（对手 ban 了谁），也是唯一能由用户自由选择的那个维度。
          选定之后，②③④⑤ 全部跟着这一个选择走。

    反推方向也反过来了：给定对手 ban 了哪两个求生者，
    从「被 ban 的角色对哪个监管最关键」反推对手想打哪个监管。
    这正是 bp_coach_report.one_combo() 的结构，所以直接复用它。

═══════════════════════════════════════════════════════════════
产出结构
═══════════════════════════════════════════════════════════════

    {
      "meta":    {...},
      "hunters": [...],          # ② 监管方可能考虑的监管前 N（与选择无关）
      "combos":  [               # ① 下拉的候选，每个含完整的 ②③④⑤
        {
          "bans": ["木偶师", "野人"],       # 对手前两个 ban 位
          "weight": 5.317,                  # 可能性（越大越可能）
          "hunters": ["牙医", "歌剧演员"],  # 这组 ban 最可能指向的监管
          "intent": [...],                  # 反推的监管意图（细节）
          "intent_note": "...",
          "ban_hunter": ["鹿头", "牙医"],    # ③ 我方应 ban 的监管
          "ban_options": [...],
          "ban_note": "...",
          "picks": ["前锋", "祭司"],        # ④ 我方前两手
          "first_options": [...],
          "second_options": [...],
          "line":  [...],                   # ⑤ 博弈树主线
          "steps": [...],                   # ⑤ 各步关键备选
          "lineup": [...],
          "expect": 1.43
        }
      ]
    }
"""
import itertools
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))

from bp_search_engine import Engine, ROOT, ban_quota  # noqa: E402
from bp_coach_report import (  # noqa: E402
    one_combo, infer_hunter_intent,
)
import ban_intel  # noqa: E402

_out = print


def print(*a, **k):  # noqa: A001
    k.setdefault("flush", True)
    _out(*a, **k)


# ═══════════════════════════════════════════════════════════════
# ② 监管方可能考虑的监管前 N（原 bp_intent_report.enumerate_hunters，
#    已内联 —— 该模块其余部分废弃，不再作为依赖保留）
# ═══════════════════════════════════════════════════════════════
def enumerate_hunters(eng, player, n=4, exclude=None, map_name=None):
    """对手可能想打的 n 个监管，按（使用率 × 强度 + 威胁）排序。

    这一步不看求生 ban——因为后面要用 ban 位来反推，
    如果这里先把 bans 算进去就成了循环论证。

    exclude：对方本场之前已经用掉的监管，不可能再拿，直接排除。
    map_name：本局地图。给了就把威胁那一项乘上地图倍数 ——
    「这张图上他更可能拿谁」和「他整体爱用谁」不是一回事。
    """
    gone = set(exclude or ())
    info = eng.players[player]
    rows = []
    for t in info["threats"]:
        h = t["char"]
        if t["p_use"] <= 0 or h in gone:
            continue
        mult = eng.map_mult(map_name, h) if map_name else 1.0
        rows.append({
            "char": h,
            "p_use": t["p_use"],
            "threat": t["threat"],
            "mult": round(mult, 4),
            "strength": eng.strength.get(h, 0.0),
            "score": t["p_use"] * eng.strength.get(h, 0.0) + t["threat"] * 0.5 * mult,
        })
    rows.sort(key=lambda r: -r["score"])
    return rows[:n]


def candidate_bans(eng, team, player, n_hunters=4, bantop=5, top=8,
                    exclude=None, focus=None):
    """枚举对手最可能 ban 的两个求生者，按真实 ban 行为打分。

    打分公式（2026-11-06 回测定稿，脚本 bt11~bt16）：
        score(pair) = max_h( 2.0×指纹(H 历史ban过这对的次数)
                             + 1.0×单角色PMI(H 比别人更爱ban这俩的程度)
                             + 0.05×两角色赛季出场次数和 )

    为什么不用「克制力之和」（旧版逻辑，已证伪）：
        真实被 ban 角色在克制力榜上的排名均匀分布（中位 15/25），
        旧口径的 top8 候选对真实 ban 对的命中率只有 1.5%；
        新口径（指纹+PMI+热门度）同条件回测 ~25.5%，随机基线 17.8%。

    候选池：本队角色池出场率 top10 的两两组合（C(10,2)=45 对）
            ∪ 指纹里出现过的对（该选手的候选监管历史上 ban 过的）
            ∪ focus（用户指定要看的组合，不受 top10 限制）。
    监管指纹与热门度都是赛季全量统计（ban_intel.stats()，每次读盘）。

    返回 [{"bans": [a, b], "weight": w, "hunters": [...], "rank": i}]。
    """
    S = ban_intel.stats()
    gone = set(exclude or ())
    roster = [c for c in eng.pool_of(team) if c not in gone]
    if len(roster) < 2:
        return []

    # 监管意图留给下一步反推（infer_hunter_intent），这里只需要
    # 「该选手可能拿的监管」来算指纹和 PMI。
    dist = (eng.players.get(player) or {}).get("dist") or {}
    cand_h = [h for h, p in dist.items() if p > 0 and h not in gone]

    # 候选对池：出场率 top10 的两两组合 ∪ 指纹里出现过的对
    hot = sorted(roster, key=lambda c: -S["play"].get(c, 0))[:10]
    pairs = {tuple(sorted(p)) for p in itertools.combinations(hot, 2)}
    for hh in cand_h:
        for pair in (S["pair_fp"].get(hh) or {}):
            if pair[0] in roster and pair[1] in roster:
                pairs.add(pair)
    # 用户点名的组合：只要不是「已被用掉」的，一律进候选池，
    # 哪怕不在出场率 top10 里 —— 否则搜了也搜不到。
    for pair in (focus or ()):
        if len(pair) == 2 and pair[0] != pair[1] and all(c in roster for c in pair):
            pairs.add(tuple(sorted(pair)))
    if not pairs:
        return []

    TOP_H = 3          # 排序用前 3（报告里只标这几个）
    merged = {}
    for pair in pairs:
        all_scored = sorted(((ban_intel.pair_score(pair, hh, S), hh) for hh in cand_h),
                           reverse=True)
        all_scored = [(round(w, 4), hh) for w, hh in all_scored if w > -50]
        if not all_scored:
            continue
        scored = all_scored[:TOP_H]
        merged[pair] = {
            "bans": list(pair),
            # weight = 指向最强的那一个的分数（排序用）
            "weight": scored[0][0],
            # 前三个指向监管 + 各自的可能性分数（可能为负：PMI 为负说明
            # 该监管历史上并不爱 ban 这对，热门度贡献的底数而已）
            "hunters": [hh for _, hh in scored],
            "hunter_scores": [{"char": hh, "score": w} for w, hh in scored],
            # 该选手全部候选监管的完整打分（报告里排成全量排名用）
            "hunter_rank": [{"char": hh, "score": w} for w, hh in all_scored],
        }
    rows = sorted(merged.values(), key=lambda r: -r["weight"])
    for i, r in enumerate(rows):
        r["rank"] = i
    # 用户点名的组合即使分数排不进前 top，也必须留在候选里 ——
    # 否则「搜索这个组合」会静默搜不到。它们排在最前，
    # 后面的自动候选照旧按可能性取前 top 组。
    fs = {tuple(sorted(p)) for p in (focus or ())}
    picked = [r for r in rows if tuple(r["bans"]) in fs]
    for i, r in enumerate(picked):
        r["rank"] = i
    return picked + rows[:top]


def build_combo(eng, team, player, game, bans, cand,
                gone_s=None, gone_h=None, map_name=None, custom=False):
    """把一组对手 ban 组合算成报告里的一个 combo（②③④⑤ 全带上）。

    生成整份报告和「报告页临时搜一个组合」都走这里，保证两边口径一致。
    cand：候选池里那一项（带 weight / rank / hunters / hunter_rank）。
    算不出来（比如 ban 完我方没得选）时返回 None。
    """
    bans = list(bans)
    # 全量排名里每个监管都补上该图倍数，报告页的地图卡直接用这份，
    # 顺序和数量与 ① 段的排名完全一致（intent 只覆盖前几个威胁）。
    hunter_rank = [dict(r) for r in (cand.get("hunter_rank") or [])]
    if map_name:
        for r in hunter_rank:
            r["mult"] = round(eng.map_mult(map_name, r["char"]), 4)
    r = one_combo(eng, team, player, game, bans, verbose_steps=True, emit=False,
                  gone_surv=gone_s, gone_hunt=gone_h, map_name=map_name)
    if not r:
        return None
    return {
        "bans": bans,
        "label": " + ".join(bans),
        "map": map_name or "",
        "weight": cand.get("weight", 0),
        "rank": cand.get("rank", 0),
        "hunters": cand.get("hunters") or [],
        # 指向监管前 3 及各自的可能性分数（① 段表格的百分比来源）
        "hunter_scores": cand.get("hunter_scores") or [],
        # 该选手全部候选监管的完整打分（① 段的全量排名），
        # 指定地图时每项再带 "mult"（该图倍数）
        "hunter_rank": hunter_rank,
        # 用户在报告页自己搜出来的组合（① 段会标出来）
        "custom": bool(custom),
        # ② 这组 ban 反推出的监管意图
        "intent": r["intent"],
        "intent_note": r["intent_note"],
        # ③ 我方应 ban 哪几个监管
        "ban_quota": r["ban_quota"],
        "ban_options": r["ban_options"],
        "ban_hunter": r["ban_hunter"],
        "ban_note": r["ban_note"],
        # ④ 我方前两手（顺序已定，只给结论与逐手评估）
        "first_pick": r["first_pick"],
        "second_pick": r["second_pick"],
        "picks_detail": r["picks_detail"],
        "solid_n": r["solid_n"],
        "picks": r["picks"],
        # ⑤ 后面博弈树
        "half_value": r["half_value"],
        "line": r["line"],
        "steps": r["steps"],
        "tree": r.get("tree") or [],
        "tree_state": r.get("tree_state"),
        "lineup": r["lineup"],
        "solid": r["solid"],
        "kite": r["kite"],
        "expect": r["expect"],
    }


def insert_combo(res, row):
    """把一组新算出的 ban 组合塞进已有报告（就地改 res）。

    规则和生成时一致：用户指定/搜索的组合排最前，其余按可能性从高到低。
    同一组（不分先后）已经存在就替换掉，不会重复。
    返回它在 combos 里的下标。
    """
    combos = res.setdefault("combos", [])
    key = frozenset(row["bans"])
    idx = next((i for i, c in enumerate(combos)
                if frozenset(c.get("bans") or ()) == key), None)
    if idx is None:
        combos.append(row)
    else:
        combos[idx] = row
    combos.sort(key=lambda c: (not c.get("custom"), c.get("rank", 0)))
    for i, c in enumerate(combos):
        c["rank"] = i
    meta = res.setdefault("meta", {})
    meta["combo_count"] = len(combos)
    # 生成时指定、但当时不在本队角色池里的组合，现在算出来了就撤掉那条提示
    meta["focus_skipped"] = [p for p in (meta.get("focus_skipped") or [])
                             if frozenset(p) != key]
    # 摘要那一份也要跟着更新，否则摘要里仍是旧列表
    secs = ((res.get("summary") or {}).get("sections") or [])
    for s in secs:
        if s.get("key") == "combos":
            s["options"] = [{"bans": c["bans"], "label": c["label"],
                             "weight": c["weight"], "hunters": c.get("hunters") or [],
                             "custom": bool(c.get("custom"))}
                            for c in combos]
    return next(i for i, c in enumerate(combos)
                if frozenset(c.get("bans") or ()) == key)


def search_combo(eng, team, player, game, bans, gone_surv=None, gone_hunt=None,
                 map_name=None):
    """报告页的「搜一个 ban 组合」：只算这一组，不改整份报告。

    可能性（weight）仍按 ban_intel 的口径给，和自动候选同源，
    这样搜出来的组合能和报告里已有的组合直接比。
    """
    bans = [str(c).strip() for c in bans if str(c).strip()]
    if len(bans) != 2 or bans[0] == bans[1]:
        return None, "需要两个不同的求生者"
    gone = set(gone_surv or ()) | set(gone_hunt or ())
    used = [c for c in bans if c in gone]
    if used:
        return None, f"{'、'.join(used)} 已经被用掉，对手不可能 ban"
    roster = set(eng.pool_of(team))
    if not set(bans) <= roster:
        return None, f"这两个求生不在 {team} 的角色池里"

    cands = candidate_bans(eng, team, player, top=10**6,
                           exclude=gone, focus=[tuple(bans)])
    key = frozenset(bans)
    cand = next((c for c in cands if frozenset(c["bans"]) == key), None)
    if cand is None:
        return None, "这组 ban 算不出结果"
    row = build_combo(eng, team, player, game, cand["bans"], cand,
                      gone_s=gone_surv, gone_h=gone_hunt, map_name=map_name,
                      custom=True)
    if row is None:
        return None, "这组 ban 算不出结果"
    return row, ""


# ═══════════════════════════════════════════════════════════════
# 主计算
# ═══════════════════════════════════════════════════════════════
def compute(eng, team, player, game, n_hunters=4, bantop=5, n_combos=8,
            global_ban=None, global_ban_surv=None, global_ban_hunt=None,
            our_pool=None, scores_source=None, roster_source=None,
            verbose=False, map_name=None, focus_bans=None):
    """算出以「对手 ban 组合」为轴的整份报告。

    全局禁选分两边（规则：本方用过的角色只封锁本方）：
        global_ban_surv  已被我方用掉的求生者 → 不能再被我方选或 ban
        global_ban_hunt  已被对方用掉的监管   → 对方不能再拿，我方也不必 ban
    global_ban 是兼容旧参数的合并写法（两边一起塞），新代码请用上面两个。
    our_pool：报告页要展示的我方求生角色池；不传就从引擎读。
    n_hunters / bantop 在新版 candidate_bans 里不再参与打分
    （打分走 ban_intel 的指纹+PMI+热门度），保留只为兼容 serve.py 的调用。
    map_name：本局地图。影响对手监管候选的排序、我方 ban 监管的候选、
    我方选人的候选排序。不改任何分数格。
    focus_bans：用户生成前指定要看的对手 ban 组合，[["求生甲", "求生乙"], ...]。
    这些组合一定进候选池，算完排在报告第 ① 段最前面并标记 custom。
    报告页上临时搜的组合走 search_combo()，不经过这里。
    """
    t0 = time.time()
    eng.map_name = map_name or ""
    quota = ban_quota(game)
    gone_s = set(global_ban_surv or ()) | set(global_ban or ())
    gone_h = set(global_ban_hunt or ()) | set(global_ban or ())

    # ② 监管方可能考虑的监管前 N（与用户选哪个 ban 组合无关）
    hunters = enumerate_hunters(eng, player, n=n_hunters,
                                exclude=gone_h, map_name=map_name)
    hunter_rows = [{
        "char": h["char"],
        "p_use": round(h["p_use"], 4),
        "strength": round(h["strength"], 4),
        "threat": round(h["threat"], 4),
        "mult": h.get("mult", 1.0),
        "score": round(h["score"], 4),
    } for h in hunters]

    # ① 候选 ban 组合
    focus = []
    bad_focus = []
    for pair in (focus_bans or ()):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        a, b = str(pair[0]).strip(), str(pair[1]).strip()
        if not a or not b or a == b:
            continue
        key = tuple(sorted((a, b)))
        # 已被用掉的角色对手不可能 ban，直接不进候选（前端也会拦）
        if key[0] in gone_s or key[1] in gone_s:
            bad_focus.append(list(key))
            continue
        focus.append(key)
    focus = list(dict.fromkeys(focus))
    focus_set = set(focus)
    cands = candidate_bans(eng, team, player, n_hunters, bantop, n_combos,
                           exclude=gone_s | gone_h, focus=focus)

    # 用户指定的组合排最前（它们才是一定要看到的结论），
    # 其余保持可能性从高到低的顺序。
    cands.sort(key=lambda c: (tuple(sorted(c["bans"])) not in focus_set,
                              c.get("rank", 0)))
    for i, c in enumerate(cands):
        c["rank"] = i

    combos = []
    for i, c in enumerate(cands):
        if verbose:
            print(f"[{i+1}/{len(cands)}] 对手 ban：{'、'.join(c['bans'])} …", flush=True)
        row = build_combo(eng, team, player, game, c["bans"], c,
                          gone_s=gone_s, gone_h=gone_h, map_name=map_name,
                          custom=tuple(sorted(c["bans"])) in focus_set)
        if row:
            combos.append(row)

    pool = list(our_pool or eng.pool_of(team))
    # 对位情报：显著互斥对（出场率显著低于赛季平均的 (求生, 监管) 组合）
    # 只做行为提示，不进分数 —— 回测证实加进分数预测 MAE 不降。
    try:
        alerts = ban_intel.matchup_alerts(eng, team, player, gone=gone_s | gone_h)
    except Exception:
        alerts = []
    meta = {
        "season": "IVL 2026 秋季赛",
        "team": team,
        "player": player,
        "game": game,
        "map": map_name or "",
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "elapsed_s": round(time.time() - t0, 1),
        "ban_quota": quota,
        "bantop": bantop,
        "hunters": n_hunters,
        "mode": "combo",
        "global_ban_surv": sorted(gone_s),
        "global_ban_hunt": sorted(gone_h),
        "scores_override": bool(getattr(eng, "scores_override", None)),
        "scores_source": scores_source or "",
        "roster_source": roster_source or "",
        "our_pool": pool,
        "combo_count": len(combos),
        "focus_bans": [list(p) for p in focus],
        # 指定了但对手不可能 ban 的组合（里面有已被用掉的角色）
        "focus_skipped": bad_focus,
        "note": "评估单位是局均逃脱，经「最低 3 人」聚合后系统性偏低，"
                "只用于同一对手下的相对比较。",
    }

    return {
        "meta": meta,
        "hunters": hunter_rows,
        "combos": combos,
        "matchup_alerts": alerts,
        "summary": build_summary(meta, hunter_rows, combos),
    }


# ═══════════════════════════════════════════════════════════════
# 五段式摘要
# ═══════════════════════════════════════════════════════════════
def build_summary(meta, hunter_rows, combos):
    """五段式摘要，顺序与教练看报告的思路一致：

        1 对手前两个 ban 位      —— 下拉候选，附可能性与指向的监管
        2 监管方可能考虑的监管前 N
        3 我方应 ban 哪几个监管
        4 我方前两手怎么选
        5 后面博弈树怎么选

    3~5 与第 1 段的选择联动，所以摘要里只放「当前选中的组合」那一份，
    具体每段的值由前端按选中的 combo 取。
    """
    top = combos[0] if combos else None
    return {
        "sections": [
            {
                "title": "对手前两个 ban 位",
                "key": "combos",
                "options": [{"bans": c["bans"], "label": c["label"],
                             "weight": c["weight"], "hunters": c["hunters"],
                             "custom": bool(c.get("custom"))}
                            for c in combos],
                "current": top["bans"] if top else [],
            },
            {
                "title": "监管方可能考虑的监管前 %d" % meta["hunters"],
                "key": "hunters",
                "rows": hunter_rows,
            },
            {
                "title": "我方应 ban 哪几个监管",
                "key": "ban_hunter",
                "rows": top["ban_options"] if top else [],
                "current": top["ban_hunter"] if top else [],
                "note": top["ban_note"] if top else "",
            },
            {
                "title": "我方前两手怎么选",
                "key": "picks",
                "picks": top["picks"] if top else [],
                "picks_detail": top["picks_detail"] if top else [],
                "solid_n": top["solid_n"] if top else 0,
            },
            {
                "title": "后面博弈树怎么选",
                "key": "tree",
                "line": top["line"] if top else [],
                "steps": top["steps"] if top else [],
                "lineup": top["lineup"] if top else [],
                "expect": top["expect"] if top else None,
            },
        ],
        "head_hunter": top["hunters"][0] if top and top["hunters"] else None,
    }


# ═══════════════════════════════════════════════════════════════
def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("team")
    ap.add_argument("player")
    ap.add_argument("game", type=int)
    ap.add_argument("--hunters", type=int, default=4)
    ap.add_argument("--bantop", type=int, default=5)
    ap.add_argument("--combos", type=int, default=8)
    ap.add_argument("--map", default="")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    eng = Engine()
    data = compute(eng, args.team, args.player, args.game,
                   n_hunters=args.hunters, bantop=args.bantop,
                   n_combos=args.combos, verbose=True,
                   map_name=args.map or None)
    print(f"\n共 {len(data['combos'])} 个组合，耗时 {data['meta']['elapsed_s']}s")
    if args.json:
        p = Path(args.json)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                     encoding="utf-8")
        print(f"已写入 {p}")


if __name__ == "__main__":
    main()
