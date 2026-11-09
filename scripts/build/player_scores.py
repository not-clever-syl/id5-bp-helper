# -*- coding: utf-8 -*-
"""按监管选手聚合分数：每个 (求生角色, 该选手用过的监管) 的实测 / 全局 / 折合。

════════════════════════════════════════════════════════════════
口径（严格对齐 build_bp_query.py，只是把聚合维度从「战队」换成「监管选手」）
════════════════════════════════════════════════════════════════
一次半局能给 N 个格子各贡献 1 个样本：该半局 N 名求生者，每个角色
都记这一次的整队逃脱人数 adj_y（战报只有整队分，没有分角色分）。

    选手实测  own  = 该选手用该监管的局里，出现过该角色的局，
                      这些局 adj_y 的均值
    全局      glob = 全联盟同样算法（不分选手）
    折合      score = w*own + (1-w)*glob，w = n/(n+K_SHRINK)

n = 该选手该格子的样本数。

════════════════════════════════════════════════════════════════
为什么选手榜要单独算
════════════════════════════════════════════════════════════════
同一个监管角色，不同选手打出来的方向可能完全相反：
有人用鹿头能被 2 逃，有人用鹿头能四抓。总榜把这些人混在一起，
等于假设「只要是鹿头就一样」，教练就看不到对手真正的长板。
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent.parent
K_SHRINK = 3.0

_out_cache: dict | None = None


def _load_rows(core=None, adj=None):
    """半局 → [(求生角色集, 监管角色, 选手, 逃脱人数)]。"""
    if core is None:
        core = json.loads((ROOT / "data" / "bp_core.json").read_text(encoding="utf-8"))
    # 选手熟练度修正（与 build_bp_query.py 同一份）
    if adj is None:
        adj = json.loads((ROOT / "data" / "adjusted_scores.json").read_text(encoding="utf-8"))
    adj_map = {k: v.get("adjust", 0.0)
               for k, v in (adj.get("player_char_adjust") or {}).items()}

    rows = []
    for h in core["halves"]:
        y = h["escape"]              # 逃脱人数口径（0~4）
        player = h.get("hunter_player")
        hc = h["hunter"]
        y_adj = y + adj_map.get(f"{player}@{hc}", 0.0)
        rows.append({
            "chars": list(h["survivors"]),
            "hunter": hc,
            "player": player,
            "team": h["survivor_team"],
            "y": y_adj,
            "game": h.get("game"),
        })
    return rows


def build(core=None, adj=None, tree=None) -> dict:
    """返回 {"players": {...}, "global": {...}, "meta": {...}}"""
    global _out_cache
    if core is None and adj is None and tree is None and _out_cache is not None:
        return _out_cache

    rows = _load_rows(core, adj)

    # 全局 (角色, 监管)
    g_acc = defaultdict(list)
    for r in rows:
        for s in r["chars"]:
            g_acc[(s, r["hunter"])].append(r["y"])
    glob = {k: sum(v) / len(v) for k, v in g_acc.items()}
    glob_n = {k: len(v) for k, v in g_acc.items()}

    # 选手 (选手, 角色, 监管)
    p_acc = defaultdict(list)
    p_halves = defaultdict(int)
    p_used = defaultdict(set)
    for r in rows:
        p = r["player"]
        if not p:
            continue
        p_halves[p] += 1
        p_used[p].add(r["hunter"])
        for s in r["chars"]:
            p_acc[(p, s, r["hunter"])].append(r["y"])

    players = {}
    for (p, s, h), vs in p_acc.items():
        n = len(vs)
        own = sum(vs) / n
        g = glob.get((s, h))
        if g is None:
            score, w = own, 1.0
        else:
            w = n / (n + K_SHRINK)
            score = w * own + (1 - w) * g
        players.setdefault(p, {}).setdefault(s, {})[h] = {
            "own": round(own, 3),
            "n": n,
            "global": round(g, 3) if g is not None else None,
            "global_n": glob_n.get((s, h)),
            "weight": round(w, 3),
            "score": round(score, 3),
        }

    # 只保留角色池并集内的求生：角色池外的角色没有行可挂，留着只会多出空行
    import custom_data
    keep = set(custom_data.all_survivors(tree))
    if tree is None:
        tree = json.loads((ROOT / "data" / "bp_tree.json").read_text(encoding="utf-8"))

    # 行顺序：按该选手所属战队的角色池顺序，池外角色排在后面按图鉴顺序
    gallery = {c: i for i, c in enumerate(tree["pool"]["survivor"])}
    team_order = {
        t: {c: i for i, c in enumerate(v["roster"])}
        for t, v in tree["teams"].items()
    }

    def order_of(p):
        # 选手→队伍：roster（自定义名单）优先，bp_tree 兜底
        try:
            import roster_store
            team = next((x["team"] for x in roster_store.load()["players"]
                         if x["name"] == p and x["team"]), None)
        except Exception:
            team = None
        if not team:
            team = tree["players"][p].get("team")
        order = team_order.get(team, {})
        return lambda c: (order.get(c, 10 ** 6), gallery.get(c, 10 ** 6))

    trimmed = {}
    for p, chars in players.items():
        rows_p = {c: v for c, v in chars.items() if c in keep}
        if not rows_p:
            continue
        trimmed[p] = dict(sorted(rows_p.items(), key=lambda kv: order_of(p)(kv[0])))

    # 列顺序：该选手用得最多的监管在前，次数相同按图鉴顺序
    hunt_idx = {c: i for i, c in enumerate(tree["pool"]["hunter"])}
    count = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if r["player"]:
            count[r["player"]][r["hunter"]] += 1
    player_hunters = {
        p: sorted(count[p], key=lambda h: (-count[p][h], hunt_idx.get(h, 10 ** 6)))
        for p in trimmed
    }

    out = {
        "meta": {
            "k_shrink": K_SHRINK,
            "formula": "score = w*own + (1-w)*global, w = n/(n+K)",
            "note": "own 为选手实测均值，n 为该选手该格子的样本数；"
                    "战报只有整队逃脱数，因此一局里每个出场角色各记一次同分。",
            "halves": len(rows),
        },
        "global": {f"{s}|{h}": {"score": round(v, 3), "n": glob_n[(s, h)]}
                   for (s, h), v in glob.items()},
        "players": trimmed,
        "player_halves": {p: p_halves[p] for p in trimmed},
        "player_hunters": player_hunters,
    }
    _out_cache = out
    return out


def main():
    d = build()
    write(d)
    print(f"  半局 {d['meta']['halves']}  选手 {len(d['players'])}  "
          f"全局格子 {len(d['global'])}")
    for p in sorted(d["players"], key=lambda x: -d["player_halves"][x])[:5]:
        n = sum(len(v) for v in d["players"][p].values())
        print(f"  {p:<10} {d['player_halves'][p]:>2} 半局  "
              f"{len(d['players'][p])} 个角色  {n} 个格子")


def write(d) -> None:
    out = ROOT / "data" / "player_scores.json"
    out.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    print(f"已写入 {out}（{out.stat().st_size/1024:.0f} KB）")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
