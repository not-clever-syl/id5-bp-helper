import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 熟练度口径 ────────────────────────────────────────────
# adjusted  折算值里含「选手熟练度修正」——BP 推演、生成页用的就是这一套
# raw        退回修正之前的原始均值
# 页面上「未调整熟练度 / 调整熟练度」两种视图就是靠它区分的。
MODE_ADJUSTED = "adjusted"
MODE_RAW = "raw"


def _tree(tree=None):
    """bp_tree.json 的内容。每次调用都重新读盘，不留缓存。

    tree 参数留给调用方传入已加载的版本，避免一次流程里重复读盘。
    """
    if tree is not None:
        return tree
    p = ROOT / "data" / "bp_tree.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _dataset():
    """bp_core.json 的内容。每次调用都重新读盘，不留缓存。"""
    p = ROOT / "data" / "bp_core.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _query():
    """bp_query.json 的内容。每次调用都重新读盘，不留缓存。"""
    p = ROOT / "data" / "bp_query.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _halves():
    return _dataset()["halves"]


# ── 基础查询（只读）──────────────────────────────────────
def base_roster() -> dict:
    """各队的求生角色池（来自 bp_tree.json 的赛季出场统计）。"""
    out = {}
    for t, v in _tree()["teams"].items():
        out[t] = list(v.get("roster") or [])
    return out


def base_cells(mode: str = MODE_ADJUSTED) -> dict:
    """原始分数表，按「角色 → 监管 → {score, n}」整理。

    原表是 team → char → hunter，但各队的 global 值相同，
    所以取第一个出现的那份作为基准，避免同一格出现四个副本。

    mode=MODE_ADJUSTED 取 global —— 已含选手熟练度修正；
    mode=MODE_RAW      取 global_raw —— 修正之前的原始均值。
    两种口径的样本数相同（同一条半局，只是加不加 adjust）。

    只保留榜上要显示的行与列（角色池并集 × 选手用过的监管），
    否则大表里会出现整行整列的空格。
    """
    fld = "global_raw" if mode == MODE_RAW else "global"
    # 一次调用里只读一遍 bp_tree.json / bp_core.json，往下传递，
    # 避免每个辅助函数各读一次
    t = _tree()
    halves = _halves()
    keep_c = set(all_survivors(t, halves))
    keep_h = set(all_hunters(t, halves))
    cells = {}
    for chars in _query()["scores"].values():
        for ch, hunters in chars.items():
            if ch in cells or ch not in keep_c:
                continue
            row = {}
            for h, v in hunters.items():
                if h not in keep_h:
                    continue
                sc = v.get(fld, v.get("global"))
                if sc is None:
                    # 没有本队实测、直接取全局值的格子只有 global 一份
                    sc = v.get("score") if mode == MODE_RAW else None
                if sc is None:
                    continue
                row[h] = {
                    "score": sc,
                    "n": v.get("global_n", 0) or 0,
                }
            if row:
                cells[ch] = row
    return cells


def all_teams() -> list:
    return list(_tree()["teams"].keys())


def all_survivors(tree=None, halves=None) -> list:
    """榜上要显示的求生角色：各队角色池的并集。

    图鉴里 53 个求生，有 10 个从没进过任何战队的角色池（击球手、小女孩、
    律师……）。它们在表里全是空格，显示出来只会干扰，所以不列。

    排序用战报里的真实出场次数：教练最常遇到的求生必须排在最前，
    否则找「佣兵」要扫很久。
    """
    cnt = survivor_counts(halves)
    t = _tree(tree)
    union = set()
    for v in t["teams"].values():
        union |= set(v["roster"])
    order = {c: i for i, c in enumerate(t["pool"]["survivor"])}
    return sorted((c for c in union if c in order),
                  key=lambda c: (-cnt.get(c, 0), order.get(c, 10 ** 6)))


def all_hunters(tree=None, halves=None) -> list:
    """榜上要显示的监管角色：选手实际用过的那些，按本季出场次数降序。

    图鉴里 36 个监管，19 个本季无人使用，分数全是空值。
    只保留出现在选手分布 dist 里的角色。
    """
    cnt = hunter_counts(halves)
    t = _tree(tree)
    used = set()
    for info in t["players"].values():
        used |= set(info.get("dist") or {})
        used |= set((info.get("raw_count") or {}).keys())
    order = {c: i for i, c in enumerate(t["pool"]["hunter"])}
    return sorted((c for c in used if c in cnt),
                  key=lambda c: (-cnt[c], order.get(c, 10 ** 6)))


def hunter_counts(halves=None) -> dict:
    """每个监管的本季出场次数，供页面标注。

    halves 传入时直接用，避免一次流程里重复读 bp_core.json。
    """
    from collections import Counter
    hs = halves if halves is not None else _halves()
    return dict(Counter(h["hunter"] for h in hs))


def survivor_counts(halves=None) -> dict:
    """每个求生角色的本季出场次数。

    halves 传入时直接用，避免一次流程里重复读 bp_core.json。
    """
    from collections import Counter
    hs = halves if halves is not None else _halves()
    return dict(Counter(c for h in hs for c in (h.get("survivors") or [])))


# ── 页面读取入口（只读）────────────────────────────────────
# 旧机制下这两个函数会先看 data/custom/ 有没有覆盖，没有才回退到基础数据。
# 旧的写盘机制已下线，data/custom/ 不再存在，所以永远返回基础数据。
def load_roster() -> dict:
    """当前生效的各队角色池。

    只保留图鉴内的求生角色：角色池文件里若残留了不在图鉴池内的名字，
    这里剔除，避免页面上的勾选数比实际可见的 chips 还多。
    """
    keep = set(all_survivors())
    return {t: [c for c in cs if c in keep] for t, cs in base_roster().items()}


def load_scores(mode: str = MODE_ADJUSTED) -> dict:
    """当前生效的分数表 {角色: {监管: {score, n}}}。

    mode 决定含不含选手熟练度修正，语义同 base_cells()。

    只保留图鉴内的角色（角色和监管都要在池子里），
    保证页面只按当前范围显示。
    """
    return base_cells(mode)


def roster_status() -> str:
    """角色池来源标记。旧的写盘机制已下线，只剩基准数据。"""
    return "base"


def scores_status() -> str:
    """分数表来源标记。旧的写盘机制已下线，只剩基准数据。"""
    return "base"
