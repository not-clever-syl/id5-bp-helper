# -*- coding: utf-8 -*-
"""从 bp_core.json 重算所有派生数据。

页面改完原始数据后调用这里，一步把下游全部刷新，不用手工逐个跑脚本。

依赖链（每一步的输出是下一步的输入）：

    bp_core.json
      └─ adjusted_scores.json    选手熟练度修正 + 折合得分
           └─ hunter_players.json  监管选手角色分布
                ├─ bp_query.json    战队 × 求生 × 监管 分数表
                │    └─ bp_tree.json  决策树（角色池、威胁、ban 统计）
                │         ├─ player_scores.json  选手榜
                │         └─ web_bp.json         前端动态计算数据
                └─ （query 也吃 adjusted）

只有「角色图鉴」是静态的：positions（求生类型）、软/中/硬辅分类、
SOLID 救人位名单。这些不随比赛结果变化，重建时原样保留。

用法：
    python scripts/rebuild.py          # 全部重建
    python scripts/rebuild.py tree     # 只跑某几步（名字见 STEPS）
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "build"))

# 每一步：名字 → 返回该构建结果的函数（不含落盘）
_STEPS = ("adjusted", "hunter_players", "query", "tree",
          "player_scores", "web_data", "presets")


def rebuild(only: list | None = None) -> dict:
    """重算派生数据。

    only 给定时只跑列出的步骤（仍会先把上游读进内存）。
    返回 {步骤名: 耗时秒}。
    """
    import json

    import adjusted
    import hunter_players
    import query
    import tree
    import player_scores
    import web_data
    import presets

    want = set(only) if only else set(_STEPS)
    unknown = want - set(_STEPS)
    if unknown:
        raise ValueError(f"未知步骤：{sorted(unknown)}")

    # 各模块每次调用都直接读盘，这里不需要再清任何缓存
    core = json.loads((ROOT / "data" / "bp_core.json").read_text(encoding="utf-8"))
    timing = {}

    def run(name, fn):
        t0 = time.time()
        fn()
        timing[name] = round(time.time() - t0, 3)

    # 1. 折合得分
    adj_out = adjusted.build(core)
    if "adjusted" in want:
        run("adjusted", lambda: adjusted.write(adj_out))

    # 2. 选手角色分布
    hp_out = hunter_players.build(core)
    if "hunter_players" in want:
        run("hunter_players", lambda: hunter_players.write(hp_out))

    # 3. 查询视图（吃 adjusted）
    q_out, _ = query.build(core, adj_out)
    if "query" in want:
        run("query", lambda: query.write(q_out))

    # 4. 决策树（吃 hunter_players + adjusted）。
    #    图鉴（positions/pool）从现有 bp_tree.json 兜底读取，保持静态。
    tree_out = tree.build(core, hp_out, adj_out)
    if "tree" in want:
        run("tree", lambda: tree.write(tree_out))

    # 5. 选手榜（吃 adjusted + tree）
    ps_out = player_scores.build(core, adj_out, tree_out)
    if "player_scores" in want:
        def _ps():
            player_scores._out_cache = ps_out
            player_scores.write(ps_out)
        run("player_scores", _ps)

    # 6. 前端数据。web_data 通过 Engine 读磁盘的 tree/query，
    #    所以先把这两份落盘（无论是否在 only 里），再构建。
    def _web():
        tree.write(tree_out)
        query.write(q_out)
        web_data.main()
    if "web_data" in want:
        run("web_data", _web)

    # 7. 可导入配置（data/hunter/、data/survivor/）。
    #    同样吃磁盘上的 tree/query/player_scores，先确保落盘。
    if "presets" in want:
        def _presets():
            tree.write(tree_out)
            query.write(q_out)
            player_scores._out_cache = ps_out
            player_scores.write(ps_out)
            presets.write(
                presets.build_hunter_configs(q_out["scores"], ps_out, tree_out),
                presets.build_survivor_configs(tree_out),
            )
        run("presets", _presets)

    return timing


def main(argv: list) -> None:
    import json

    only = [a for a in argv[1:] if not a.startswith("-")]
    if "--json" in argv:
        print(json.dumps(rebuild(only or None), ensure_ascii=False))
        return
    print("重建派生数据……")
    timing = rebuild(only or None)
    total = sum(timing.values())
    for name in _STEPS:
        if name in timing:
            print(f"  {name:<16}{timing[name]:>7.3f}s")
    print(f"  {'合计':<15}{total:>7.3f}s")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main(sys.argv)
