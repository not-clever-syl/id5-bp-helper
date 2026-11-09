# -*- coding: utf-8 -*-
"""监管选手的角色使用分布（只按选手，贝叶斯收缩 + 信度标记）。

════════════════════════════════════════════════════════════════
为什么要收缩
════════════════════════════════════════════════════════════════
本赛段共 18 位监管选手，样本量差距极大：

    YuM 30 半局 / Qiu 25 / zz9 24 / Tear 24 / ppei 19 / Jin 18
    ...
    moonlight 3 / Nokino 3 / YuZ 1

若直接用原始频率，YuZ 只打了 1 局，就会显示「100% 用牙医」，
这是噪声而不是信号。因此像 build_adjusted.py 处理选手-角色效应那样，
用全联盟的监管角色使用率做先验，按样本量收缩：

    P(p, c) = (n_pc + K * P_global(c)) / (n_p + K)

    n_pc        选手 p 使用角色 c 的半局数
    n_p        选手 p 的总半局数
    P_global(c) 全联盟监管角色使用率
    K           收缩强度，取 5

    K = 5 的含义：选手打满 5 局时，先验与实测各占一半；
    打满 15 局时实测占 75%；打满 30 局时实测占 86%。

════════════════════════════════════════════════════════════════
信度等级
════════════════════════════════════════════════════════════════
    reliable      n >= 18   可以按分布做针对性准备
    weak          10 <= n < 18  有方向性，但不宜过度拟合
    insufficient  n < 10     基本等同于全联盟先验，仅供参考

输出：data/hunter_players.json
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
sys.path.insert(0, str(ROOT / "scripts"))
K_SHRINK = 5.0          # 收缩强度
N_RELIABLE = 18
N_WEAK = 10


def load(core=None):
    if core is None:
        core = json.loads((ROOT / "data" / "bp_core.json").read_text(encoding="utf-8"))
    return core["halves"]


def confidence(n):
    if n >= N_RELIABLE:
        return "reliable"
    if n >= N_WEAK:
        return "weak"
    return "insufficient"


def build(core=None):
    """返回选手分布字典。core 不给就读磁盘。"""
    halves = load(core)
    total = len(halves)

    # ── 全局先验：全联盟各监管角色的使用率 ──────────────────
    global_count = Counter(h["hunter"] for h in halves)
    p_global = {c: n / total for c, n in global_count.items()}

    # ── 按选手汇总 ──────────────────────────────────────────
    by_player = defaultdict(list)
    for h in halves:
        by_player[h["hunter_player"]].append(h)

    players = {}
    for p, hs in sorted(by_player.items(), key=lambda x: -len(x[1])):
        n = len(hs)
        raw = Counter(h["hunter"] for h in hs)
        # 收缩：向全联盟先验收缩
        dist = {}
        for c in global_count:
            p_post = (raw.get(c, 0) + K_SHRINK * p_global[c]) / (n + K_SHRINK)
            dist[c] = round(p_post, 4)
        # 只保留收缩后占比 >= 3% 的，避免输出一堆噪声
        dist = {c: v for c, v in sorted(dist.items(), key=lambda x: -x[1]) if v >= 0.03}

        teams = Counter(h["hunter_team"] for h in hs)
        players[p] = {
            "game": n,
            "team": teams.most_common(1)[0][0],
            "teams": dict(teams),
            "confidence": confidence(n),
            "raw_count": dict(raw),
            "dist": dist,
            "top": list(dist)[0] if dist else None,
        }

    return {
        "meta": {
            "description": "监管选手的角色使用分布（贝叶斯收缩，向全联盟先验收缩）",
            "source": "data/bp_core.json",
            "season": "IVL 2026 秋季赛",
            "half_count": total,
            "player_count": len(players),
            "k_shrink": K_SHRINK,
            "confidence_rule": f"reliable n>={N_RELIABLE}, weak {N_WEAK}<=n<{N_RELIABLE}, "
                               f"insufficient n<{N_WEAK}",
            "notes": [
                "dist 为收缩后的概率，已剔除占比 <3% 的角色。",
                "raw_count 为原始使用次数，供对照。",
                "小样本选手（n<10）的 dist 基本等于全联盟先验，不可当作个人偏好。",
            ],
        },
        "global_dist": {c: round(v, 4) for c, v in
                        sorted(p_global.items(), key=lambda x: -x[1])},
        "players": players,
    }


def write(out):
    path = ROOT / "data" / "hunter_players.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已生成 {path}")


def main():
    out = build()
    write(out)
    halves_n = out["meta"]["half_count"]
    global_count = {c: round(v * halves_n) for c, v in out["global_dist"].items()}
    print(f"半局总数 {halves_n}，监管角色 {len(global_count)} 个")
    print("全联盟监管角色使用率（先验）:")
    for c, n in sorted(global_count.items(), key=lambda x: -x[1]):
        print(f"  {c:<10}{n:>3.0f}  {n/halves_n*100:5.1f}%")
    print()
    print("=" * 74)
    print("选手角色使用分布（收缩后）")
    print("=" * 74)
    for p, info in sorted(out["players"].items(), key=lambda x: -x[1]["game"]):
        n = info["game"]
        raw = info["raw_count"]
        print(f"\n  {p}  ({info['team']}, {n} 半局, {info['confidence']})")
        top_raw = max(raw.items(), key=lambda x: x[1]) if raw else ("—", 0)
        print(f"    原始最多: {top_raw[0]} {top_raw[1]}/{n}")
        for c, v in list(info["dist"].items())[:6]:
            r = raw.get(c, 0)
            bar = "█" * int(v * 40)
            print(f"      {c:<10}{v*100:5.1f}%  (原始 {r:>2})  {bar}")
    c = Counter(v["confidence"] for v in out["players"].values())
    print()
    print(f"  信度: reliable {c['reliable']} 人, weak {c['weak']} 人, "
          f"insufficient {c['insufficient']} 人")


if __name__ == "__main__":
    main()
