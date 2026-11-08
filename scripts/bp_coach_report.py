# -*- coding: utf-8 -*-
"""求生方教练报告（组合版）：批量分析对手 ① ban 的多个组合，并反推对手想打的监管。

════════════════════════════════════════════════════════════════
用法
════════════════════════════════════════════════════════════════
    python scripts/bp_coach_report.py ACT YuM 3
        默认取对手最可能的 5 个 ban 角色，两两组合逐组出报告

    python scripts/bp_coach_report.py ACT YuM 3 --topk 4
        只取前 4 个角色组合（6 组）

    python scripts/bp_coach_report.py ACT YuM 3 --ban 幻灯师 弓箭手
        只算指定的这一组

════════════════════════════════════════════════════════════════
每组报告回答四个问题
════════════════════════════════════════════════════════════════
  1. 对手可能想打哪些监管   —— 从「ban 这组求生者」反推：哪些监管最吃亏于
                              这组角色不在场，以及选手自身的使用偏好
  2. 我该 ban 对手哪几个    —— ② 每个候选的最终博弈值
  3. 我该先选哪两个         —— ③ 第一手 / 第二手各自最优
  4. 后续 ban-pick 怎么走   —— ④⑤⑥⑦ 主变线路 + 关键备选

════════════════════════════════════════════════════════════════
反推逻辑（问题 1）
════════════════════════════════════════════════════════════════
对手 ban 掉的求生者 = 他不想面对的角色。
对每个候选监管 H，算「这组被 ban 的求生者对该监管的阵容评分」相对
「同队最强替身补位」的落差，落差越大，说明 H 越依赖被 ban 的角色，
也就是对手更可能想打 H。再乘以选手对 H 的使用率作为加权。
"""
import json
import sys
import time
from itertools import combinations
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))

from bp_search_engine import Engine, SOLID, ROOT, canon  # noqa: E402

# 报告会跑几十秒，绑定成逐行刷新的 print，方便看进度
_print = print


def print(*a, **k):  # noqa: A001
    k.setdefault("flush", True)
    _print(*a, **k)

STAGE_NAME = {
    "①": "监管 ban 求生", "②": "求生 ban 监管", "③": "求生选 1/2",
    "④": "监管 ban 求生", "⑤": "求生选 2/2", "⑥": "监管 ban 求生",
    "⑦": "求生选完", "⑩": "监管选人（仅展示）",
}


# ═══════════════════════════════════════════════════════════════
# 基础工具
# ═══════════════════════════════════════════════════════════════
def line_state(stages, line, start_idx=0, start_taken=0,
               start_lban=None, start_lpick=None, start_spicks=None):
    """沿线路重建每个动作发生前的状态。

    line 可能不是从 stages[0] 开始的（教练报告会从 ③ 或 ④ 中途续搜），
    此时必须把起始状态传进来，否则 ban/pick 池会完全错位，
    后面算出的「更优/更差」也就全部不成立。
    """
    out = []
    lban = set(start_lban or ())
    lpick = set(start_lpick or ())
    spicks = set(start_spicks or ())
    idx, tkn = start_idx, start_taken
    for code, c, v in line:
        while idx < len(stages) and stages[idx][0] != code:
            idx += 1
            tkn = 0
        if idx >= len(stages):
            break
        kind = stages[idx][1]
        out.append({"idx": idx, "taken": tkn, "code": code, "kind": kind,
                    "lban": set(lban), "lpick": set(lpick), "spicks": set(spicks),
                    "chosen": c})
        if kind.endswith("ban"):
            lban.add(c)
        elif kind != "hunt_pick":
            lpick.add(c)
            spicks.add(c)
        tkn += 1
        if tkn >= stages[idx][2]:
            idx, tkn = idx + 1, 0
    return out


def run_from(eng, team, player, game, idx, taken, lban, lpick, spicks,
             fresh=False):
    """从任意中间状态搜到叶子。

    fresh=True 时清空记忆化缓存，从零开始搜。
    默认不清 —— memo 的 key 是完整状态
    (阶段, 已ban, 已选, 已pick的求生)，同一个状态的后续最优值与
    从哪条路径到达无关，所以可以在多个候选之间复用。
    过去每次都清缓存，导致「算 5 个 ban 候选」把同一片搜索树
    重新搜了 5 遍（实测 39s，占单组合耗时的绝大部分）。

    `_taken_chars` 仍要清：它记的是「当前搜索路径上这一阶段选了谁」，
    跟路径有关，跨候选复用会串味。
    """
    stages = eng._stages(game)
    eng.counters = {"nodes": 0, "cut": 0}
    eng._taken_chars = {}
    if fresh:
        eng._memo = {}
    return eng._dfs(team, player, game, stages, idx,
                    frozenset(lban), frozenset(lpick), frozenset(spicks),
                    -1e9, 1e9, len(stages), taken)


def value_after(eng, team, player, game, state, char, extra=None):
    """在 state 之后强制走 char（可连带 extra），后续搜到叶子。"""
    stages = eng._stages(game)
    kind = state["kind"]
    lban, lpick, spicks = set(state["lban"]), set(state["lpick"]), set(state["spicks"])
    acts = [char] + list(extra or [])
    for a in acts:
        if kind.endswith("ban"):
            lban.add(a)
        elif kind != "hunt_pick":
            lpick.add(a)
            spicks.add(a)
    count = stages[state["idx"]][2]
    if state["taken"] + len(acts) >= count:
        nidx, ntaken = state["idx"] + 1, 0
    else:
        nidx, ntaken = state["idx"], state["taken"] + len(acts)
    v, _ = run_from(eng, team, player, game, nidx, ntaken, lban, lpick, spicks)
    return v, (nidx, ntaken, lban, lpick, spicks)


def pick_candidates(eng, team, player, state, topn=6, map_name=None):
    """当前可选的求生角色，按补进来后的浅层评估排序。

    map_name：本局地图。给了就用「浅层评估 × 地图倍数」重排，
    因为浅层评估本身已经含了地图对监管候选的影响，再乘一次
    求生角色的地图倍数，才是「这张图更可能选谁」的排序。

    注意：这里截断到 topn，所以排序会真正决定候选范围。
    引擎 _dfs 里的 surv_pick 是对全 roster 求最大，排序只影响
    剪枝效率、不影响结果，地图在那个位置没有作用。
    """
    roster = eng.pool_of(team)
    cs = [c for c in roster if c not in state["lban"] and c not in state["lpick"]]

    def key(c):
        v = eng._shallow(team, player, frozenset(set(state["spicks"]) | {c}))
        if map_name:
            v = v * eng.surv_map_mult(map_name, c)
        return -v

    cs.sort(key=key)
    return cs[:topn]


def ban_hunter_candidates(eng, player, state, topn=5, gone_hunt=None,
                          map_name=None):
    """② 求生 ban 监管的候选：威胁度降序。

    gone_hunt：本场之前对方已经用掉的监管，对方拿不到，我方也不必 ban。
    map_name：本局地图，按「威胁度 × 地图倍数」重排 —— 这张图上他更可能
    拿的监管优先 ban。实测真实监管落在前 3 的比例 59.7% → 66.7%。
    """
    lb, lp = state.get("lban") or set(), state.get("lpick") or set()
    gone = set(gone_hunt or ())
    cs = [c for c in eng.ban_candidates_hunt(player, map_name or "")
          if c not in lb and c not in lp and c not in gone]

    def key(c):
        th = next((t["threat"] for t in eng.players[player]["threats"]
                   if t["char"] == c), 0.0)
        if map_name:
            th *= eng.map_mult(map_name, c)
        return -th

    cs.sort(key=key)
    return cs[:topn]


def fmt_gap(v, base, is_max):
    d = v - base
    if abs(d) < 1e-9:
        return "持平"
    better = (d > 0) if is_max else (d < 0)
    return ("更优 %+.3f" if better else "更差 %+.3f") % d


# ═══════════════════════════════════════════════════════════════
# 问题 1：从「ban 了这组求生者」反推对手想打的监管
# ═══════════════════════════════════════════════════════════════
def infer_hunter_intent(eng, team, player, banned_survivors, topn=6,
                        gone_surv=None, gone_hunt=None, map_name=None):
    """反推：对手 ban 掉这些求生者之后，最可能是为了打哪些监管。

    两个信号：
      A. 剩余池压制力 strong = 2.00 - 剩余最优三人对该监管的均分。
         分越低 = 这套监管越强，对手越愿意用。
      B. 针对度 hit = 被 ban 角色对该监管的最好成绩 - 剩余最优三人均分。
         hit ≈ 0 或为正：被 ban 的角色本来就是对该监管最好用的，
         说明这组 ban 有针对性（对手在为这个监管清路）。
         hit 明显为负：被 ban 的角色对该监管并非最优，这组 ban 是
         通用强度 ban，与这个监管无关。

    实测（ACT vs YuM）：hit 全部是负数，因为 ACT 被 ban 的幻灯师、
    弓箭手等本就不是最能打的求生者。所以 hit 只用来判断「有没有针对性」，
    不参与主评分；评分以压制力为准，并在输出时显式提示有无针对性。

    gone_surv / gone_hunt：本场之前已用掉的求生 / 监管（全局禁选）。
    已被我方用掉的求生者不在场上，不能参与「剩余池」的对比；
    已被对方用掉的监管对方拿不到，不再列入意图。

    返回 [{char, p_use, threat, best3, strong, hit, targeted, score}]。
    """
    info = eng.players[player]
    roster = eng.pool_of(team)
    usable = [c for c in roster
              if c not in banned_survivors and c not in set(gone_surv or ())]
    if not usable:
        return []

    rows = []
    for t in info["threats"][:8]:
        h = t["char"]
        if t["p_use"] <= 0 or h in set(gone_hunt or ()):
            continue
        scores = sorted((eng.cell(team, c, h) for c in usable), reverse=True)
        best3 = sum(scores[:3]) / min(3, len(scores))
        # 压制：对剩余最强三人的分越低，对手越愿意用这套
        strong = 2.00 - best3
        # 针对度：被 ban 的角色是不是对该监管特别好用
        if banned_survivors:
            ban_best = max(eng.cell(team, c, h) for c in banned_survivors)
            hit = ban_best - best3
        else:
            hit = 0.0
        # 地图：这张图上他更可能拿的监管排在前面（乘在威胁度那一项上，
        # 不动压制力 —— 压制力是这对阵容的客观强度，与地图无关）
        mult = eng.map_mult(map_name, h) if map_name else 1.0
        rows.append({
            "char": h, "p_use": t["p_use"], "threat": t["threat"],
            "best3": best3, "strong": strong, "hit": hit,
            "mult": round(mult, 4),
            # 有针对性的（hit ≥ -0.10）额外加权，提示「对手在为他清路」
            "targeted": hit >= -0.10,
            "score": t["p_use"] * max(strong, 0.0) + t["threat"] * 0.5 * mult
                     + (0.15 if hit >= -0.10 else 0.0),
        })

    rows.sort(key=lambda r: -r["score"])
    return rows[:topn]


# ═══════════════════════════════════════════════════════════════
# 单个 ban 组合的完整报告
# ═══════════════════════════════════════════════════════════════
def one_combo(eng, team, player, game, bans, verbose_steps=True, emit=True,
              gone_surv=None, gone_hunt=None, map_name=None):
    """针对一组 ① ban 生成四问报告。

    计算与展示分离：先把结果组装成 dict，再（可选）打印。
    这样同一份数据既能出文本报告，也能直接写 JSON 给 Web 用。

    gone_surv / gone_hunt 是本场之前已被用掉的求生 / 监管（全局禁选）。
    求生被用掉只封锁我方，监管被用掉只封锁对方，所以分开传。
    两者都直接进 lban —— 被 ban 的角色既不能被选，也不会出现在候选里。

    map_name：本局地图。这里必须写进 eng.map_name，因为后面调的
    _leaf / _shallow / ban_candidates_response 都从引擎读它；
    显式传参的那几处（ban_hunter_candidates / pick_candidates /
    infer_hunter_intent）也一并传，保证口径一致。
    """
    eng.map_name = map_name or ""
    stages = eng._stages(game)
    lban = set(bans) | set(gone_surv or ()) | set(gone_hunt or ())
    idx1 = next(i for i, s in enumerate(stages) if s[0] == "①")
    if idx1 + 1 >= len(stages):
        return None
    nidx, ntaken = idx1 + 1, 0

    def say(*a):
        if emit:
            print(*a)

    say("\n" + "=" * 74)
    say(f"对手 ① ban：{'、'.join(bans)}")
    if map_name:
        say(f"地图：{map_name}")
    say("=" * 74)

    # ── 问题 1：反推对手想打的监管 ──
    say("\n1. 对手可能想打哪些监管")
    say("-" * 74)
    rows = infer_hunter_intent(eng, team, player, bans,
                               gone_surv=gone_surv, gone_hunt=gone_hunt,
                               map_name=map_name)
    intent = []
    for r in rows[:6]:
        intent.append({
            "char": r["char"],
            "p_use": round(r["p_use"], 4),
            "strength": round(eng.strength.get(r["char"], 0), 4),
            "best3": round(r["best3"], 4),
            "strong": round(r["strong"], 4),
            "hit": round(r["hit"], 4),
            "threat": round(r["threat"], 4),
            "mult": r.get("mult", 1.0),
        })
    if map_name:
        say(f"   {'监管':<11}{'使用率':>7}{'强度':>7}{'对剩余最优三人':>13}"
            f"{'压制':>7}{'针对度':>8}{'该图倍数':>9}  威胁")
    else:
        say(f"   {'监管':<11}{'使用率':>7}{'强度':>7}{'对剩余最优三人':>13}"
            f"{'压制':>7}{'针对度':>8}  威胁")
    for d in intent:
        hit = f"+{d['hit']:.2f}" if d["hit"] > 0.02 else f"{d['hit']:.2f}"
        tail = f"{d['mult']:>9.2f}" if map_name else ""
        say(f"   {d['char']:<11}{d['p_use']*100:>6.1f}%{d['strength']:>7.2f}"
            f"{d['best3']:>13.3f}{d['strong']:>7.2f}{hit:>8}{tail}  {d['threat']:.3f}")

    intent_note = ""
    if intent:
        say(f"\n   → 这组 ban 最可能指向：{'、'.join(d['char'] for d in intent[:3])}")
        best = intent[0]
        say(f"     {best['char']} 对剩余求生池最优三人仅 {best['best3']:.2f}，"
            f"是他压制力最强的选择")
        hits = sorted((d["hit"] for d in intent), reverse=True)
        lead = hits[0] - hits[1] if len(hits) > 1 else 0.0
        if hits[0] >= -0.10 and lead >= 0.15:
            intent_note = (f"有针对性：{best['char']} 的针对度 {hits[0]:+.2f}，"
                           f"明显高于次席 {hits[1]:+.2f}（领先 {lead:.2f}）")
        elif hits[0] >= 0.0:
            intent_note = (f"有针对性：被 ban 的角色对 {best['char']} 本来就是本队最好用的"
                           f"（{hits[0]:+.2f}）")
        else:
            intent_note = (f"没有明显针对性（各监管针对度在 {hits[0]:+.2f} ~ {hits[-1]:+.2f} "
                           f"之间波动但整体偏负），更像通用的强角色 ban")
        say(f"     {intent_note}")

    # ── 问题 2：我该 ban 对手哪几个 ──
    say("\n2. 我该 ban 对手哪几个")
    say("-" * 74)
    st2 = {"idx": nidx, "taken": ntaken, "kind": stages[nidx][1],
           "lban": set(lban), "lpick": set(), "spicks": set()}
    quota = stages[nidx][2] if stages[nidx][1] == "surv_ban" else 0
    chosen_ban = []
    ban_rows = []
    ban_note = ""
    if quota == 0:
        say("   第 1 局没有 ban 监管的名额，直接进入选人。")
        ban_note = "第 1 局没有 ban 监管的名额"
    else:
        cands = ban_hunter_candidates(eng, player, st2, topn=5,
                                      gone_hunt=gone_hunt, map_name=map_name)
        rr = []
        for c in cands:
            v, _ = value_after(eng, team, player, game, st2, c)
            rr.append((c, v))
        rr.sort(key=lambda x: -x[1])
        best = rr[0][1]
        say(f"   名额 {quota} 个，下表是 ban 掉该监管后双方最优博弈的结果（越高越对求生有利）：")
        say(f"   {'监管':<12}{'评估':>8}   {'相对最优':<14}使用率")
        for c, v in rr:
            p = next((t["p_use"] for t in eng.players[player]["threats"]
                      if t["char"] == c), 0)
            ban_rows.append({"char": c, "value": round(v, 4),
                             "p_use": round(p, 4)})
            say(f"   {c:<12}{v:>8.4f}   {fmt_gap(v, best, True):<14}{p*100:.1f}%")
        if quota >= 2:
            combos = list(combinations([c for c, _ in rr], 2))
            bc = None
            for a, b in combos:
                v, _ = value_after(eng, team, player, game, st2, a, extra=[b])
                if bc is None or v > bc[2]:
                    bc = (a, b, v)
            if bc:
                say(f"\n   → 两 ban 组合最优：{bc[0]} + {bc[1]}（评估 {bc[2]:.4f}）")
                chosen_ban = [bc[0], bc[1]]
            else:
                chosen_ban = [c for c, _ in rr[:quota]]
        else:
            chosen_ban = [rr[0][0]]
        if chosen_ban:
            spread = rr[0][1] - rr[-1][1]
            say(f"   → 建议 ban：{'、'.join(chosen_ban)}")
            if spread < 0.005:
                ban_note = (f"所有候选几乎等价（极差 {spread:.3f}），"
                            f"ban 谁都不改变结局，可优先 ban 自己更不想面对的那个")
                say(f"     注意：{ban_note}")

    # ── 问题 3：我该先选哪两个 ──
    say("\n3. 我该先选哪两个")
    say("-" * 74)
    idx3 = next(i for i, s in enumerate(stages) if s[0] == "③")
    lban3 = set(lban) | set(chosen_ban)
    st3 = {"idx": idx3, "taken": 0, "kind": "surv_pick",
           "lban": lban3, "lpick": set(), "spicks": set()}
    first = pick_candidates(eng, team, player, st3, topn=6,
                            map_name=map_name)
    r1 = []
    for c in first:
        v, nxt = value_after(eng, team, player, game, st3, c)
        r1.append((c, v, nxt))
    r1.sort(key=lambda x: -x[1])
    b1 = r1[0][1]
    say(f"   第一手（数值 = 选他之后双方把后面走完）：")
    say(f"   {'角色':<12}{'评估':>8}   {'相对最优':<14}定位")
    first_rows = []
    for c, v, _ in r1:
        pos = eng.positions.get(c) or []
        first_rows.append({"char": c, "value": round(v, 4),
                           "pos": pos, "solid": c in SOLID})
        say(f"   {c:<12}{v:>8.4f}   {fmt_gap(v, b1, True):<14}"
            f"{'/'.join(pos) or '—'}{' · 稳救' if c in SOLID else ''}")
    first_pick = r1[0][0]
    say(f"   → 第一手推荐 {first_pick}")

    nidx2, ntaken2, nb2, np2, ns2 = r1[0][2]
    st3b = {"idx": nidx2, "taken": ntaken2,
            "kind": stages[nidx2][1] if nidx2 < len(stages) else "surv_pick",
            "lban": set(nb2), "lpick": set(np2), "spicks": set(ns2)}
    second = pick_candidates(eng, team, player, st3b, topn=6,
                             map_name=map_name)
    r2 = []
    for c in second:
        v, nxt = value_after(eng, team, player, game, st3b, c)
        r2.append((c, v, nxt))
    r2.sort(key=lambda x: -x[1])
    b2 = r2[0][1]
    say(f"   第一手 {first_pick} 之后，第二手：")
    say(f"   {'角色':<12}{'评估':>8}   {'相对最优':<14}定位")
    second_rows = []
    for c, v, _ in r2:
        pos = eng.positions.get(c) or []
        second_rows.append({"char": c, "value": round(v, 4),
                            "pos": pos, "solid": c in SOLID})
        say(f"   {c:<12}{v:>8.4f}   {fmt_gap(v, b2, True):<14}"
            f"{'/'.join(pos) or '—'}{' · 稳救' if c in SOLID else ''}")
    second_pick = r2[0][0]
    say(f"   → 第二手推荐 {second_pick}")
    solid_n = sum(1 for c in (first_pick, second_pick) if c in SOLID)
    note = ("正常" if solid_n == 1
            else ("⚠ 没有稳救，第三手必须补" if solid_n == 0 else "⚠ 稳救过多"))
    say(f"   → 前两手：{first_pick} + {second_pick}（稳救 {solid_n} 个，{note}）")

    # ── 问题 4：后续 ban-pick 怎么走 ──
    # 从「前两手都定下之后」继续搜 —— 这样第⑤段的博弈树不含 ③，
    # 不会和第④段报的前两手打架。
    nidx3, ntaken3, nb3, np3, ns3 = r2[0][2]
    v4, line4 = run_from(eng, team, player, game, nidx3, ntaken3, nb3, np3, ns3)
    say(f"   后半段整体最优结果 {v4:.4f}")
    line_rows = []
    for code, c, v in line4:
        line_rows.append({"stage": code, "name": STAGE_NAME.get(code, ""),
                          "char": c, "value": round(v, 4)})
        say(f"   {code:<4}{STAGE_NAME.get(code, ''):<18}{str(c):<12}{v:>8.4f}")

    # 起点必须是「③ 两手都选完」之后的状态（nidx3/ns3）。
    # 用 nidx2（只选完第一手）会导致第二手没进 spicks，
    # 后面每一段都会把那个已经选下的角色当成还能被 ban 的对象。
    steps = line_state(stages, line4, nidx3, ntaken3, nb3, np3, ns3)

    step_rows = []
    if verbose_steps:
        say(f"\n   各步关键备选（只列前 3）")
        for st in steps:
            if st["kind"] == "hunt_pick":
                continue
            kind, code = st["kind"], st["code"]
            if kind == "hunt_ban":
                cs = eng.ban_candidates_response(team, st["spicks"], st["lban"],
                                                 st["lpick"], player, eng.cand_ban_surv)[:3]
                is_max = False
            elif kind == "surv_ban":
                cs = ban_hunter_candidates(eng, player, st, topn=3,
                                          gone_hunt=gone_hunt,
                                          map_name=map_name)
                is_max = True
            else:
                cs = pick_candidates(eng, team, player, st, topn=3,
                                     map_name=map_name)
                is_max = True
            if st["chosen"] not in cs:
                cs = [st["chosen"]] + cs[:2]
            rr = [(c, value_after(eng, team, player, game, st, c)[0]) for c in cs[:3]]
            # 排序按「谁在做选择」：监管 ban 时他会挑值最低的，所以按升序
            rr.sort(key=lambda x: -x[1] if is_max else x[1])
            bv = next((v for c, v in rr if c == st["chosen"]), rr[0][1])
            # 标签一律用求生方视角：值越高对求生越有利
            opts = []
            for c, v in rr:
                opts.append({
                    "char": c,
                    "value": round(v, 4),
                    "delta": round(v - bv, 4),
                    "chosen": c == st["chosen"],
                    "better": (v - bv) > 1e-9,
                })
            txt = "，".join(f"{c} {v:.3f}"
                            + ("" if c == st["chosen"] else f"（对求生{fmt_gap(v, bv, True)}）")
                            for c, v in rr)
            say(f"     {code} 主选 {st['chosen']:<10} | {txt}")
            step_rows.append({
                "stage": code, "name": STAGE_NAME.get(code, ""),
                "actor": "求生者" if is_max else "监管者",
                "chosen": st["chosen"], "options": opts,
            })

    # 后半段里属于 ③ 的那一手就是第二手，避免和前两手拼成 5 人
    all_picks = [s["chosen"] for s in steps
                 if s["kind"] == "surv_pick" and s["code"] != "③"]
    lineup = [first_pick, second_pick] + all_picks
    solid = [c for c in lineup if c in SOLID]
    kite = [c for c in lineup if c not in SOLID
            and "牵制型" in (eng.positions.get(c) or [])]
    r = eng.expected_vs_player(team, lineup, player)
    say(f"\n   最终阵容：{'、'.join(lineup)}")
    say(f"     稳救 {len(solid)} 个" + (f"（{'、'.join(solid)}）" if solid else "")
        + f"，牵制型 {len(kite)} 个，评估 {r['expect']:.4f}")

    return {
        "bans": list(bans),
        "map": map_name or "",
        "intent": intent,
        "intent_note": intent_note,
        "ban_quota": quota,
        "ban_options": ban_rows,
        "ban_hunter": chosen_ban,
        "ban_note": ban_note,
        "first_pick": first_pick,
        "first_options": first_rows,
        "second_pick": second_pick,
        "second_options": second_rows,
        "solid_n": solid_n,
        "half_value": round(v4, 4),
        "line": line_rows,
        "steps": step_rows,
        "lineup": lineup,
        "solid": solid,
        "kite": kite,
        "expect": round(r["expect"], 4),
        # 汇总用
        "picks": [first_pick, second_pick],
    }


# ═══════════════════════════════════════════════════════════════
def main():
    argv = sys.argv[1:]
    manual = None
    topk = 4
    out_json = None
    quiet = False
    map_name = None
    if "--map" in argv:
        i = argv.index("--map")
        map_name = argv[i + 1] if i + 1 < len(argv) else ""
        argv = argv[:i] + argv[i + 2:]
    if "--ban" in argv:
        i = argv.index("--ban")
        manual = [canon(a) for a in argv[i + 1:i + 3]]
        argv = argv[:i] + argv[i + 3:]
    if "--topk" in argv:
        i = argv.index("--topk")
        topk = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if "--json" in argv:
        i = argv.index("--json")
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            out_json = argv[i + 1]
            argv = argv[:i] + argv[i + 2:]
        else:
            out_json = str(ROOT / "data" / "coach_report.json")
            argv = argv[:i] + argv[i + 1:]
        quiet = True
    args = [a for a in argv if not a.startswith("--")]

    if len(args) < 3:
        print("用法：python scripts/bp_coach_report.py <战队> <监管选手> <局次> "
              "[--topk N] [--ban A B] [--map 地图名] [--json 输出路径]")
        return
    team, player, game = args[0], args[1], int(args[2])
    eng = Engine()
    if team not in eng.teams:
        print(f"未知战队 {team}，可选：{', '.join(sorted(eng.teams))}")
        return
    if player not in eng.players:
        print(f"未知选手 {player}，可选：{', '.join(sorted(eng.players))}")
        return

    t0 = time.time()
    if not quiet:
        print("=" * 74)
        print(f"求生方教练报告 · {team}  vs  {player}（监管） · 第 {game} 局"
              + (f" · {map_name}" if map_name else ""))
        print("=" * 74)

    if manual:
        combos = [tuple(manual)]
    else:
        top_chars = [c for c in eng.ban_candidates_surv(team)][:topk]
        top_chars.sort(key=lambda c: -eng.ban_surv.get(c, 0.0))
        combos = list(combinations(top_chars, 2))
        if not quiet:
            print(f"\n对手最可能 ban 的 {len(top_chars)} 个角色：{'、'.join(top_chars)}")
            print(f"（按历史被 ban 率排序，两两组合共 {len(combos)} 组）")

    results = []
    for bans in combos:
        if quiet:
            print(f"  计算 {'、'.join(bans)} ...", end="\r", flush=True)
        r = one_combo(eng, team, player, game, list(bans), emit=not quiet,
                      map_name=map_name)
        if r:
            results.append(r)

    if quiet:
        print(" " * 60, end="\r")

    # 汇总
    if not quiet:
        print("\n" + "=" * 74)
        print("汇总：各 ban 组合下的最优应对")
        print("=" * 74)
        print(f"{'对手 ban':<20}{'我 ban':<16}{'前两手':<20}{'评估':>8}")
        for r in sorted(results, key=lambda x: -x["expect"]):
            print(f"{'、'.join(r['bans']):<20}{'、'.join(r['ban_hunter']) or '—':<16}"
                  f"{'、'.join(r['picks']):<20}{r['expect']:>8.4f}")

    if out_json:
        payload = {
            "meta": {
                "season": "IVL 2026 秋季赛",
                "rule_version": "第十一版",
                "team": team,
                "player": player,
                "game": game,
                "map": map_name or "",
                "generated_at": time.strftime("%Y-%m-%d %H:%M"),
                "elapsed_s": round(time.time() - t0, 1),
                "candidates": [c for c in
                               ([x for x in eng.ban_candidates_surv(team)][:topk]
                                if not manual else list(manual))],
                "note": "评估单位是局均逃脱，经「最低 3 人」聚合后系统性偏低，"
                        "只用于同一对手下的相对比较。",
            },
            "combos": results,
        }
        p = Path(out_json)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已写入 {p}（{len(results)} 组，耗时 {time.time() - t0:.1f}s）")
    elif not quiet:
        print(f"\n（总耗时 {time.time() - t0:.1f}s）")


if __name__ == "__main__":
    main()
