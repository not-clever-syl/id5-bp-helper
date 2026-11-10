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

_TREE = None
_QUERY = None
_DS = None


def _tree(tree=None):
    global _TREE
    if tree is not None:
        return tree
    if _TREE is None:
        p = ROOT / "data" / "bp_tree.json"
        _TREE = json.loads(p.read_text(encoding="utf-8"))
    return _TREE


def _dataset():
    global _DS
    if _DS is None:
        p = ROOT / "data" / "bp_core.json"
        _DS = json.loads(p.read_text(encoding="utf-8"))
    return _DS


def _halves():
    return _dataset()["halves"]


def _survivor_counts() -> dict:
    """每个求生角色的本季出场次数。

    战报只记整队逃脱数，一局里每个出场求生各记一次，所以这里就是
    「该角色本季被选出场的次数」。
    """
    from collections import Counter
    return dict(Counter(c for h in _halves()
                        for c in (h.get("survivors") or [])))


def _query():
    global _QUERY
    if _QUERY is None:
        p = ROOT / "data" / "bp_query.json"
        _QUERY = json.loads(p.read_text(encoding="utf-8"))
    return _QUERY


def clear_cache() -> None:
    """原始数据被页面改过以后调用，让下次读取重新落到磁盘。

    bp_core 是这三份表的共同来源；页面把它写回之后，如果还留着
    这里的缓存，分数表、角色池会继续拿改之前的那份。
    """
    global _TREE, _QUERY, _DS
    _TREE = None
    _QUERY = None
    _DS = None


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
    keep_c = set(all_survivors())
    keep_h = set(all_hunters())
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


def all_survivors(tree=None) -> list:
    """榜上要显示的求生角色：各队角色池的并集。

    图鉴里 53 个求生，有 10 个从没进过任何战队的角色池（击球手、小女孩、
    律师……）。它们在表里全是空格，显示出来只会干扰，所以不列。

    排序用战报里的真实出场次数：教练最常遇到的求生必须排在最前，
    否则找「佣兵」要扫很久。
    """
    cnt = _survivor_counts()
    t = _tree(tree)
    union = set()
    for v in t["teams"].values():
        union |= set(v["roster"])
    order = {c: i for i, c in enumerate(t["pool"]["survivor"])}
    return sorted((c for c in union if c in order),
                  key=lambda c: (-cnt.get(c, 0), order.get(c, 10 ** 6)))


def all_hunters(tree=None) -> list:
    """榜上要显示的监管角色：选手实际用过的那些，按本季出场次数降序。

    图鉴里 36 个监管，19 个本季无人使用，分数全是空值。
    只保留出现在选手分布 dist 里的角色。
    """
    cnt = hunter_counts()
    t = _tree(tree)
    used = set()
    for info in t["players"].values():
        used |= set(info.get("dist") or {})
        used |= set((info.get("raw_count") or {}).keys())
    order = {c: i for i, c in enumerate(t["pool"]["hunter"])}
    return sorted((c for c in used if c in cnt),
                  key=lambda c: (-cnt[c], order.get(c, 10 ** 6)))


def hunter_counts() -> dict:
    """每个监管的本季出场次数，供页面标注。"""
    from collections import Counter
    return dict(Counter(h["hunter"] for h in _halves()))


def survivor_counts() -> dict:
    """每个求生角色的本季出场次数。"""
    return _survivor_counts()


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
