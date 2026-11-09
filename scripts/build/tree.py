# -*- coding: utf-8 -*-
"""构建 BP 决策树数据（依据 docs/rules_v11.md 第十一版 + 260 半局实测校正）。

════════════════════════════════════════════════════════════════
本局阶段序列（文档 3.1）
════════════════════════════════════════════════════════════════
    ①  监管 ban 求生 2      恒为 2
    ②  求生 ban 监管 0/1/2  第1局0 第2局1 第3局起2
    ③  求生 pick 求生 2
    ④  监管 ban 求生 1
    ⑤  求生 pick 求生 1
    ⑥  监管 ban 求生 1
    ⑦  求生 pick 求生 1
    ⑩  监管 pick 监管 1

════════════════════════════════════════════════════════════════
威胁度模型
════════════════════════════════════════════════════════════════
    threat(p, c) = P_use(p, c) × strength(c)

    P_use    选手 p 使用角色 c 的概率（hunter_players.json，贝叶斯收缩）
    strength 角色强度，由 α 归一化：strength = (max_α − α) / (max_α − min_α)
             α 越负越强。只对 7 个主力监管有 α。

    对策卡（出场 < 20 场）不做 α 估计，单独列出，不参与威胁排序。

════════════════════════════════════════════════════════════════
BAN 统计（供页面参考）
════════════════════════════════════════════════════════════════
战报只给 4 个求生者 ban 的集合，无法还原 ①/④/⑥ 的批次归属，
因此统计按「全部 4 个」合并，页面上会明确标注这一点。
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent.parent
SOLID = {"守墓人", "大副", "佣兵", "前锋", "野人", "逃脱大师"}

# 战报旧名 / 带引号名 → 去引号正式名
ALIAS = {"邦邦": "26号守卫"}


def canon(c: str) -> str:
    c = c.replace("\u201c", "").replace("\u201d", "").replace('"', "")
    return ALIAS.get(c, c)


def load_positions():
    """角色定位与角色池属于静态图鉴，不随 bp_core 变化。

    优先读 assets/portraits_manifest.json；缺失时退回现有 bp_tree.json，
    再退回 web_bp.json（此时监管角色池只剩本赛季出场过的角色）。
    """
    man_path = ROOT / "assets" / "portraits_manifest.json"
    if man_path.exists():
        man = json.loads(man_path.read_text(encoding="utf-8"))
        pos, pool_surv, pool_hunt = {}, [], []
        for m in man:
            n = canon(m["name"])
            if m.get("side") == "求生者":
                pos[n] = m.get("positions") or []
                pool_surv.append(n)
            elif m.get("side") == "监管者":
                pool_hunt.append(n)
        return pos, sorted(pool_surv), sorted(pool_hunt)

    tree_path = ROOT / "data" / "bp_tree.json"
    if tree_path.exists():
        old = json.loads(tree_path.read_text(encoding="utf-8"))
        return (
            old.get("positions") or {},
            sorted(old.get("pool", {}).get("survivor") or []),
            sorted(old.get("pool", {}).get("hunter") or []),
        )

    web_path = ROOT / "data" / "web_bp.json"
    if web_path.exists():
        old = json.loads(web_path.read_text(encoding="utf-8"))
        roles = old.get("roles") or {}
        return (
            {c: (v.get("pos") or []) for c, v in roles.items()},
            sorted(roles),
            sorted((old.get("meta") or {}).get("hunter_pool") or []),
        )

    raise SystemExit(
        "缺少角色图鉴：assets/portraits_manifest.json、data/bp_tree.json "
        "与 data/web_bp.json 都不存在，无法确定角色定位。"
    )


def build(core=None, hp=None, adj=None, gallery=None):
    """返回 BP 决策树字典。四个来源不给就读磁盘 / 用静态图鉴。"""
    if core is None:
        core = json.loads((ROOT / "data" / "bp_core.json").read_text(encoding="utf-8"))
    if hp is None:
        hp = json.loads((ROOT / "data" / "hunter_players.json").read_text(encoding="utf-8"))
    if adj is None:
        adj = json.loads((ROOT / "data" / "adjusted_scores.json").read_text(encoding="utf-8"))

    pos, pool_surv, pool_hunt = (gallery if gallery is not None
                                 else load_positions())

    halves = core["halves"]

    # ── α 强度 ──────────────────────────────────────────────
    effect = adj.get("hunter_char_effect") or {}
    alpha = {canon(c): v["alpha"] for c, v in effect.items()
             if isinstance(v, dict) and "alpha" in v}
    vals = list(alpha.values())
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    strength = {c: (hi - v) / span for c, v in alpha.items()}   # 最强 = 1
    counter_pick = [canon(c) for c in (adj.get("counter_pick_chars") or {})]

    print(f"α 覆盖 {len(alpha)} 个主力监管，对策卡 {len(counter_pick)} 个")

    # ── 监管方 ban 求生者：全部 4 个合并统计 ────────────────
    hb = Counter(c for h in halves for c in h["hunter_bans"])
    n_ban = sum(hb.values())
    ban_all = [{"char": c, "n": k, "p": round(k / n_ban, 4)} for c, k in hb.most_common()]

    # 按局次分开统计（① 的数量恒为 2，但战报无法区分批次）
    # 加时局的 game 是 4/5（真实局号），跟正常局一起统计；
    # tiebreak 单独记一份，方便页面区分。
    hb_by_game = {}
    for g in [1, 2, 3, 4, 5]:
        hs = [h for h in halves if h.get("game") == g]
        if not hs:
            continue
        c = Counter(x for h in hs for x in h["hunter_bans"])
        tot = sum(c.values()) or 1
        hb_by_game[str(g)] = [{"char": k, "n": v, "p": round(v / tot, 4)}
                              for k, v in c.most_common(15)]

    # ── 求生方 ban 监管（② 阶段） ──────────────────────────
    sb = Counter(c for h in halves for c in h["survivor_bans"])
    n_sb = sum(sb.values()) or 1
    sban_all = [{"char": c, "n": k, "p": round(k / n_sb, 4)} for c, k in sb.most_common()]
    sban_by_game = {}
    for g in [2, 3, 4, 5]:
        hs = [h for h in halves if h.get("game") == g]
        if not hs:
            continue
        c = Counter(x for h in hs for x in h["survivor_bans"])
        tot = sum(c.values()) or 1
        sban_by_game[str(g)] = [{"char": k, "n": v, "p": round(v / tot, 4)}
                                for k, v in c.most_common(15)]

    # ── 战队角色池 ──────────────────────────────────────────
    team_roster = defaultdict(set)
    for h in halves:
        team_roster[h["survivor_team"]] |= set(h["survivors"])

    def typ(c, t):
        return t in pos.get(c, [])

    teams = {}
    for team, roster in team_roster.items():
        solid = sorted(c for c in roster if c in SOLID)
        # 稳救的优先级：用本队实际出现过的情况给个参考顺序
        teams[team] = {
            "roster": sorted(roster),
            "solid": solid,
            "kite": sorted(c for c in roster if typ(c, "牵制型")),
            "rescue": sorted(c for c in roster if typ(c, "救援型")),
            "support": sorted(c for c in roster if typ(c, "辅助型")),
            "decode": sorted(c for c in roster if typ(c, "破译型")),
        }

    # ── 局次统计 ────────────────────────────────────────────
    games = {}
    for g in [1, 2, 3, 4, 5]:
        hs = [h for h in halves if h.get("game") == g]
        if not hs:
            continue
        n = len(hs)
        games[str(g)] = {
            "n": n,
            "solid_rate": round(sum(1 for h in hs if any(c in SOLID for c in h["survivors"])) / n, 4),
            "kite_rate": round(sum(1 for h in hs if any(typ(c, "牵制型") for c in h["survivors"])) / n, 4),
            "support_rate": round(sum(1 for h in hs if any(typ(c, "辅助型") for c in h["survivors"])) / n, 4),
            "decode_rate": round(sum(1 for h in hs if any(typ(c, "破译型") for c in h["survivors"])) / n, 4),
            "escape_avg": round(sum(h["escape"] for h in hs) / n, 3),
            "survivor_ban_quota": 0 if g == 1 else (1 if g == 2 else 2),
            "tiebreak_n": sum(1 for h in hs if h.get("tiebreak")),
        }

    # ── 选手档案 ────────────────────────────────────────────
    by_pg, by_pt = defaultdict(Counter), defaultdict(Counter)
    for h in halves:
        g = str(h.get("game") or 4)
        by_pg[(h["hunter_player"], g)][h["hunter"]] += 1
        by_pt[h["hunter_player"]][h["hunter"]] += 1

    players = {}
    for p, info in hp["players"].items():
        dist = {canon(c): v for c, v in info["dist"].items()}
        threats = []
        for c, prob in dist.items():
            s = strength.get(c)
            if s is None:
                continue
            threats.append({"char": c, "p_use": prob,
                            "strength": round(s, 4), "threat": round(prob * s, 4)})
        threats.sort(key=lambda x: -x["threat"])

        raw = {canon(c): v for c, v in info["raw_count"].items()}
        by_game = {}
        for g in ["1", "2", "3", "4"]:
            c = by_pg.get((p, g))
            if c:
                by_game[g] = [{"char": canon(k), "n": v} for k, v in c.most_common(5)]

        players[p] = {
            "team": info["team"],
            "game": info["game"],
            "confidence": info["confidence"],
            "dist": dict(sorted(dist.items(), key=lambda x: -x[1])),
            "raw_count": dict(sorted(raw.items(), key=lambda x: -x[1])),
            "threats": threats,
            "by_game": by_game,
            # 该选手本赛季实际用过的监管角色（战队级封锁池的可选范围）
            "used_chars": sorted(by_pt.get(p, Counter())),
        }

    out = {
        "meta": {
            "season": "IVL 2026 秋季赛",
            "half_count": len(halves),
            "rule_version": "第十一版（docs/rules_v11.md）",
            "description": "BP 决策树数据：阶段序列 × 角色池 × 监管选手威胁",
            "stages": [
                {"code": "①", "actor": "监管者", "action": "ban", "side": "求生者",
                 "count": 2, "desc": "恒为 2 个，在求生方任何选择之前完成"},
                {"code": "②", "actor": "求生者", "action": "ban", "side": "监管者",
                 "count": "0/1/2", "desc": "第1局 0 个，第2局 1 个，第3局起 2 个"},
                {"code": "③", "actor": "求生者", "action": "pick", "side": "求生者",
                 "count": 2, "desc": "求生方先定 2 人"},
                {"code": "④", "actor": "监管者", "action": "ban", "side": "求生者",
                 "count": 1, "desc": "看到 ③ 的 2 人后才 ban"},
                {"code": "⑤", "actor": "求生者", "action": "pick", "side": "求生者",
                 "count": 1, "desc": "第 1 轮 ban1pick1 的 pick"},
                {"code": "⑥", "actor": "监管者", "action": "ban", "side": "求生者",
                 "count": 1, "desc": "看到 ⑤ 后才 ban"},
                {"code": "⑦", "actor": "求生者", "action": "pick", "side": "求生者",
                 "count": 1, "desc": "第 2 轮 ban1pick1 的 pick，阵容定型"},
                {"code": "⑩", "actor": "监管者", "action": "pick", "side": "监管者",
                 "count": 1, "desc": "监管方最后选人"},
            ],
            "global_lock": {
                "rule": "只封锁 pick，ban 不进池",
                "evidence": [
                    "求生 pick 跨半局重复 0/260",
                    "监管 pick 跨半局重复 0/155",
                    "监管 ban 跨半局重复 149/260 → ban 不受封锁池约束",
                ],
                "scope": "按阵营各自独立：本方用过的角色只封锁本方",
                "games": "第一至第四局新增封锁；第五局及加赛不再新增",
                "timing": "本半局结束后一次性并入",
            },
            "caveat": "战报只记录 4 个求生者 ban 的集合，①/④/⑥ 的批次无法还原，"
                      "因此 ban 统计按全部 4 个合并。",
        },
        "games": games,
        "teams": teams,
        "players": players,
        "banSurvivor": ban_all,
        "banSurvivorByGame": hb_by_game,
        "banHunter": sban_all,
        "banHunterByGame": sban_by_game,
        "strength": {c: round(v, 4) for c, v in
                     sorted(strength.items(), key=lambda x: -x[1])},
        "alphaRaw": {c: round(v, 4) for c, v in alpha.items()},
        "counterPick": counter_pick,
        "solid": sorted(SOLID),
        "positions": pos,
        "pool": {"survivor": sorted(pool_surv), "hunter": sorted(pool_hunt)},
    }

    return out


def write(out):
    path = ROOT / "data" / "bp_tree.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已生成 {path} ({path.stat().st_size / 1024:.0f} KB)")


def main():
    out = build()
    write(out)

    print()
    print("监管 ban 求生者（4 个合并，Top8）:")
    for x in out["banSurvivor"][:8]:
        print(f"  {x['char']:<10}{x['n']:>4} 次  {x['p']*100:5.1f}%")
    print()
    print("求生 ban 监管（② 阶段，Top8）:")
    for x in out["banHunter"][:8]:
        print(f"  {x['char']:<10}{x['n']:>4} 次  {x['p']*100:5.1f}%")
    print()
    players = out["players"]
    first = next(iter(players), None)
    if first:
        print(f"威胁度示例 {first}:")
        for t in players[first]["threats"][:5]:
            print(f"  {t['char']:<10} {t['p_use']*100:5.1f}% × "
                  f"{t['strength']:.3f} = {t['threat']:.4f}")


if __name__ == "__main__":
    main()
