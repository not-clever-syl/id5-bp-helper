# -*- coding: utf-8 -*-
"""BP 搜索引擎：α-β 剪枝 + 候选裁剪 + 中间两人评估。

════════════════════════════════════════════════════════════════
设计依据（均已在历史数据上验证）
════════════════════════════════════════════════════════════════
1. 叶子评估 = 4 个求生角色各自对该监管的局均逃脱，
   取最低 3 个求均值（监管在局内会针对最弱的求生者），
   再按选手威胁前 3 的监管加权求期望（见 expected_vs_player）。
   口径验证见 scripts/verify_leaf_metric.py / verify_leaf_ranking.py：
   MAE 0.430 / RMSE 0.566 / r 0.815 / 方向命中 56.5%。

2. 但纯逃脱分会系统性低估稳救角色（佣兵/大副/守墓人）：它们分走了
   救援和牵制资源，贡献藏在「救人成功」里而非逃脱里。实测无稳救阵容
   局均逃脱 0.86，有稳救 1.68。因此加入结构项：
       恰好 1 个稳救          +0.00
       0 个稳救               -0.45
       2 个及以上稳救         -0.25
       无牵制型               -0.30   （260/260 半局都带牵制型）

3. 监管方在 ⑩ 按选手分布取期望（hunter_players.json，贝叶斯收缩 K=5），
   不做 min 最坏情况。对策卡（出场 < 20）不进入期望，单独列出。

4. 候选裁剪：① 取本队角色池被 ban 率前 6；② 取选手威胁前 4；
   ③⑤⑦ 为本队角色池（bp_tree.json 的赛季出场统计，
   本次生成可由 pool_override 覆盖）；
   ④⑥ 针对已选阵容动态生成（见 ban_candidates_response）。
   pick 阶段的全展开约 10^18 个节点，必须裁剪。

5. 地图：只改变「对手更可能拿哪个监管」的权重，不改任何格子的分数。
   地图倍数 = (该图该监管次数 + K×全局份额) / (该图局数 + K) ÷ 全局份额，
   K = MAP_SHRINK_K。它同时作用于叶子评估的两个位置：
       排序（谁进前 3）和 权重（用使用次数加权）。
   实测（5 折，260 半局）：top1 0.265 → 0.358，top3 0.519 → 0.573，
   逃脱分 MAE 0.8214 → 0.7983；打乱地图 100 次，真地图排在前 1%。
   注意两点（都是实测结论，别改回去）：
     · 地图必须参与「排序」，只改权重则前 3 的构成不变，等于没接；
     · 收缩的基线是全局份额（17 个监管平均 0.059），不是 0.5。

════════════════════════════════════════════════════════════════
量纲说明
════════════════════════════════════════════════════════════════
分数单位是「局均逃脱人数」，值域 0~4，2.00 为平局线。
求生方最大化，监管方最小化。
"""
from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SOLID = {"守墓人", "大副", "佣兵", "前锋", "野人", "逃脱大师",
         "骑士", "记者", "哭泣小丑"}
KITE_TYPES = {"牵制型"}

# 硬辅：能在关键时刻打断监管节奏（眩晕、位移、强制换追）的角色。
# 中辅：满足特定条件后才有机会打断监管节奏（如需要蓄力、需要队友配合、
#       条件受限或成功率不稳定），不保证能断。
# 软辅：只提供增益、治疗、信息类位移，无法直接打断监管动作。
# 校验规则：至少要有硬辅或中辅之一。
HARD_SUPPORT = {
    "前锋", "野人", "击球手", "逃脱大师", "哭泣小丑", "牛仔",
    "古董商", "咒术师", "勘探员", "画家", "小说家",
    "教授", "斗牛士", "杂技演员", "舞女", "昆虫学者",
}
MID_SUPPORT = {
    "祭司", "木偶师", "病患", "飞行家", "记者",
    "调酒师", "作曲家", "入殓师",
}
SOFT_SUPPORT = {
    "先知", "邮差", "心理学家", "小女孩", "医生",
    "拉拉队员", "气象学家", "幻灯师", "火灾调查员",
    "囚徒", "玩具商", "园丁", "骑士", "默剧艺人", "神童",
}

# 结构项权重（依据见模块头部说明）
PENALTY_NO_SOLID = 0.45
# 稳救（救援位）最多 1 个：2 个及以上即视为结构问题，按硬性冲突重罚
PENALTY_MULTI_SOLID = 1.20
PENALTY_NO_KITE = 0.30
# 样本量收缩的强度：n 个样本等权于 k 个「全联盟均值」样本。
# k=3 表示：样本 ≤3 时以全联盟为主，样本 ≥10 时基本信本队数据。
SHRINK_K = 3.0
# 地图倍数的收缩强度：该图该监管的 n 次出场，等价于再补 K 个
# 「按全局份额分布」的虚拟样本。K 越大倍数越贴近 1（越保守）。
# 扫过 2/3/4/6/8/12，K=4~8 时 top1 稳定在最高档且 nll 最低，取 4。
MAP_SHRINK_K = 4.0
# PENALTY_NO_SUPPORT 暂未启用：用户要求先看现有阵容是否满足「至少一个硬辅或软辅」，
# 确认名单无误后再决定是否并入评分。当前仅作页面校验用。
PENALTY_NO_SUPPORT = 0.35


def canon(c: str) -> str:
    c = c.replace("\u201c", "").replace("\u201d", "").replace('"', "")
    return "26号守卫" if c == "邦邦" else c


def ban_quota(game: int) -> int:
    """求生方 ② 阶段的 ban 名额（文档 3.1）。"""
    return 0 if game <= 1 else (1 if game == 2 else 2)


class Engine:
    def __init__(self, cand_ban_surv: int = 6, cand_ban_hunt: int = 4,
                 scores_override: dict = None,
                 pool_override: dict = None):
        """引擎的三份数据来源，优先级从高到低：

        scores_override：本次生成用的分数表（导入的监管选手配置 + 页面上
            的手工修改）。优先级最高 —— 它已经算好了每个格子的分，
            引擎直接用，不再二次收缩。引擎只读，不落盘。
        pool_override：本次生成用的角色池，{队伍名: [角色, ...]}。
            同样优先级最高，覆盖引擎内置的赛季出场角色池。
        bp_core.json / bp_query.json：预编辑表，只读，是兜底来源。

        没有「用户自定义数据」那一层了（旧的 data/custom/ 机制已下线）：
        预制数据在 data/ 根目录只读，要改就走三个配置目录的导入/导出。
        """
        self.tree = json.loads((ROOT / "data" / "bp_tree.json").read_text(encoding="utf-8"))
        q = json.loads((ROOT / "data" / "bp_query.json").read_text(encoding="utf-8"))
        self.sc = q["scores"]
        self.teams = self.tree["teams"]

        # 报告生成页传进来的最终分数表（最高优先级，不落盘）
        self.scores_override = {}
        for ch, hs in (scores_override or {}).items():
            if not isinstance(hs, dict):
                continue
            row = {}
            for h, v in hs.items():
                try:
                    row[canon(h)] = float(v if isinstance(v, (int, float))
                                          else (v or {}).get("score"))
                except (TypeError, ValueError):
                    continue
            if row:
                self.scores_override[canon(ch)] = row

        self.players = {}
        for p, info in self.tree["players"].items():
            info = dict(info)
            info["dist"] = {canon(c): v for c, v in info["dist"].items()}
            self.players[p] = info
        self.strength = {canon(c): v for c, v in self.tree["strength"].items()}
        self.ban_surv = {canon(x["char"]): x["p"] for x in self.tree["banSurvivor"]}
        self.ban_hunt = {canon(x["char"]): x["p"] for x in self.tree["banHunter"]}
        self.positions = {canon(c): v for c, v in self.tree["positions"].items()}
        # 监管候选池：只放选手实际用过的角色。图鉴里 19 个没人用过的监管
        # 分数全空，放进候选只会产出无意义的评分。
        _used = set()
        for info in self.players.values():
            _used |= set(info["dist"]) | set(info["raw_count"])
        self.hunter_pool = [canon(c) for c in self.tree["pool"]["hunter"]
                            if canon(c) in _used]
        # 各队求生角色池：pool_override（本次生成选的配置）优先，
        # 否则用 bp_tree.json 里由赛季实际出场统计出的 roster。
        self.rosters = {}
        for t, v in self.teams.items():
            self.rosters[t] = [canon(c) for c in v["roster"]]
        if pool_override:
            for t, chars in pool_override.items():
                if not chars:
                    continue
                self.rosters[t] = [canon(c) for c in chars]
        self.cand_ban_surv = cand_ban_surv
        self.cand_ban_hunt = cand_ban_hunt
        # 性能缓存：角色分数表、选手威胁前 3
        self._score_cache = {}
        self._top3_cache = {}
        self._ban_hunt_cache = {}
        # cell 查询的二级缓存（sc 是只读的）
        self._cell_cache = {}
        self._lineup_cache = {}
        self._exp_cache = {}
        # 热路径专用的数值缓存（只存浮点，不存明细字典）
        self._exp_val_cache = {}
        # 该选手的监管序列（按地图口径排好序），按 (player, map) 缓存
        self._threat_rows_cache = {}
        # 地图倍数查表（按图缓存，进程内，不落盘）
        self._mult_cache = {}
        self._surv_mult_cache = {}
        # 状态记忆化：同一 (阶段, 已 ban, 已选) 状态的最优值
        self._memo = {}
        # 每个状态的「排好序的候选表」。与路径无关，跨节点复用；
        # 因为它依赖 self.map_name，每次 search 开始时清空。
        self._cands_cache = {}
        # 本局地图（search 时设置，未选图时为 ""）
        self.map_name = ""
        # 先验平均：全联盟局均逃脱人数。
        # 直接从权威数据 bp_core.json 算，不写死常数 —— 口径一改
        # （比如比分→逃脱人数的换算），先验跟着变，不会出现
        # 「表是新的、基线还是旧的」这种不一致。
        core = json.loads((ROOT / "data" / "bp_core.json").read_text(encoding="utf-8"))
        _esc = [h["escape"] for h in core.get("halves", [])]
        self.prior = round(sum(_esc) / len(_esc), 4) if _esc else 2.0
        self._build_map_tables(core)

    def _build_map_tables(self, core) -> None:
        """地图 × 监管 的出场份额表，只用来给候选排序加权。

        数据源是 bp_core.json 的 map 字段（260 半局全覆盖）。
        这里只算「某张图上某个监管出场了多少次」，不碰任何分数格 ——
        地图不会改写 cell()，所以「某张图上能不能打」不会变成扣分，
        只会让更可能上场的候选排在前面。

        倍数定义（收缩以「全局份额」为基线，不是 0.5）：
            g    = 该监管在全联盟的出场份额 = 总次数 / 总半局
            sh   = (该图该监管次数 + K×g) / (该图局数 + K)
            mult = sh / g
        K = MAP_SHRINK_K。样本少的格向 1 收缩，不会因为一两局
        冒出 ×5 这种极端倍数。
        """
        halves = [h for h in core.get("halves", []) if h.get("map")]
        self._core_halves = core.get("halves", [])
        self._map_n = {}
        self._map_hn = {}
        self._map_sn = {}
        self._map_total = {}
        self._surv_total = {}
        for h in halves:
            m = h["map"]
            hh = canon(h["hunter"])
            self._map_n[m] = self._map_n.get(m, 0) + 1
            self._map_total[hh] = self._map_total.get(hh, 0) + 1
            self._map_hn.setdefault(m, {})
            self._map_hn[m][hh] = self._map_hn[m].get(hh, 0) + 1
            # 求生侧：同一局里每个出场角色各记一次
            self._map_sn.setdefault(m, {})
            for c in (h.get("survivors") or []):
                cc = canon(c)
                self._surv_total[cc] = self._surv_total.get(cc, 0) + 1
                self._map_sn[m][cc] = self._map_sn[m].get(cc, 0) + 1
        n_all = len(halves)
        self._map_prior = {}
        for hh, c in self._map_total.items():
            self._map_prior[hh] = c / n_all if n_all else 0.0
        # 求生角色的全局份额：分母是半局数，所以平均约 4/53
        self._surv_prior = {}
        for c, v in self._surv_total.items():
            self._surv_prior[c] = v / n_all if n_all else 0.0
        self._maps = sorted(self._map_n)

    def maps(self) -> list:
        """本季出现过的地图（按局数降序）。"""
        return sorted(self._map_n, key=lambda m: (-self._map_n[m], m))

    def map_share(self, map_name: str, hunter: str) -> float:
        """某张图上该监管的出场份额（已收缩）。没有地图数据时回退全局份额。"""
        hh = canon(hunter)
        g = self._map_prior.get(hh, 0.0)
        if not map_name:
            return g
        n = self._map_n.get(map_name, 0)
        if n == 0:
            return g
        cnt = self._map_hn.get(map_name, {}).get(hh, 0)
        K = MAP_SHRINK_K
        return (cnt + K * g) / (n + K)

    def map_mult(self, map_name: str, hunter: str) -> float:
        """该图该监管的「相对全局份额」倍数。1.0 = 与全联盟平均持平。

        没有该监管的全局记录时返回 1.0（不做任何加权），
        避免除零，也避免把从没上过场的角色乘成 0。
        """
        g = self._map_prior.get(canon(hunter), 0.0)
        if g <= 0:
            return 1.0
        return self.map_share(map_name, hunter) / g

    def _mult_table(self, map_name: str) -> dict:
        """某张图上「每个监管的倍数」查表，首次访问时算好。

        搜索里 map_mult 被调用上百万次，每次都走 canon() 的字符串处理
        （replace × 3）是纯浪费 —— 同一张图、同一个监管的倍数在整次
        搜索里恒定不变。这里一次算完，之后只做字典查询。

        不落盘：表在 Engine 实例内按图缓存，进程结束即消失。
        """
        tbl = self._mult_cache.get(map_name)
        if tbl is not None:
            return tbl
        if not map_name:
            tbl = {}
        else:
            n = self._map_n.get(map_name, 0)
            if n == 0:
                tbl = {}
            else:
                K = MAP_SHRINK_K
                hn = self._map_hn.get(map_name) or {}
                tbl = {}
                # 该图出场过的 + 全局有记录的，两边都覆盖
                for hh in set(hn) | set(self._map_prior):
                    g = self._map_prior.get(hh, 0.0)
                    if g <= 0:
                        tbl[hh] = 1.0
                    else:
                        cnt = hn.get(hh, 0)
                        tbl[hh] = ((cnt + K * g) / (n + K)) / g
        self._mult_cache[map_name] = tbl
        return tbl

    def map_mult_fast(self, map_name: str, hunter: str) -> float:
        """map_mult 的查表版，语义完全一致，热路径专用。"""
        if not map_name:
            return 1.0
        tbl = self._mult_table(map_name)
        if not tbl:
            return 1.0
        v = tbl.get(hunter)
        if v is not None:
            return v
        # 表里没有：可能是未 canon 的别名（如「邦邦」），退回通用实现
        return self.map_mult(map_name, hunter)

    def map_stats(self, map_name: str = "") -> dict:
        """地图统计：每张图（或指定图）的监管出场与实测逃脱。

        hunter_rows 给的是原始计数，用于页面标注「样本多少」，
        不做任何平滑 —— 平滑只发生在排序用的 map_mult 里。
        """
        halves = [h for h in self._core_halves if h.get("map")]
        out = {}
        for m in (self._maps if not map_name else [map_name]):
            sub = [h for h in halves if h["map"] == m]
            if not sub:
                continue
            cnt = {}
            for h in sub:
                hh = canon(h["hunter"])
                cnt[hh] = cnt.get(hh, 0) + 1
            esc = [h["escape"] for h in sub]
            out[m] = {
                "halves": len(sub),
                "escape": round(sum(esc) / len(esc), 4),
                "hunters": [{"char": hh, "n": c,
                             "mult": round(self.map_mult(m, hh), 4)}
                            for hh, c in sorted(cnt.items(), key=lambda x: -x[1])],
                "survivors": self._map_surv_rows(m),
            }
        return out

    def _map_surv_rows(self, map_name: str) -> list:
        """该图上出场次数 >= 2 的求生角色（含地图倍数）。"""
        sub = [h for h in self._core_halves if h.get("map") == map_name]
        if not sub:
            return []
        cnt = {}
        for h in sub:
            for c in (h.get("survivors") or []):
                cc = canon(c)
                cnt[cc] = cnt.get(cc, 0) + 1
        rows = [{"char": c, "n": n, "mult": round(self.surv_map_mult(map_name, c), 4)}
                for c, n in cnt.items()]
        rows.sort(key=lambda r: -r["n"])
        return rows

    # ── 求生角色 × 地图 ──────────────────────────────────
    def surv_map_share(self, map_name: str, char: str) -> float:
        """某张图上该求生角色的出场份额（已收缩到全局份额）。

        每局 4 个求生位，所以份额的含义是「这一局里他上场的比例」，
        全联盟平均约 4/53 ≈ 0.075。倍数才是跨角色可比的量。
        """
        c = canon(char)
        g = self._surv_prior.get(c, 0.0)
        if not map_name:
            return g
        n = self._map_n.get(map_name, 0)
        if n == 0:
            return g
        cnt = self._map_sn.get(map_name, {}).get(c, 0)
        K = MAP_SHRINK_K
        return (cnt + K * g) / (n + K)

    def surv_map_mult(self, map_name: str, char: str) -> float:
        """该图该求生角色的「相对全局份额」倍数。1.0 = 与全联盟平均持平。

        实测（5 折，260 半局）：按该图份额排序，真实选中的角色
        落在 top8 的比例 37.1% → 47.6%，打乱地图 100 次 p=0.01。
        所以地图确实影响「这张图会选哪些角色」，可以用于候选排序。
        它不改任何分数格，只影响排序。
        """
        g = self._surv_prior.get(canon(char), 0.0)
        if g <= 0:
            return 1.0
        return self.surv_map_share(map_name, char) / g

    def surv_map_mult_fast(self, map_name: str, char: str) -> float:
        """surv_map_mult 的查表版，语义一致，热路径专用。"""
        if not map_name:
            return 1.0
        tbl = self._surv_mult_cache.get(map_name)
        if tbl is None:
            n = self._map_n.get(map_name, 0)
            if n == 0:
                tbl = {}
            else:
                K = MAP_SHRINK_K
                sn = self._map_sn.get(map_name) or {}
                tbl = {}
                for c in set(sn) | set(self._surv_prior):
                    g = self._surv_prior.get(c, 0.0)
                    if g <= 0:
                        tbl[c] = 1.0
                    else:
                        cnt = sn.get(c, 0)
                        tbl[c] = ((cnt + K * g) / (n + K)) / g
            self._surv_mult_cache[map_name] = tbl
        v = tbl.get(char) if tbl else None
        if v is not None:
            return v
        return self.surv_map_mult(map_name, char)

    def map_surv_role_mix(self, map_name: str) -> dict:
        """该图上各职能的出现比例，与全联盟比例的对比。

        只做展示：实测把它乘进候选排序只带来 +0.5% 的 top8 提升，
        说明职能信息已经被单个角色的地图份额吸收，不值得进排序。
        但教练看「红教堂 54% 带破译型 / 月亮河公园 12%」是有用的提示
        （χ²=26.1 vs 零分布 7.0，置换 p=0.0005）。
        """
        roles = ["救援型", "牵制型", "破译型", "辅助型"]
        sub = [h for h in self._core_halves if h.get("map")] if not map_name \
            else [h for h in self._core_halves if h.get("map") == map_name]
        if not sub:
            return {}
        allh = [h for h in self._core_halves if h.get("map")]
        out = {}
        for r in roles:
            def has(rows):
                return sum(1 for x in rows
                           if any(r in (self.positions.get(canon(c)) or [])
                                  for c in (x.get("survivors") or [])))
            base = has(allh) / len(allh) if allh else 0.0
            cur = has(sub) / len(sub)
            out[r] = {"rate": round(cur, 4), "base": round(base, 4),
                      "mult": round(cur / base, 4) if base > 0 else 1.0}
        return out

    def pool_of(self, team: str) -> list:
        """该队的求生可选角色池。"""
        return self.rosters.get(team) or []

    # ── 分数查询 ──────────────────────────────────────────
    def cell(self, team: str, char: str, hunter: str) -> float:
        """某求生角色面对某监管的局均逃脱。

        取值顺序（先到先用，谁在前谁说了算）：
            ① scores_override —— 本次生成导入的监管选手配置 + 手工修改。
               这一份里的每个格子都已经算好了分数，直接用，不再收缩。
            ② bp_query.score   —— 预编辑表里算好的分：
                   score = w*own + (1-w)*global,  w = n/(n+K)
               这里直接取 score，不再拿 global 二次收缩。

        为什么不再自己收缩一次（历史 bug）：
            旧代码取 global 后又用 SHRINK_K 收缩一次，等于把
            「样本少 → 向联盟均值靠」这件事做了两遍。后果是
            n=1 的极端格子被砍掉近一半 —— 野人 vs 时空之影
            实测 own=4.0、global=4.0，却被压成 2.24，
            平均偏差 0.27、最大 1.76。bp_query 里的 weight
            本来就是干这件事的，引擎再干一次是重复劳动。
        """
        key = (team, char, hunter)
        v = self._cell_cache.get(key)
        if v is not None:
            return v

        # ① 本次生成导入的监管配置（页面上的最终分数表）
        ov = self.scores_override.get(char)
        if ov is not None and hunter in ov:
            v = ov[hunter]
            self._cell_cache[key] = v
            return v

        # ② 预编辑表：直接用已经算好的 score（含 own 与权重的收缩结果）
        #    score 是按队算的，所以先查本队；本队没有这一格再回退到别队的同格。
        row = None
        own_chars = self.sc.get(team) or {}
        cc = (own_chars.get(char) or {}).get(hunter)
        if cc:
            row = cc
        else:
            for chars in self.sc.values():
                cc = (chars.get(char) or {}).get(hunter)
                if cc:
                    row = cc
                    break
        if row is None:
            v = self.prior
        else:
            s = row.get("score")
            if s is None:
                s = row.get("global")
            v = float(s) if s is not None else self.prior
        self._cell_cache[key] = v
        return v

    def load_player_scores(self, player: str) -> None:
        """载入监管选手的折合分表（data/hunter/<选手>.json）作为 scores_override。

        该表是 (求生 × 监管角色) 的折合分格子，经 build_preset_configs.py
        从选手实测折合，n 是样本局数。载入后 cell() 第一优先级直接用它，
        本队/全局修正都不再参与 —— 评估完全以这位选手的实战表现为准。
        表里没有的格子自动回退 global/prior（cell 兜底）。
        """
        from file_api import DIR_HUNTER
        p = ROOT / DIR_HUNTER / f"{player}_1.json"
        if not p.exists():
            return
        d = json.loads(p.read_text(encoding="utf-8"))
        cells = {}
        for c, hs in (d.get("cells") or {}).items():
            row = {}
            for h, v in hs.items():
                num = v.get("score") if isinstance(v, dict) else v
                if num is None:
                    continue
                try:
                    row[canon(h)] = float(num)
                except (TypeError, ValueError):
                    continue
            if row:
                cells[canon(c)] = row
        if cells:
            self.scores_override = cells
            # override 变了，所有缓存作废
            self._cell_cache.clear()
            self._lineup_cache.clear()
            self._exp_cache.clear()
            self._exp_val_cache.clear()
            self._memo.clear()
            self._cands_cache.clear()
            self._score_cache.clear()
            self._top3_cache.clear()
            self._ban_hunt_cache.clear()

    def lineup_score(self, team: str, chars, hunter: str) -> float:
        """阵容对某监管的评分：最低 3 个各权重 0.3 + 最高 1 个权重 0.1，加结构项。

        权重口径（2026-11-06 用户定）：最低 3 个 ×0.3 + 最高 1 个 ×0.1。
        旧的「纯最低 3 人均值」等于把最强的一个完全剔掉，
        现在保留 0.1 的权重 —— 强角色仍有贡献，但主要分量还是
        由被监管针对的最弱三人决定。

        阵容不足 4 人时（只在阶段评估的中间态出现）：现有分数按 0.3 权重
        计入，缺失的位置按 0 分补足（含 0.1 的最强位权重），
        与旧口径一样向保守方向偏，不会出现「少一个人分数反而更高」的假象。
        """
        key = (team, tuple(sorted(chars)), hunter)
        v = self._lineup_cache.get(key)
        if v is not None:
            return v

        vals = sorted(self.cell(team, c, hunter) for c in chars)
        n = len(vals)
        if n >= 4:
            # 最低 3 个权重 0.9（每个 0.3）+ 最高 1 个权重 0.1
            base = (sum(vals[:3]) * 0.3 + vals[3] * 0.1)
        elif n == 0:
            base = self.prior
        else:
            # 不足 4 人时：现有分数按 0.3 权重、缺口按 0 分补足
            # （再留 0.1 的「最强位」权重给缺失的第 4 人，同为 0 分），
            # 与旧口径一样向保守方向偏，不会出现少一人分反而更高的假象
            base = sum(vals) * 0.3

        solid_n = sum(1 for c in chars if c in SOLID)
        kite_n = sum(1 for c in chars
                     if c not in SOLID
                     and "牵制型" in (self.positions.get(c) or []))

        if solid_n == 0:
            base -= PENALTY_NO_SOLID
        elif solid_n >= 2:
            # 稳救最多 1 个；每多 1 个就再叠加一次惩罚，
            # 避免出现“3 个稳救”和“2 个稳救”同分的情况
            base -= PENALTY_MULTI_SOLID * (solid_n - 1)
        if kite_n == 0:
            base -= PENALTY_NO_KITE
        self._lineup_cache[key] = base
        return base

    def _expect_value(self, team: str, chars, player: str, exclude=None,
                      map_name: str = None) -> float:
        """expected_vs_player 的纯数值版，搜索热路径专用。

        只返回期望值，不构造 top3 明细 —— 明细里的 round()、字典分配
        在每秒几十万次的调用下是纯开销，而热路径只用得到这个浮点数。

        口径与 expected_vs_player 完全一致（同一套候选、同一套权重），
        两条路径共用 _expect_rows()，不存在两份实现各算各的风险。
        """
        # 键直接用 frozenset：集合无序，本身就等价于原实现里的
        # tuple(sorted(...))，但不必先把元素排一遍。
        # 热路径每秒调用几十万次，省下的就是那几百万次 sorted。
        # 已经是 frozenset 时 frozenset() 直接返回原对象，零开销。
        key = (team, frozenset(chars), player,
               frozenset(exclude) if exclude else (), map_name or "")
        hit = self._exp_val_cache.get(key)
        if hit is not None:
            return hit
        chosen, tot_w, acc = self._expect_core(team, key[1], player, key[3], map_name)
        v = acc / tot_w if tot_w else self.prior
        self._exp_val_cache[key] = v
        return v

    def _expect_core(self, team, chars, player, exclude, map_name):
        """算出 (入选的监管列表, 权重和, 加权分)。两条路径共用。"""
        info = self.players[player]
        raw = info["raw_count"]
        skip = exclude or ()
        rows = self._threat_rows(player, map_name)
        if skip:
            sk = set(skip)
            rows = [r for r in rows if r[0] not in sk]
        if not rows:
            return None, 0.0, self.prior

        chosen = rows[:3]
        tot_w = 0.0
        acc = 0.0
        for ch, w, th, mult in chosen:
            weff = w * mult
            tot_w += weff
            acc += self.lineup_score(team, chars, ch) * weff
        return chosen, tot_w, acc

    def _threat_rows(self, player: str, map_name: str = ""):
        """该选手「有使用记录」的监管，按当前地图口径排好序。

        返回 [(char, raw_count, threat, mult), ...]，直接可切片取前 3。

        预处理要点：
          · 原实现每次调用都要遍历 threats、查 raw、算 map_mult，
            再（有地图时）排序 —— 每秒几十万次调用全部重做一遍。
          · 排序结果只跟 (选手, 地图) 有关，与已 ban 什么无关，
            所以按 (player, map_name) 缓存整张有序表，
            调用方只需在表上按 exclude 过滤。
        不落盘：缓存挂在 Engine 实例上。
        """
        ck = (player, map_name or "")
        cached = self._threat_rows_cache.get(ck)
        if cached is not None:
            return cached

        info = self.players[player]
        raw = info["raw_count"]
        rows = []
        if map_name:
            tbl = self._mult_table(map_name)
            for t in info["threats"]:
                ch = t["char"]
                w = raw.get(ch, 0)
                if w <= 0:
                    continue
                mult = tbl.get(ch)
                if mult is None:
                    mult = self.map_mult(map_name, ch)
                rows.append((ch, w, t["threat"], mult))
            rows.sort(key=lambda r: -(r[2] * r[3]))
        else:
            for t in info["threats"]:
                ch = t["char"]
                w = raw.get(ch, 0)
                if w <= 0:
                    continue
                rows.append((ch, w, t["threat"], 1.0))
        self._threat_rows_cache[ck] = rows
        return rows

    def expected_vs_player(self, team: str, chars, player: str, exclude=None,
                           map_name: str = None) -> dict:
        """叶子评估：取威胁度最高的前 3 个监管，按选手选用次数加权。

        规则（与用户确认）：
          1. 排序依据：威胁度 = 使用概率 × 角色强度
          2. 权重：选手对该监管的实际选用次数 raw_count
          3. 分母：入选的这 3 个监管的次数之和（归一化）
          4. 备选：若前 3 个里有缺失或次数为 0 的，按威胁度顺序继续
             往下取，直到凑满 3 个有实际使用记录的角色；
             若整个分布都不足 3 个，按实际数量归一化

        exclude：已被 ban 的监管（lban）。被 ban 的监管不可能被选中，
        必须从候选里剔除，否则 ban 对结果完全没有影响。

        map_name：本局地图。给了就把地图倍数乘进「排序」和「权重」两处：
            排序 → 威胁度 × 倍数   （决定谁进前 3）
            权重 → 使用次数 × 倍数 （决定 3 个之间怎么分）
        两处都要乘。只乘权重的话前 3 的构成不变，top3 命中率
        实测一动不动（0.519 → 0.519），等于没接地图。

        为什么不用「概率 × 强度」直接当权重：
            强度为 0 的台球手即便被频繁使用，权重也会被抹成 0，
            等于假设对手永远不会选它，与选手的实际习惯冲突。

        记忆化：叶子评估在搜索里被调用几百万次，
        而 (阵容, 选手, 已ban的监管) 的组合是有限的，
        所以整段结果缓存起来，不再重复算。
        """
        key = (team, tuple(sorted(chars)), player,
               tuple(sorted(exclude)) if exclude else (), map_name or "")
        hit = self._exp_cache.get(key)
        if hit is not None:
            return hit

        chosen, tot_w, acc = self._expect_core(team, chars, player, exclude, map_name)
        if not chosen:
            res = {"expect": self.prior, "top3": []}
            self._exp_cache[key] = res
            return res

        detail = []
        for ch, w, th, mult in chosen:
            detail.append({"char": ch, "w": w, "w_map": round(w * mult, 4),
                           "mult": round(mult, 4),
                           "score": round(self.lineup_score(team, chars, ch), 4),
                           "threat": round(th, 4)})
        res = {"expect": acc / tot_w, "top3": detail}
        self._exp_cache[key] = res
        return res

    # ── 候选集裁剪 ────────────────────────────────────────
    # 剪枝思路：候选必须和「对手是谁」绑定，否则教练给每个选手做的 BP 差异就被抹平了。
    #   ①④⑥ 监管 ban 求生：只在角色池内取被 ban 率最高的前 8
    #   ② 求生 ban 监管：只取该监管选手威胁度前 6
    @lru_cache(maxsize=None)
    def ban_candidates_surv(self, team: str) -> tuple:
        """① 监管方 ban 求生者的候选：该队角色池中被 ban 率前 6。

        ① 阶段求生方还没有任何选择，监管方只能凭「哪些角色通常难打」来 ban，
        所以用全局被 ban 率作为排序依据。范围是该队自己的角色池。
        """
        scored = sorted(self.pool_of(team), key=lambda c: -self.ban_surv.get(c, 0.0))
        return tuple(scored[: self.cand_ban_surv])

    def ban_candidates_response(self, team: str, picked, lban, lpick, player: str,
                                n: int | None = None) -> list:
        """④⑥ 监管方 ban 求生者的候选：针对 ③⑤ 已选出的角色。

        监管方看到已选角色后才 ban，目标是 ban 掉「能让这套阵容变强」的角色。
        打分：假设求生方最终会选 c，算 {已选 + c} 相对 {已选} 的评分提升，
        再叠加被 ban 率作为先验（教练有固定偏好）。

        性能关键：per-char 分数表按 (team, char) 预缓存，
        每个节点只做一次 O(n) 的查表，不做嵌套重算。
        """
        n = n or self.cand_ban_surv
        roster = self.pool_of(team)
        top = self._top3_hunters(player, self.map_name)

        def char_min_scores(c):
            """该角色对前 3 监管的分数（已排序，缓存）。"""
            key = (team, c, top)
            v = self._score_cache.get(key)
            if v is None:
                v = sorted(self.cell(team, c, h) for h in top)
                self._score_cache[key] = v
            return v

        def agg(chars):
            """最低 3 个分数的均值（与叶子口径一致）。"""
            vals = []
            for c in chars:
                vals.extend(char_min_scores(c))
            if not vals:
                return self.prior
            vals.sort()
            k = min(3, len(vals))
            return sum(vals[:k]) / k

        base = list(picked)
        cur = agg(base) if base else self.prior
        # 「再补一个人之后的聚合分」= 已选阵容最小 3 个分数 ∪ 新角色 3 个分数
        # 里取最小 3 个 —— 已选阵容其余分数不可能挤进前三，
        # 所以不必每次把 12 个数重排一遍，只需排 6 个。
        base_min = []
        if base:
            for c in base:
                base_min.extend(char_min_scores(c))
            base_min.sort()
            base_min = base_min[:3]
        scored = []
        picked_set = set(picked)
        for c in roster:
            # picked 必须显式排除：调用方传进来的 lpick 不一定覆盖它
            # （教练报告从中间状态续搜时，已选角色可能只在 spicks 里）。
            # 不排除的话，对方会「ban 掉我方已经选下的求生」，逻辑上不成立。
            if c in lban or c in lpick or c in picked_set:
                continue
            if base_min:
                merged = base_min + char_min_scores(c)
                merged.sort()
                k = min(3, len(merged))
                gain = sum(merged[:k]) / k - cur
            else:
                gain = agg([c]) - cur
            prior = self.ban_surv.get(c, 0.0)
            scored.append((c, gain * 3.0 + prior * 2.0))
        scored.sort(key=lambda x: -x[1])
        return [c for c, _ in scored[:n]]

    def _top3_hunters(self, player: str, map_name: str = "") -> tuple:
        """选手威胁前 3 的监管（与叶子评估一致），缓存起来。

        给了地图就按「威胁度 × 地图倍数」重排后取前 3，口径与
        expected_vs_player 一致 —— 这两处必须用同一套候选，
        否则「对手会 ban 哪些求生」和「对手会拿哪个监管」会互相打架。
        """
        key = (player, map_name or "")
        v = self._top3_cache.get(key)
        if v is None:
            rows = list(self.players[player]["threats"])
            if map_name:
                tbl = self._mult_table(map_name)
                rows.sort(key=lambda t: -(t["threat"] * tbl.get(t["char"],
                                                                self.map_mult(map_name, t["char"]))))
            v = tuple(t["char"] for t in rows[:3])
            self._top3_cache[key] = v
        return v

    def ban_candidates_hunt(self, player: str, map_name: str = "") -> tuple:
        """求生方 ban 监管的候选：该选手威胁度前 6。

        地图参与排序：优先 ban 掉「这张图上他最可能拿出来」的监管。
        """
        key = (player, map_name or "")
        v = self._ban_hunt_cache.get(key)
        if v is None:
            rows = [t for t in self.players[player]["threats"]]
            if map_name:
                tbl = self._mult_table(map_name)
                rows.sort(key=lambda t: -(t["threat"] * tbl.get(t["char"],
                                                                self.map_mult(map_name, t["char"]))))
            v = tuple(t["char"] for t in rows[: self.cand_ban_hunt])
            self._ban_hunt_cache[key] = v
        return v

    # ── 搜索主体 ──────────────────────────────────────────
    def search(self, team: str, player: str, game: int, depth_limit: int | None = None,
               map_name: str | None = None):
        """从当前半局开头做全流程搜索，返回最优动作序列和各阶段评估。

        map_name：本局地图。影响对手监管候选的排序与加权（见
        expected_vs_player），不影响任何分数的取值。

        返回值：
            best  = 求生方视角的最优期望逃脱
            line  = 主变线路上的动作序列
        """
        stages = self._stages(game)
        self.counters = {"nodes": 0, "cut": 0}
        self.map_name = map_name or ""
        self.trace = []
        # 记录同一阶段内已选的动作，用于「只往后取」去重排列
        self._taken_chars = {}
        # 每次搜索清空状态记忆（不同 team/player/game 之间不通用）
        self._memo = {}
        self._cands_cache = {}

        result = self._dfs(
            team, player, game, stages, 0,
            frozenset(), frozenset(),   # local_ban, local_pick
            frozenset(),                # 本局已选求生（用于阵容评估）
            -1e9, 1e9,
            depth_limit if depth_limit is not None else len(stages),
            0,                          # taken：当前阶段已做几个动作
        )
        return result

    def _stages(self, game: int) -> list:
        s = [
            ("①", "hunt_ban", 2),
            ("②", "surv_ban", ban_quota(game)),
            ("③", "surv_pick", 2),
            ("④", "hunt_ban", 1),
            ("⑤", "surv_pick", 1),
            ("⑥", "hunt_ban", 1),
            ("⑦", "surv_pick", 1),
            ("⑩", "hunt_pick", 1),
        ]
        return [x for x in s if x[2] > 0]

    def _dfs(self, team, player, game, stages, idx, lban, lpick, spicks, alpha, beta, depth, taken=0):
        """深度优先 + α-β 剪枝 + 状态记忆化。

        taken 表示「当前阶段已经做了几个动作」，用来处理 ① ban2 和 ③ pick2
        这类一个阶段多个动作的情况，避免靠集合大小反推阶段进度。

        记忆化：同一个 (阶段, 已 ban, 已选, 已 pick 的求生) 状态，
        无论从哪条路径到达，后续最优值都一样，所以可以缓存整个子树结果。

        两处限制保证正确性：
          · 只缓存 taken == 0 的节点 —— taken > 0 时候选表会被
            _taken_chars 按「上一手选了什么」截断，那是路径相关的。
          · 只缓存返回值严格落在 (alpha0, beta0) 开区间内的节点 ——
            落在边界上说明子树被窗口截断过，那只是「至少/至多」的
            估计值，换个窗口再来就会算错。
        """
        self.counters["nodes"] += 1
        if idx >= len(stages):
            return self._leaf(team, player, spicks, lban), []
        if idx >= depth:
            return self._shallow(team, player, spicks, lban), []

        # memo 的 key 必须带地图：同一个 Engine 实例换图重算时，
        # 如果 key 里没有 map_name，上一张图的最优值和线路会被当成
        # 这张图的结果直接返回（实测月亮河拿到红教堂的结论）。
        # team/player/game 在单次会话内恒定，但它们也一并进来 ——
        # 代价只是 key 长一点，换来跨场次复用也安全。
        #
        # key 只在真要用的时候才构造：建 frozenset 不便宜，
        # 而绝大部分节点根本不查 memo。
        #
        # 只有 taken == 0 的节点可缓存：taken > 0 时候选表会被
        # _taken_chars 按「上一手选了什么」截断，那是路径相关的，
        # 同一个 (idx, taken, lban) 从不同路径到达时后续候选不同。
        memo_ok = taken == 0
        if memo_ok:
            key = (team, player, game, self.map_name, depth, idx,
                   lban, lpick, spicks)
            hit = self._memo.get(key)
            if hit is not None:
                return hit[0], list(hit[1])
        alpha0, beta0 = alpha, beta

        code, kind, count = stages[idx]
        is_surv_pick = kind == "surv_pick"
        is_max = is_surv_pick            # 求生方最大化，其余全部最小化

        # ⑩ 监管选人：叶子按「威胁前 3 加权」评估，与监管方具体选了谁无关，
        # 这一层对结果没有影响，只记录「若推进到这里，最可能选谁」供展示。
        # 展示值必须排除 lban：我方 ban 掉的监管对方不可能拿到，
        # 不排除的话这块会显示「对手选了我方刚 ban 的人」——
        # 分数不受影响（分数走 _leaf，内部本来就带 exclude），但这行字是错的。
        if kind == "hunt_pick":
            v = self._leaf(team, player, spicks, lban)
            likely = None
            if spicks:
                rows = self._threat_rows(player, self.map_name)
                for r in rows:
                    if r[0] not in lban:
                        likely = r[0]
                        break
            return v, [("⑩", likely, v)]

        # 构造候选，并按「对评估有利」排序 —— 好的排序能大幅提高 α-β 剪枝效率
        #
        # 候选集与它们的排序只由「阶段 + 已 ban/已 picked」决定，与从哪条
        # 路径到达无关，因此整个排好序的候选表可以按状态缓存。
        # ⑥⑦ 的重复率高达 75%~80%（同一状态被不同路径反复压到），
        # 不缓存的话 ⑦ 的 pick_key 要跑上百万次，每次都是整池重排。
        #
        # key 里必须带 map_name 与 depth：候选排序用了 _shallow / 地图倍数，
        # 而 _cands_cache 在 run_from 之间是复用的（不像 search 每次清空），
        # 漏掉这两项会让换图重算时拿到上一张图的候选顺序。
        ckey = (code, idx, taken, lban, lpick, spicks,
                self.map_name, depth)
        cands = self._cands_cache.get(ckey)
        if cands is None:
            if kind == "hunt_ban":
                if code == "①":
                    # ① 求生方还没有任何选择，只能凭全局被 ban 率
                    cands = [c for c in self.ban_candidates_surv(team)
                             if c not in lban and c not in lpick]
                    cands.sort(key=lambda c: -self.ban_surv.get(c, 0.0))
                else:
                    # ④⑥ 针对 ③⑤ 已选出的阵容来 ban
                    cands = self.ban_candidates_response(
                        team, spicks, lban, lpick, player, self.cand_ban_surv)
            elif kind == "surv_ban":
                cands = [c for c in self.ban_candidates_hunt(player, self.map_name)
                         if c not in lban and c not in lpick]
                # 求生方优先 ban 对阵容杀伤最大的（分数越低越该 ban）
                cands.sort(key=lambda c: self.lineup_score(team, list(spicks), c) if spicks else 0)
            elif kind == "surv_pick":
                # 已选的求生角色同时排除 lpick 与 spicks：
                # 正常从 ① 开搜时两者一致，但从中间状态续搜时 spicks 可能已有内容
                # （教练报告就是这么用的），必须防重复选人
                taken_set = lban | lpick | spicks
                cands = [c for c in self.pool_of(team) if c not in taken_set]
                # 求生方优先选能提升评估的：先试稳救，再试高分角色
                def pick_key(c):
                    trial = list(spicks) + [c]
                    return -(self._shallow(team, player, frozenset(trial), lban))
                cands.sort(key=pick_key)
            else:  # hunt_pick
                cands = [c for c in self.players[player]["dist"]
                         if c in self.strength and c not in lban and c not in lpick]
                # 监管方优先选最克制的（分数最低）
                cands.sort(key=lambda c: self.lineup_score(team, list(spicks), c) if spicks else 0)
            cands = tuple(cands)
            self._cands_cache[ckey] = cands
        else:
            cands = list(cands)

        if not cands:
            return self._leaf(team, player, spicks, lban), []
        # 同一阶段内多个动作是无序组合（先 ban A 再 ban B 与先 B 后 A 等价），
        # 因此本阶段后续动作只从「当前候选之后」取，避免重复枚举排列。
        if taken > 0:
            prev = self._taken_chars.get((idx, taken - 1))
            if prev is not None and prev in cands:
                cands = cands[cands.index(prev) + 1:]
            if not cands:
                return self._leaf(team, player, spicks, lban), []

        best_val = -1e9 if is_max else 1e9
        best_line = []
        # 这两个判断只跟阶段类型有关，提到循环外算一次。
        # 循环体是整棵树里执行次数最多的地方（每节点每候选一次）。
        is_ban = kind.endswith("ban")
        is_pick = kind.endswith("pick")
        n_stages = len(stages)
        # ⑩ 要展示的「最可能选的监管」必须排除「到那时已被 ban 的监管」，
        # 而每个候选的 nb 不同，所以在循环内按 nb 现取（见下面的分支）。
        nxt_is_hunt_pick = (idx + 1 < n_stages
                            and stages[idx + 1][1] == "hunt_pick")
        for c in cands:
            if count > 1:
                self._taken_chars[(idx, taken)] = c
            nb = lban | {c} if is_ban else lban
            np_ = lpick | {c} if is_pick else lpick
            nsp = spicks | {c} if is_surv_pick else spicks
            # 本阶段还没做完就留在本阶段，做满了再进下一阶段
            if taken + 1 >= count:
                nidx, ntaken = idx + 1, 0
            else:
                nidx, ntaken = idx, taken + 1
            # 下一层是 ⑩（监管选人）时不必真递归进去：它的返回值就是
            # 该状态的叶子评估，监管方选谁不影响分数，只在主变线路上
            # 留一行展示。省掉的正是最深一层，节点数与叶子数同量级，
            # 原本占了整棵树的九成。
            if (nidx < n_stages and stages[nidx][1] == "hunt_pick"
                    and nidx < depth):
                v = self._leaf(team, player, nsp, nb)
                likely = None
                if nsp:
                    rows = self._threat_rows(player, self.map_name)
                    for r in rows:
                        if r[0] not in nb:
                            likely = r[0]
                            break
                line = [("⑩", likely, v)]
            else:
                v, line = self._dfs(team, player, game, stages, nidx,
                                    nb, np_, nsp, alpha, beta, depth, ntaken)
            if count > 1:
                self._taken_chars.pop((idx, taken), None)
            if v is None:
                v = 0.0
            if is_max:
                if v > best_val:
                    best_val, best_line = v, [(code, c, v)] + line
                alpha = max(alpha, v)
            else:
                if v < best_val:
                    best_val, best_line = v, [(code, c, v)] + line
                beta = min(beta, v)
            if beta <= alpha:
                self.counters["cut"] += 1
                break
        # 存 memo 只在结果落在原窗口内部时安全：窗口内若发生过剪枝，
        # best_val 只是「至少/至多这么多」，换个窗口再来就会算错。
        # 判据：最终 best_val 严格落在 (alpha0, beta0) 之间，说明没有任何
        # 兄弟节点被窗口截断，这次搜索是完整的精确值。
        if memo_ok and alpha0 < best_val < beta0:
            self._memo[key] = (best_val, list(best_line))
        return best_val, best_line

    def _leaf(self, team, player, spicks, lban=None):
        """真实叶子：此时求生阵容应为 4 人。

        lban 里被 ban 掉的监管必须排除，否则 ban 监管对结果毫无影响。
        地图从 self.map_name 读（search() 时设置），决定候选排序与加权。
        """
        if len(spicks) >= 4:
            return self._expect_value(team, spicks, player, exclude=lban,
                                      map_name=self.map_name)
        return self._shallow(team, player, spicks, lban)

    def _shallow(self, team, player, spicks, lban=None):
        """深度截断时的近似评估。

        不能用「花名册补齐」——那会让所有分支得到同一个值，搜索退化。
        改为：只对当前已选角色中威胁最高的前 3 个监管求期望。
        阵容不完整的时间接扣分已在 lineup_score 里按 4 人归一处理，
        这里不再额外扣分，否则等于重复惩罚。
        """
        if not spicks:
            return self.prior
        return self._expect_value(team, spicks, player, exclude=lban,
                                  map_name=self.map_name)


def main():
    eng = Engine()
    team = "ACT"
    player = "YuM"
    game = 3
    import time
    t0 = time.time()
    val, line = eng.search(team, player, game)
    dt = time.time() - t0
    print(f"{team} vs {player} 第 {game} 局")
    print(f"  搜索节点 {eng.counters['nodes']}  剪枝 {eng.counters['cut']} 次  耗时 {dt*1000:.0f} ms")
    print(f"  最优期望逃脱 {val:.3f}")
    print("  主变线路：")
    for code, c, v in line:
        print(f"    {code} {c:<8} → {v:.3f}")


if __name__ == "__main__":
    main()
