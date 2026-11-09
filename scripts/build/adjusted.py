# -*- coding: utf-8 -*-
"""折合得分计算（对策卡分离版）。

════════════════════════════════════════════════════════════════
一、为什么要分离对策卡
════════════════════════════════════════════════════════════════
监管角色按出场频次分为两类：

  主力角色  出场 ≥ MIN_MAIN_N 场（本项目为 20），是选手的常规选择。
            其得分主要由「角色强度 + 选手熟练度」决定，适合用双因素模型。

  对策卡    出场 < MIN_MAIN_N 场，是为了针对求生阵容弱点而临时选出。
            其得分主要由「阵容匹配度」决定，与选手熟练度的关系弱。
            实测：对策卡全局求生均分 1.58，主力卡 1.68，差异仅 0.10，
            说明这些卡并没有稳定压制效果；且总计只有 44 场（16.9%），
            混入估计只会污染 α/β。

因此：对策卡不参与 α/β 估计，其场次的折合得分直接等于原始得分。

════════════════════════════════════════════════════════════════
二、模型（仅对主力角色）
════════════════════════════════════════════════════════════════
    y_k = μ + α_{c(k)} + β_{p(k), c(k)} + ε_k

    μ        全局求生均分
    α_c      监管角色强度（负 = 强，求生难得分）
    β_{p,c}  选手 p 使用角色 c 的熟练度效应（正 = 打得差）
    ε_k      求生方真实表现

识别约束：对每个角色 c，Σ_p β_{p,c} = 0
    → α_c 代表「行业标准水平」，β 是相对它的偏离。

════════════════════════════════════════════════════════════════
三、估计：迭代双因素分解 + 经验贝叶斯收缩
════════════════════════════════════════════════════════════════
    1. α_c ← mean(y - μ - β)，仅在主力角色间中心化
    2. β_raw ← mean(y - μ - α)
       β     ← β_raw · n/(n+λ)，再按角色中心化
    重复至收敛。

    λ = 3：数据中 (选手,角色) 组合的 n 中位数约为 2，
           n=1 权重 25%、n=3 权重 50%、n=7 权重 70%，
           避免单场结果产生极端权重。

════════════════════════════════════════════════════════════════
四、折合得分
════════════════════════════════════════════════════════════════
    adjust(p,c) = clip(-β_{p,c}, -MAX_ADJUST, +MAX_ADJUST)
    adjusted    = y + adjust

含义：该选手用这个角色时比行业标准选手让求生多得 0.4 分，
则 adjust = -0.4，表示「这局求生的原始得分被高估了，应下调」。

聚合到 (战队, 求生角色, 监管角色)：
    折合得分 = mean(adjusted)
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
LAMBDA = 3.0         # 选手-角色效应的收缩强度
MAX_ADJUST = 0.5     # 单局修正量上限
MIN_MAIN_N = 20      # 主力角色的最少出场次数，低于此归为对策卡
MAX_ITER = 500
TOL = 1e-8


def load_rows(core=None):
    if core is None:
        core = json.loads((ROOT / "data" / "bp_core.json").read_text(encoding="utf-8"))

    rows = []
    for r in core["halves"]:
        rows.append({
            "survivor_team": r["survivor_team"],
            "hunter_team": r["hunter_team"],
            "hunter_char": r["hunter"],
            "hunter_player": r.get("hunter_player"),
            "survivors": r["survivors"],
            # 评分单位：局均逃脱人数（0~4），不是原始比分
            "y": r["escape"],
        })
    return rows


def fit(rows):
    """只在主力角色上做双因素分解。"""
    mu = sum(r["y"] for r in rows) / len(rows)

    by_char = defaultdict(list)
    by_pc = defaultdict(list)
    for r in rows:
        by_char[r["hunter_char"]].append(r)
        by_pc[(r["hunter_player"], r["hunter_char"])].append(r)

    main_chars = {c for c, rs in by_char.items() if len(rs) >= MIN_MAIN_N}
    main_rows = [r for r in rows if r["hunter_char"] in main_chars]

    mu_main = sum(r["y"] for r in main_rows) / len(main_rows)

    alpha = {c: 0.0 for c in main_chars}
    beta = defaultdict(float)

    it = 0
    for it in range(MAX_ITER):
        new_alpha = {}
        for c in main_chars:
            rs = by_char[c]
            resid = sum(r["y"] - mu_main - beta[(r["hunter_player"], c)] for r in rs)
            new_alpha[c] = resid / len(rs)
        a_mean = sum(new_alpha.values()) / len(new_alpha)
        new_alpha = {c: v - a_mean for c, v in new_alpha.items()}

        raw = {}
        for (p, c), rs in by_pc.items():
            if c not in main_chars:
                continue
            resid = sum(r["y"] - mu_main - new_alpha[c] for r in rs)
            n = len(rs)
            raw[(p, c)] = (resid / n) * n / (n + LAMBDA)

        per_char = defaultdict(list)
        for (p, c), v in raw.items():
            per_char[c].append(v)
        new_beta = {}
        for (p, c), v in raw.items():
            vals = per_char[c]
            mean_c = sum(vals) / len(vals) if len(vals) > 1 else 0.0
            new_beta[(p, c)] = v - mean_c

        delta = max(
            max((abs(new_alpha[c] - alpha[c]) for c in new_alpha), default=0.0),
            max((abs(new_beta[k] - beta[k]) for k in new_beta), default=0.0),
        )
        alpha, beta = new_alpha, new_beta
        if delta < TOL:
            break

    return mu, mu_main, alpha, beta, by_pc, by_char, main_chars, it + 1


def build(core=None):
    """返回折合得分字典。core 不给就读磁盘。"""
    rows = load_rows(core)
    mu, mu_main, alpha, beta, by_pc, by_char, main_chars, iters = fit(rows)
    counter_chars = sorted(set(by_char) - main_chars)

    # 每个 (p,c) 的修正量
    adjust = {}
    for (p, c), rs in by_pc.items():
        n = len(rs)
        if c not in main_chars:
            adjust[(p, c)] = {
                "n": n, "beta": 0.0, "adjust": 0.0, "adjust_unclipped": 0.0,
                "clipped": False, "shrink_weight": 0.0, "counter_pick": True,
            }
            continue
        raw_adj = -beta[(p, c)]
        clipped = max(-MAX_ADJUST, min(MAX_ADJUST, raw_adj))
        adjust[(p, c)] = {
            "n": n,
            "beta": round(beta[(p, c)], 4),
            "adjust": round(clipped, 4),
            "adjust_unclipped": round(raw_adj, 4),
            "clipped": abs(raw_adj) > MAX_ADJUST,
            "shrink_weight": round(n / (n + LAMBDA), 3),
            "counter_pick": False,
        }

    # 修正每个半局
    for r in rows:
        a = adjust.get((r["hunter_player"], r["hunter_char"]))
        r["adjust"] = a["adjust"] if a else 0.0
        r["adj_y"] = r["y"] + r["adjust"]

    # 聚合
    agg = defaultdict(list)
    for r in rows:
        for s in r["survivors"]:
            agg[(r["survivor_team"], s, r["hunter_char"])].append((r["y"], r["adj_y"]))

    table = {}
    for (team, s, c), vs in agg.items():
        n = len(vs)
        raw_mean = sum(v[0] for v in vs) / n
        adj_mean = sum(v[1] for v in vs) / n
        conf = "high" if n >= 3 else "medium" if n == 2 else "low"
        table.setdefault(team, {}).setdefault(s, {})[c] = {
            "n": n,
            "raw": round(raw_mean, 3),
            "adjusted": round(adj_mean, 3),
            "delta": round(adj_mean - raw_mean, 3),
            "confidence": conf,
            "counter_pick": c not in main_chars,
        }

    out = {
        "meta": {
            "description": "战队求生角色 × 监管角色 的折合得分",
            "baseline": "行业标准水平的监管选手（主力角色内选手熟练度均值为零）",
            "lambda_player": LAMBDA,
            "max_adjust": MAX_ADJUST,
            "min_main_n": MIN_MAIN_N,
            "iterations": iters,
            "mu": round(mu, 4),
            "mu_main_subset": round(mu_main, 4),
            "halves": len(rows),
            "main_chars": sorted(main_chars),
            "counter_pick_chars": counter_chars,
            "score_formula": "adjusted = raw + adjust(p,c), adjust = clip(-beta)",
            "counter_pick_rule": "对策卡不参与 α/β 估计，adjust 恒为 0",
        },
        "hunter_char_effect": {
            c: {"alpha": round(v, 4), "n": len(by_char[c]), "estimated": True}
            for c, v in sorted(alpha.items(), key=lambda x: x[1])
        },
        "counter_pick_chars": {
            c: {"n": len(by_char[c])} for c in counter_chars
        },
        "player_char_adjust": {f"{p}@{c}": v for (p, c), v in sorted(adjust.items())},
        "adjusted_scores": table,
    }
    return out


def write(out):
    OUT = ROOT / "data" / "adjusted_scores.json"
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写入 {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


def main():
    out = build()
    write(out)
    m = out["meta"]
    print(f"半局数 {m['halves']}   迭代 {m['iterations']} 次收敛")
    print(f"全局求生均分 μ = {m['mu']:.3f}   （主力角色子集 μ = {m['mu_main_subset']:.3f}）")
    print(f"收缩强度 λ = {m['lambda_player']}   单局修正上限 = ±{m['max_adjust']}")
    print(f"主力角色 {len(m['main_chars'])} 个：{m['main_chars']}")
    print(f"对策卡   {len(m['counter_pick_chars'])} 个：{m['counter_pick_chars']}")
    print()
    print("监管角色强度（α 越负 = 越强）:")
    for c, v in sorted(out["hunter_char_effect"].items(),
                       key=lambda x: x[1]["alpha"]):
        print(f"  {c:<8} α={v['alpha']:+.3f}  n={v['n']}")


if __name__ == "__main__":
    main()
