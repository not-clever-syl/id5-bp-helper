# -*- coding: utf-8 -*-
"""bp_core 原始数据的逻辑校验。

保存前用这里查一遍，把明显不合常理的数据挡下来。
分两层：

    基础层   单行自身：字段齐不齐、比分和逃脱人数对不对得上、角色有没有重复
    结构层   整场一起看：上下半局是不是互换的、同局地图是不是一致、
             局次是不是递增、ban 名额对不对

结构层需要先按 seq 把行切成「一场」——seq 每场都从 1 重新开始，
所以 seq 变小就说明上一场结束了。

每条问题都带行号，页面上据此把不合逻辑的行标黄。

用法：
    python scripts/core_check.py            # 查当前 data/bp_core.json
    python scripts/core_check.py 文件.json   # 查指定文件
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "data" / "bp_core.json"

# 求生方 ban 监管的名额：第 1 局 0 个，第 2 局 1 个，第 3 局起 2 个
BAN_QUOTA = {1: 0, 2: 1}
DEFAULT_QUOTA = 2


def split_matches(halves: list) -> list:
    """按 seq 把行切成一场场。seq 每场从 1 重新开始。

    返回 [[(行号, 半局), ...], ...]
    """
    out, cur = [], []
    for i, x in enumerate(halves):
        if cur and x.get("seq", 0) <= cur[-1][1].get("seq", 0):
            out.append(cur)
            cur = []
        cur.append((i, x))
    if cur:
        out.append(cur)
    return out


def check(halves: list, meta: dict) -> list:
    """返回问题列表。

    每条是 {"rows": [行号...], "tag": "第 1 行", "msg": "..."}。
    行号从 0 开始，对应 halves 数组下标。
    空列表表示没问题。
    """
    issues = []
    s2e = {str(k): int(v) for k, v in
           (meta.get("score_to_escape") or {}).items()}
    if not s2e:
        s2e = {"0": 0, "1": 1, "2": 2, "3": 3, "5": 4}

    def add(rows, tag, msg):
        issues.append({"rows": list(rows), "tag": tag, "msg": msg})

    # ── 基础层：一行自身 ───────────────────────────────
    for i, h in enumerate(halves):
        tag = f"第 {i + 1} 行"
        if not isinstance(h, dict):
            add([i], tag, "不是对象")
            continue
        for f in ("seq", "side", "survivor_team", "hunter_team", "map"):
            if not h.get(f):
                add([i], tag, f"缺少 {f}")
        if h.get("side") not in ("upper", "lower"):
            add([i], tag, f"side={h.get('side')} 不合法（应为 upper / lower）")
        if h.get("tiebreak"):
            if h.get("game") not in (4, 5):
                add([i], tag, f"标了加时，game={h.get('game')} 应为 4 或 5")
        elif h.get("game") not in (1, 2, 3, 4, 5):
            add([i], tag, f"game={h.get('game')} 不合法（应为 1~5）")

        sb, hb = h.get("survivors"), h.get("hunter_bans")
        if not isinstance(sb, list) or len(sb) != 4:
            add([i], tag, "求生阵容必须正好 4 个")
        if not isinstance(hb, list) or len(hb) != 4:
            add([i], tag, "求生 ban 位必须正好 4 个")
        if isinstance(sb, list) and len(sb) == 4:
            if len(set(sb)) != 4:
                add([i], tag, "求生阵容有重复角色")
            if isinstance(hb, list) and len(hb) == 4:
                both = set(sb) & set(hb)
                if both:
                    add([i], tag,
                        f"角色既上场又被 ban：{'、'.join(sorted(both))}")
        if isinstance(hb, list) and len(hb) == 4 and len(set(hb)) != 4:
            add([i], tag, "求生 ban 位有重复角色")
        sbans = h.get("survivor_bans") or []
        if len(set(sbans)) != len(sbans):
            add([i], tag, "监管 ban 位有重复角色")
        if h.get("hunter") and h.get("hunter") in sbans:
            add([i], tag, f"监管角色 {h['hunter']} 既出场又被 ban")
        for ch in (sb or []) + (hb or []) + sbans:
            if not isinstance(ch, str) or not ch.strip():
                add([i], tag, "存在空的角色名")
                break

        score = str(h.get("score") or "")
        parts = score.split(":")
        if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
            add([i], tag, f"比分 {score!r} 格式不对（应为 求生:监管）")
        else:
            a, b = parts[0].strip(), parts[1].strip()
            if a not in s2e or b not in s2e:
                add([i], tag, f"比分 {score} 不在允许取值内")
            else:
                if h.get("escape") != s2e[a] or h.get("kill") != s2e[b]:
                    add([i], tag, f"escape/kill 与比分 {score} 对不上"
                                    f"（应为 {s2e[a]}/{s2e[b]}）")

    # ── 结构层：整场一起看 ───────────────────────────
    for mi, m in enumerate(split_matches(halves)):
        if not m:
            continue
        rows = [i for i, _ in m]
        a0 = m[0][1]
        tag = (f"第 {mi + 1} 场"
               f"({a0.get('survivor_team')} vs {a0.get('hunter_team')})")

        seqs = [x.get("seq") for _, x in m]
        if seqs != list(range(1, len(m) + 1)):
            add(rows, tag, f"seq 不连续：{seqs}")
        # 同一场里 seq 不能重复（否则上下半局配对就乱了）
        dup = [s for s in set(seqs) if seqs.count(s) > 1]
        if dup:
            add(rows, tag, f"seq 重复：{sorted(dup)}")

        # 每两行是一局：upper 在前，两行队伍互换、地图相同、game 相同
        for k in range(0, len(m) - 1, 2):
            ia, a = m[k]
            ib, b = m[k + 1]
            sa, sb_ = a.get("seq"), b.get("seq")
            if a.get("side") != "upper":
                add([ia, ib], tag, f"seq {sa} 应该是 upper，却是 {a.get('side')}")
            if a.get("side") == b.get("side"):
                add([ia, ib], tag, f"seq {sa}/{sb_} 上下半局 side 相同")
            if a.get("survivor_team") != b.get("hunter_team") or \
                    a.get("hunter_team") != b.get("survivor_team"):
                add([ia, ib], tag, f"seq {sa}/{sb_} 同局两行的队伍没互换")
            if a.get("game") != b.get("game"):
                add([ia, ib], tag, f"seq {sa}/{sb_} 同局 game 不一致"
                                   f"（{a.get('game')} vs {b.get('game')}）")
            if a.get("map") != b.get("map"):
                add([ia, ib], tag, f"seq {sa}/{sb_} 同局两行地图不同"
                                   f"（{a.get('map')} vs {b.get('map')}）")

        # 局次递增
        games = [x.get("game") for _, x in m if x.get("game")]
        if games != sorted(games):
            add(rows, tag, f"局次不是递增：{games}")

        # 加时最多一局（两个 half）
        tb = [(i, x) for i, x in m if x.get("tiebreak")]
        if len(tb) > 2:
            add([i for i, _ in tb], tag, f"加时超过一局（{len(tb)} 个 half）")

        # 求生 ban 监管的名额
        for i, x in m:
            if x.get("tiebreak"):
                continue
            g = x.get("game")
            want = BAN_QUOTA.get(g, DEFAULT_QUOTA)
            got = len(x.get("survivor_bans") or [])
            if got != want:
                add([i], tag, f"seq {x.get('seq')} 第 {g} 局求生 ban 监管 "
                              f"{got} 个，应为 {want} 个")

    # ── 主客映射 ────────────────────────────────────────
    hp = meta.get("home_pairs")
    if hp is not None:
        if not isinstance(hp, dict):
            add([], "meta.home_pairs", "必须是对象")
        else:
            for k, v in hp.items():
                parts = [p for p in str(k).replace("|", ",").split(",") if p]
                if len(parts) != 2:
                    add([], "meta.home_pairs", f"键 {k!r} 不是两个队名")
                elif v not in parts:
                    add([], "meta.home_pairs", f"{k!r} 的主队 {v!r} 不在这两队里")

    return issues


def issues_text(issues: list) -> list:
    """把问题列表转成给人看的句子。"""
    return [f"{it['tag']}：{it['msg']}" for it in issues]


def bad_rows(issues: list) -> dict:
    """行号 → 该行的所有问题描述。"""
    out = {}
    for it in issues:
        for r in it["rows"]:
            out.setdefault(r, []).append(it["msg"])
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else CORE
    d = json.loads(path.read_text(encoding="utf-8"))
    halves = d.get("halves") or []
    meta = d.get("meta") or {}
    issues = check(halves, meta)
    print(f"{path}：{len(halves)} 行，{len(split_matches(halves))} 场")
    if not issues:
        print("逻辑检查通过，没发现问题。")
        return
    print(f"发现 {len(issues)} 个问题：")
    for s in issues_text(issues):
        print("  -", s)


if __name__ == "__main__":
    main()
