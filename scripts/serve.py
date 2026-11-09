# -*- coding: utf-8 -*-
"""带保存接口的静态服务：在本地起一个 http server，额外支持
GET  /api/roster       —— 各队求生角色池（只读，基准数据）
GET  /api/scores       —— 监管 × 求生 分数总榜（只读，基准数据）
GET  /api/player_scores—— 选手榜（每个监管选手 × 求生 × 他用过的监管）
GET  /api/maps         —— 各图局数、实测逃脱、监管出场与倍数
GET  /api/map_scores   —— 某张图的实测表（只含该图出场过的角色）
GET  /api/halves       —— 按地图/角色/选手筛出的半局明细
GET  /api/solid        —— 救人位名单（报告页按当前阵容实时计数用）
POST /api/recompute    —— 「重新搜索」：新建引擎，用当前数据重算
GET  /api/generate/meta—— 报告生成页的元数据（战队、监管选手、选手折合分、角色池）
POST /api/generate     —— 生成一份 BP 报告并存进 data/reports/

════════════════════════════════════════════════════════════════
说明
════════════════════════════════════════════════════════════════
原来的「用户自定义数据」机制（data/custom/ 写盘 + 两个 save 接口）已下线。
现在数据的流向是：
    只读基准      data/ 根目录的预制数据
    可改配置      data/hunter/、data/survivor/、data/reports/ 三个目录
    一次生成      页面导入的监管配置 → scores_override
                  页面导入的角色池   → pool_override
引擎不落盘、不留残余状态。

════════════════════════════════════════════════════════════════
用法
════════════════════════════════════════════════════════════════
    python scripts/serve.py            # 监听 8765
    python scripts/serve.py 8080       # 换端口
"""
import json
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import custom_data  # noqa: E402
import file_api  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _combos_arg(raw) -> int:
    """「可选 ban 组合」数量：钳在 1~16，非法值退回 8。

    16 组是耗时与信息量的折中（实测第 2 局单组约 2.8 秒）。
    """
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 8
    return max(1, min(16, n))


def _list_reports() -> list:
    """列出 data/reports/ 下已有的报告（只取 meta，够生成页显示数量了）。

    报告本身是独立文件，报告页靠「导入」读取它们；
    这里只是给生成页显示「已有 N 份报告」，不维护任何索引。
    """
    out = []
    base = ROOT / "data" / "reports"
    if not base.exists():
        return out
    for f in sorted(base.iterdir()):
        if not f.is_file() or f.suffix.lower() != ".json":
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        m = d.get("meta")
        if isinstance(m, dict) and m.get("team"):
            out.append(m)
    return out


def _validate_core(halves, meta) -> str:
    """校验原始数据。返回错误字符串，通过则返回空串。

    具体规则在 core_check.py 里，命令行也能单独跑：
        python scripts/core_check.py
    """
    import core_check
    issues = core_check.check(halves, meta)
    if not issues:
        return ""
    texts = core_check.issues_text(issues)
    head = f"数据不合逻辑（共 {len(issues)} 处）："
    detail = "；".join(texts[:5])
    if len(texts) > 5:
        detail += f" …… 还有 {len(texts) - 5} 处"
    return head + detail


class Handler(SimpleHTTPRequestHandler):
    # 端口探测靠 bind 试错，服务器本身也必须用同样的判定，
    # 否则探测说「可用」但实际 bind 失败，会退回到硬报错。
    allow_reuse_address = False

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/api/recompute":
            return self._api_recompute()
        if self.path == "/api/core":
            return self._api_core_write()
        if self.path == "/api/core/check":
            return self._api_core_check()
        if self.path == "/api/generate":
            return self._api_generate()
        if self.path.startswith("/api/files/list"):
            return self._api_files_list()
        if self.path.startswith("/api/files/read"):
            return self._api_files_read()
        if self.path.startswith("/api/files/write"):
            return self._api_files_write()
        self.send_error(404)

    def do_GET(self):
        # 只拦 /api/ 开头的，其余交给静态文件服务
        if self.path.startswith("/api/roster"):
            return self._api_roster_get()
        if self.path.startswith("/api/scores"):
            return self._api_scores_get()
        if self.path.startswith("/api/maps"):
            return self._api_maps()
        if self.path.startswith("/api/map_pick"):
            return self._api_map_pick()
        if self.path.startswith("/api/map_scores"):
            return self._api_map_scores()
        if self.path.startswith("/api/matchup"):
            return self._api_matchup()
        if self.path.startswith("/api/tree"):
            return self._api_tree()
        if self.path.startswith("/api/solid"):
            return self._api_solid()
        if self.path.startswith("/api/halves"):
            return self._api_halves()
        if self.path.startswith("/api/core"):
            return self._api_core_get()
        if self.path.startswith("/api/expect/meta"):
            return self._api_expect_meta()
        if self.path.startswith("/api/generate/meta"):
            return self._api_generate_meta()
        if self.path == "/api/player_scores":
            return self._api_player_scores()
        if self.path.startswith("/api/files/list"):
            return self._api_files_list()
        if self.path.startswith("/api/files/read"):
            return self._api_files_read()
        if self.path.startswith("/api"):
            return self._json(404, {"ok": False, "error": "unknown api"})
        return super().do_GET()

    # ── 文件读写（给页面的导入/导出子窗口用） ──────────────
    def _api_files_list(self):
        kind = "import"
        prefix = ""
        pattern = ""
        player = team = ""
        game = ""
        dirs = []
        with_meta = False
        if "?" in self.path:
            q = self.path.split("?", 1)[1]
            for kv in q.split("&"):
                if kv.startswith("kind="):
                    kind = kv[5:]
                elif kv.startswith("prefix="):
                    prefix = urllib.parse.unquote(kv[7:])
                elif kv.startswith("pattern="):
                    pattern = urllib.parse.unquote(kv[8:])
                elif kv.startswith("player="):
                    player = urllib.parse.unquote(kv[7:])
                elif kv.startswith("team="):
                    team = urllib.parse.unquote(kv[5:])
                elif kv.startswith("game="):
                    game = kv[5:]
                elif kv.startswith("dir="):
                    dirs = [urllib.parse.unquote(v)
                            for v in kv[4:].split(",") if v]
                elif kv == "meta=1":
                    with_meta = True
        try:
            # 报告版本：<监管id>_<数字>_vs_<队伍名>_game<局数>.json
            if player and team:
                pattern = file_api.report_pattern(player, team, game or 1)
                res = file_api.list_files("export", pattern=pattern,
                                         dirs=[file_api.DIR_REPORTS])
                res["suggested"] = file_api.next_report_name(
                    player, team, game or 1)
                self._json(200, {"ok": True, **res})
                return
            res = file_api.list_files(kind, prefix=prefix, pattern=pattern,
                                      dirs=dirs or None, with_meta=with_meta)
            if prefix and not pattern:
                sub = (dirs[0] if dirs else file_api.DIR_HUNTER)
                res["suggested"] = file_api.next_name(prefix, sub)
            self._json(200, {"ok": True, **res})
        except Exception as e:
            self._json(400, {"ok": False, "error": str(e)})

    def _api_files_read(self):
        try:
            data = self._read_json()
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"bad json: {e}"})
        try:
            obj = file_api.read_json(data.get("path"))
            self._json(200, {"ok": True, "path": data.get("path"), "data": obj})
        except FileNotFoundError as e:
            self._json(404, {"ok": False, "error": str(e)})
        except Exception as e:
            self._json(400, {"ok": False, "error": str(e)})

    def _api_files_write(self):
        try:
            data = self._read_json()
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"bad json: {e}"})
        try:
            overwrite = bool(data.get("overwrite"))
            # 覆盖已有版本：path 就是列表里那个文件的完整相对路径。
            # 新建版本：只给 prefix + dir，文件名由服务端按规范生成，
            # 页面无法自己决定存成什么名字。
            if overwrite:
                res = file_api.write_json(data.get("path"), data.get("data"),
                                          overwrite=True)
            else:
                prefix = data.get("prefix")
                sub = data.get("dir")
                if not prefix or not sub:
                    return self._json(400, {"ok": False,
                                            "error": "缺少 prefix 或 dir"})
                name = file_api.next_name(prefix, sub)
                res = file_api.write_json(f"{sub.rstrip('/')}/{name}",
                                          data.get("data"), overwrite=False)
            self._json(200, {"ok": True, **res})
        except FileExistsError as e:
            self._json(409, {"ok": False, "error": str(e)})
        except Exception as e:
            self._json(400, {"ok": False, "error": str(e)})

    # ── 基准数据（只读）────────────────────────────────────
    def _api_roster_get(self):
        self._json(200, {
            "status": custom_data.roster_status(),
            "teams": custom_data.load_roster(),
            "base": custom_data.base_roster(),
            "all_teams": custom_data.all_teams(),
            "all_survivors": custom_data.all_survivors(),
        })

    def _api_scores_get(self):
        self._json(200, {
            "status": custom_data.scores_status(),
            "cells": custom_data.load_scores(),
            "all_survivors": custom_data.all_survivors(),
            "all_hunters": custom_data.all_hunters(),
            "hunter_counts": custom_data.hunter_counts(),
            "survivor_counts": custom_data.survivor_counts(),
            "player_teams": self._player_teams(),
        })

    @staticmethod
    def _player_teams() -> dict:
        """监管选手 → 所属队伍（原始数据页用来收窄选手下拉）。"""
        try:
            d = json.loads((ROOT / "data" / "hunter_players.json")
                           .read_text(encoding="utf-8"))
        except Exception:
            return {}
        return {name: (info or {}).get("team") or ""
                for name, info in (d.get("players") or {}).items()}

    def _api_expect_meta(self):
        self._json(200, {
            "scores_status": custom_data.scores_status(),
            "roster_status": custom_data.roster_status(),
        })

    def _api_maps(self):
        """地图统计：每张图的局数、实测逃脱、监管出场、求生职能倾向。

        只读，由 bp_core.json 的 map 字段即时算出（不落盘）。
        多张图的倍数表是给页面看「这张图谁更可能上场」的。
        参数 ?map=图名 只把 stats 收窄到那一张，maps 仍是全量列表
        （页面下拉需要完整的候选）；未知地图会得到空的 stats。
        """
        try:
            from bp_search_engine import Engine
            eng = Engine()
            name = ""
            if "?" in self.path:
                q = self.path.split("?", 1)[1]
                for kv in q.split("&"):
                    if kv.startswith("map="):
                        name = urllib.parse.unquote(kv[4:])
            data = eng.map_stats(name)
            roles = {m: eng.map_surv_role_mix(m) for m in data}
            for m in data:
                data[m]["roles"] = roles.get(m) or {}
            self._json(200, {
                "ok": True,
                "maps": eng.maps(),
                "stats": data,
                "hunter_prior": {k: round(v, 4) for k, v in eng._map_prior.items()},
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def _api_map_scores(self):
        """某张图的实测表：只含这张图上出场过的角色。

        ?map=图名 必填。
        """
        try:
            import map_scores
            name = ""
            if "?" in self.path:
                q = self.path.split("?", 1)[1]
                for kv in q.split("&"):
                    if kv.startswith("map="):
                        name = urllib.parse.unquote(kv[4:])
            if not name:
                return self._json(400, {"ok": False, "error": "缺少 map 参数"})
            self._json(200, {"ok": True, **map_scores.table(name)})
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def _api_matchup(self):
        """对位情报：互斥对（某求生面对某监管时出场率显著低于赛季平均）。

        只依赖赛季行为统计与「哪支队伍、哪个对手」这两个筛选，
        与分数表、地图、报告里的 ban 组合都无关。

        ?player= 监管选手（必填）  ?team= 我方队伍（默认 ACT）
        ?limit= 条数（默认 40，最多 200）
        """
        try:
            from bp_search_engine import Engine
            import ban_intel
            args = {"player": "", "team": "ACT", "limit": "40"}
            if "?" in self.path:
                q = self.path.split("?", 1)[1]
                for kv in q.split("&"):
                    k, _, v = kv.partition("=")
                    if k in args:
                        args[k] = urllib.parse.unquote(v)
            player = args["player"]
            team = args["team"] or "ACT"
            try:
                limit = max(1, min(200, int(args["limit"] or 40)))
            except ValueError:
                limit = 40
            eng = Engine()
            if player and player not in eng.players:
                return self._json(400, {"ok": False,
                                        "error": f"未知监管选手 {player}"})
            if team not in eng.rosters:
                return self._json(400, {"ok": False, "error": f"未知队伍 {team}"})
            rows = ban_intel.matchup_alerts(eng, team, player, top=limit)
            S = ban_intel.stats()
            self._json(200, {
                "ok": True,
                "player": player,
                "team": team,
                "players": sorted(eng.players),
                "teams": sorted(eng.rosters),
                "pool": [c for c in eng.pool_of(team)],
                "hunter_halves": {h: S["hunter_halves"].get(h, 0)
                                  for h in ((eng.players.get(player) or {})
                                            .get("dist") or {})},
                "halves_n": S["halves_n"],
                "rows": rows,
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def _api_solid(self):
        """救人位名单。

        报告页的「最终阵容」要按当前主线阵容实时数「救人位几个」，
        而早期生成的报告里没有 meta.solid_chars。名单以后端为准下发，
        页面就不用自己再维护一份。
        """
        from bp_search_engine import SOLID
        self._json(200, {"chars": sorted(SOLID)})

    def _api_tree(self):
        """交互树的下一层：给定一个搜索状态，返回该节点的全部候选。

        报告里已经带了预展开的两层，更深的层按需请求 ——
        整棵树一次性铺开会有几百 KB，而实际用到的往往只有一条路径。

        ?team= ?player= ?game= ?idx= ?taken= ?kind=
        ?map= ?lban= ?lpick= ?spicks= ?gone_hunt=    （后四项用逗号分隔）
        """
        try:
            from bp_search_engine import Engine
            import bp_coach_report as coach
            args = {"team": "", "player": "", "game": "1", "idx": "0",
                    "taken": "0", "kind": "", "map": "", "lban": "",
                    "lpick": "", "spicks": "", "gone_hunt": ""}
            if "?" in self.path:
                q = self.path.split("?", 1)[1]
                for kv in q.split("&"):
                    k, _, v = kv.partition("=")
                    if k in args:
                        args[k] = urllib.parse.unquote(v)

            def split(k):
                raw = (args[k] or "").strip()
                return [x for x in raw.split(",") if x] if raw else []

            team, player = args["team"], args["player"]
            if not team or not player:
                return self._json(400, {"ok": False,
                                        "error": "缺少 team 或 player"})
            try:
                game = int(args["game"])
                idx = int(args["idx"])
                taken = int(args["taken"])
            except ValueError:
                return self._json(400, {"ok": False, "error": "参数必须是整数"})

            eng = Engine()
            if player not in eng.players:
                return self._json(400, {"ok": False,
                                        "error": f"未知监管选手 {player}"})
            if team not in eng.rosters:
                return self._json(400, {"ok": False,
                                        "error": f"未知队伍 {team}"})
            stages = eng._stages(game)
            if not (0 <= idx < len(stages)):
                return self._json(400, {"ok": False, "error": "idx 超出范围"})
            eng.map_name = args["map"] or ""

            state = {"idx": idx, "taken": taken, "kind": stages[idx][1],
                     "lban": set(split("lban")), "lpick": set(split("lpick")),
                     "spicks": set(split("spicks"))}
            nodes = coach.build_tree(eng, team, player, game, state,
                                     depth_left=2,
                                     gone_hunt=split("gone_hunt"),
                                     map_name=eng.map_name or None)
            layer = nodes or {}
            self._json(200, {"ok": True,
                             "stage": layer.get("stage", stages[idx][0]),
                             "kind": layer.get("kind", stages[idx][1]),
                             "name": layer.get("name", ""),
                             "actor": layer.get("actor", ""),
                             "nodes": layer.get("nodes", [])})
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def _api_halves(self):
        """符合筛选条件的半局明细，按赛季时间顺序。

        ?map= ?survivor= ?hunter= ?player= 全部可选，同时给就是取交集。
        """
        try:
            import map_scores
            args = {"map": "", "survivor": "", "hunter": "", "player": ""}
            if "?" in self.path:
                q = self.path.split("?", 1)[1]
                for kv in q.split("&"):
                    k, _, v = kv.partition("=")
                    if k in args:
                        args[k] = urllib.parse.unquote(v)
            self._json(200, {"ok": True, "filter": args,
                             **map_scores.halves(args["map"], args["survivor"],
                                                 args["hunter"], args["player"])})
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def _api_map_pick(self):
        """地图选择偏好：某队在各局次选了什么图。

        ?team=队名 返回单队视图；不传则返回全部队伍。
        图权按局次轮转，主客队不同，所以这里的统计必须先定主队。
        """
        try:
            import map_pick
            team = ""
            if "?" in self.path:
                q = self.path.split("?", 1)[1]
                for kv in q.split("&"):
                    if kv.startswith("team="):
                        team = urllib.parse.unquote(kv[5:])
            if team:
                self._json(200, {"ok": True, **map_pick.one(team)})
            else:
                self._json(200, {"ok": True, **map_pick.stats()})
        except KeyError as e:
            self._json(404, {"ok": False, "error": str(e)})
        except Exception as e:
            self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    # ── 原始数据 bp_core（读写）────────────────────────────
    def _api_core_check(self):
        """逻辑校验（不落盘）：返回每条问题涉及的行号和说明。"""
        try:
            data = self._read_json()
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"bad json: {e}"})
        import core_check
        halves = data.get("halves")
        if not isinstance(halves, list):
            return self._json(400, {"ok": False, "error": "halves 必须是数组"})
        meta = data.get("meta") or {}
        issues = core_check.check(halves, meta)
        self._json(200, {
            "ok": True,
            "issues": issues,
            "bad_rows": core_check.bad_rows(issues),
            "count": len(issues),
        })

    def _api_core_get(self):
        """原始数据：meta + 全部半局 + 主客队映射，给表格页展示。"""
        try:
            import map_pick
            c = json.loads((ROOT / "data" / "bp_core.json").read_text(encoding="utf-8"))
            meta = c.get("meta") or {}
            hp = meta.get("home_pairs")
            if not isinstance(hp, dict) or not hp:
                hp = {"|".join(sorted(k)): v
                      for k, v in map_pick.HOME_PAIRS.items()}
            halves = c.get("halves") or []
            self._json(200, {
                "ok": True,
                "meta": meta,
                "halves": halves,
                "home_pairs": hp,
                "teams": sorted({h["survivor_team"] for h in halves}
                                | {h["hunter_team"] for h in halves}),
                "maps": sorted({h["map"] for h in halves if h.get("map")}),
                "pool_survivor": self._role_pool("survivor", halves),
                "pool_hunter": self._role_pool("hunter", halves),
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    @staticmethod
    def _role_pool(side, halves):
        """该阵营的全量角色池（图鉴里的全部角色），加上数据里出现过的。

        图鉴（data/bp_tree.json 的 pool）是权威的完整名单；
        数据里可能有个别图鉴没收录的角色，这里一并补上。
        """
        pool = []
        try:
            t = json.loads((ROOT / "data" / "bp_tree.json").read_text(encoding="utf-8"))
            pool = list(t.get("pool", {}).get(side) or [])
        except Exception:
            pool = []
        for h in halves:
            names = []
            if side == "survivor":
                names = list(h.get("survivors") or []) + list(h.get("hunter_bans") or [])
            else:
                names = list(h.get("survivor_bans") or [])
                if h.get("hunter"):
                    names.append(h["hunter"])
            for c in names:
                if c and c not in pool:
                    pool.append(c)
        return pool

    def _api_core_write(self):
        """保存原始数据：备份 → 写回 bp_core.json →（数据合法才）重建派生数据。

        数据不合逻辑时照样写盘（用户可以先把半录的数据存下来），
        但**不重建派生数据**——不合逻辑的数据算出来的分数没有意义。
        此时下游各页面用的还是上一次重算的结果，页面上会明确警告。

        写盘前先把现有 bp_core.json 备份到 bp_core.json.bak，
        万一改错了还能手工还原。
        """
        try:
            data = self._read_json()
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"bad json: {e}"})

        halves = data.get("halves")
        if not isinstance(halves, list) or not halves:
            return self._json(400, {"ok": False, "error": "halves 必须是非空数组"})

        old = json.loads((ROOT / "data" / "bp_core.json").read_text(encoding="utf-8"))
        meta = data.get("meta") or old.get("meta") or {}

        # 备份 → 原子写
        core_path = ROOT / "data" / "bp_core.json"
        bak = ROOT / "data" / "bp_core.json.bak"
        old_text = core_path.read_text(encoding="utf-8")
        bak.write_text(old_text, encoding="utf-8")
        # 额外在 tmp/ 留一份带时间戳的快照，多存几次以便回退到任意一次保存前
        snaps = ROOT / "tmp" / "bp_core_snapshots"
        try:
            snaps.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            (snaps / ("bp_core-%s.json" % stamp)).write_text(old_text, encoding="utf-8")
        except Exception:
            pass
        tmp = core_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"meta": meta,
                                   "halves": halves},
                                  ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(core_path)

        # 数据不合逻辑 → 只存不算。派生数据保持上一次的结果。
        import core_check
        issues = core_check.check(halves, meta)
        if issues:
            custom_data.clear_cache()
            return self._json(200, {
                "ok": True,
                "halves": len(halves),
                "valid": False,
                "rebuilt": False,
                "issues": issues,
                "bad_rows": core_check.bad_rows(issues),
                "count": len(issues),
                "backup": "data/bp_core.json.bak",
                "snapshots": "tmp/bp_core_snapshots/",
                "message": "已保存，但数据不合逻辑，未重算派生数据。"
                           "其他页面用的还是上一次的结果。",
            })

        # 重建所有派生数据
        try:
            import rebuild
            timing = rebuild.rebuild()
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._json(500, {
                "ok": False,
                "error": f"已写回 bp_core，但重建派生数据失败：{type(e).__name__}: {e}",
                "backup": "data/bp_core.json.bak"})

        custom_data.clear_cache()
        self._json(200, {"ok": True, "halves": len(halves),
                         "valid": True,
                         "rebuilt": True,
                         "issues": [],
                         "backup": "data/bp_core.json.bak",
                         "snapshots": "tmp/bp_core_snapshots/",
                         "timing": timing})

    def _api_player_scores(self):
        """选手榜：每个监管选手 × 求生角色 × 他用过的监管 的 实测/全局/折合。"""
        p = ROOT / "data" / "player_scores.json"
        if not p.exists():
            try:
                import subprocess
                subprocess.run([sys.executable,
                                str(ROOT / "scripts" / "build_player_scores.py")],
                               capture_output=True, timeout=180)
            except Exception:
                pass
        if not p.exists():
            return self._json(500, {"ok": False,
                                    "error": "player_scores.json 不存在且生成失败"})
        d = json.loads(p.read_text(encoding="utf-8"))
        self._json(200, {
            "meta": d["meta"],
            "players": d["players"],
            "player_halves": d["player_halves"],
            "player_hunters": d["player_hunters"],
            "teams_of_player": self._teams_of_player(),
        })

    def _teams_of_player(self):
        tree = json.loads((ROOT / "data" / "bp_tree.json").read_text(encoding="utf-8"))
        return {p: v.get("team") for p, v in tree["players"].items()}

    # ── 报告生成 ──────────────────────────────────────────
    def _player_cells(self):
        """每个监管选手的折合分格子：{选手: {求生: {监管: score}}}。

        生成页的「监管数据」先选一位参考选手，以他的实测折合分覆盖基准表。
        """
        p = ROOT / "data" / "player_scores.json"
        if not p.exists():
            try:
                import subprocess
                subprocess.run([sys.executable,
                                str(ROOT / "scripts" / "build_player_scores.py")],
                               capture_output=True, timeout=180)
            except Exception:
                pass
        if not p.exists():
            return {}, {}
        d = json.loads(p.read_text(encoding="utf-8"))
        cells, used = {}, {}
        for name, rows in (d.get("players") or {}).items():
            flat, hs = {}, set()
            for surv, hunters in rows.items():
                for h, v in hunters.items():
                    sc = v.get("score") if isinstance(v, dict) else v
                    if isinstance(sc, (int, float)):
                        flat.setdefault(surv, {})[h] = round(float(sc), 3)
                        hs.add(h)
            if flat:
                cells[name] = flat
                used[name] = sorted(hs, key=str)
        return cells, used

    def _api_generate_meta(self):
        """生成页要用的全部元数据。

        监管数据 = 数据页那张「求生 × 监管」折合分表。
        生成页现在不读「自定义」那份了：表格和角色池都由页面上的
        导入配置 + 手工修改决定，只在本次生成里生效。
        """
        tree = json.loads((ROOT / "data" / "bp_tree.json").read_text(encoding="utf-8"))
        players = {p: v.get("team") for p, v in tree["players"].items()}
        base_roster = custom_data.base_roster()
        base_cells = custom_data.base_cells()
        reports = _list_reports()
        player_cells, player_hunters = self._player_cells()
        # 地图列表给生成页的下拉用。局数一并给出，让教练知道哪些图样本太少。
        try:
            from bp_search_engine import Engine
            _eng = Engine()
            _stats = _eng.map_stats()
            map_rows = [{"name": m, "halves": _stats[m]["halves"],
                         "escape": _stats[m]["escape"]}
                        for m in _eng.maps()]
        except Exception:
            map_rows = []
        self._json(200, {
            "teams": sorted(base_roster),
            "players": [{"name": p, "team": t} for p, t in players.items()],
            "all_survivors": custom_data.all_survivors(),
            "all_hunters": custom_data.all_hunters(),
            "hunter_counts": custom_data.hunter_counts(),
            "survivor_counts": custom_data.survivor_counts(),
            "maps": map_rows,
            "roster": {"base": base_roster},
            # data/hunter/ 下现有的监管配置，按文件名排序。
            # 生成页启动时默认载入第一份，省得每次都手动导入。
            "hunter_files": self._hunter_files(),
            # data/survivor/ 下现有的角色池配置，按文件名排序。
            # 生成页启动时默认挑 ACT 那一份。
            "roster_files": self._roster_files(),
            "scores": {
                "base": base_cells,
                "player_cells": player_cells,
                "player_hunters": player_hunters,
            },
            "reports": reports,
        })

    def _hunter_files(self):
        """data/hunter/ 下的监管配置清单，按文件名排序。"""
        try:
            res = file_api.list_files("export", dirs=[file_api.DIR_HUNTER])
        except Exception:
            return []
        return [{"path": f["path"], "name": f["name"]}
                for f in res.get("files") or []]

    def _roster_files(self):
        """data/survivor/ 下的角色池配置清单，按文件名排序。"""
        try:
            res = file_api.list_files("export", dirs=[file_api.DIR_SURVIVOR])
        except Exception:
            return []
        return [{"path": f["path"], "name": f["name"]}
                for f in res.get("files") or []]

    def _api_generate(self):
        """生成一份报告。算完按规范命名存进 data/reports/。"""
        try:
            data = self._read_json()
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"bad json: {e}"})
        team = data.get("team")
        player = data.get("player")
        try:
            game = int(data.get("game") or 1)
            n_hunters = int(data.get("hunters") or 4)
            bantop = int(data.get("bantop") or 5)
        except (TypeError, ValueError):
            return self._json(400, {"ok": False, "error": "局次/监管数/ban 组合数必须是整数"})
        if not team or not player:
            return self._json(400, {"ok": False, "error": "缺少战队或监管选手"})
        if not 1 <= game <= 5:
            return self._json(400, {"ok": False, "error": f"局次 {game} 不在 1~5 之间"})
        if not 1 <= n_hunters <= 12:
            return self._json(400, {"ok": False, "error": "监管数应在 1~12 之间"})
        if not 2 <= bantop <= 10:
            return self._json(400, {"ok": False,
                                    "error": "ban 组合数应在 2~10 之间（至少要凑出一对 ban 位）"})
        gban_s = data.get("global_ban_surv") or []
        gban_h = data.get("global_ban_hunt") or []
        for name, v in (("global_ban_surv", gban_s), ("global_ban_hunt", gban_h)):
            if not isinstance(v, list):
                return self._json(400, {"ok": False, "error": f"{name} 必须是数组"})
        gban_s = [str(c).strip() for c in gban_s if str(c).strip()]
        gban_h = [str(c).strip() for c in gban_h if str(c).strip()]
        # 全局禁选数量必须与局数严格吻合：
        # 第 x 局前面打了 x-1 局，每局我方用掉 4 个求生、对方用掉 1 个监管，
        # 所以恰好 4(x-1) 个求生、x-1 个监管。多一个少一个都不放行。
        need_s, need_h = (game - 1) * 4, game - 1
        if len(gban_s) != need_s or len(gban_h) != need_h:
            bad = []
            if len(gban_s) != need_s:
                bad.append(f"求生需 {need_s} 个（现 {len(gban_s)} 个）")
            if len(gban_h) != need_h:
                bad.append(f"监管需 {need_h} 个（现 {len(gban_h)} 个）")
            return self._json(400, {
                "ok": False,
                "error": f"第 {game} 局的全局禁选数量不对："
                         + "，".join(bad)
                         + f"。前面 {game - 1} 局每局用掉 4 个求生、1 个监管，"
                           f"必须恰好禁 {need_s} 个求生、{need_h} 个监管。"})
        # 重名要去重，否则数量对不上
        gban_s = list(dict.fromkeys(gban_s))
        gban_h = list(dict.fromkeys(gban_h))
        if len(gban_s) != need_s or len(gban_h) != need_h:
            return self._json(400, {
                "ok": False,
                "error": f"第 {game} 局的全局禁选里有重复角色，"
                         f"去重后求生 {len(gban_s)}/{need_s}、监管 {len(gban_h)}/{need_h}。"})
        # 第 1 步的最终分数表（可选）：导入的配置 + 页面上的手工修改。
        # 只用于本次计算，不落盘。
        cells = data.get("cells")
        if cells is not None and not isinstance(cells, dict):
            return self._json(400, {"ok": False, "error": "cells 必须是对象"})
        pool = data.get("pool")
        if pool is not None and not isinstance(pool, list):
            return self._json(400, {"ok": False, "error": "pool 必须是数组"})
        try:
            from bp_search_engine import Engine
            from bp_combo_report import compute as bp_compute
            # scores_override：导入的监管配置 + 页面手工修改。
            # 每格可能是 {"score": x, "n": y} 或纯数字，统一压成 float。
            scores_ov = None
            if cells:
                scores_ov = {}
                for c, hs in cells.items():
                    row = {}
                    for h, v in hs.items():
                        num = v.get("score") if isinstance(v, dict) else v
                        if num is None:
                            continue
                        try:
                            row[h] = float(num)
                        except (TypeError, ValueError):
                            continue
                    if row:
                        scores_ov[c] = row
            # pool_override：本次生成选的求生角色池配置
            pool_ov = {team: list(pool)} if pool else None
            eng = Engine(scores_override=scores_ov, pool_override=pool_ov)
            # 本局地图：只影响候选排序与加权，不改任何分数格。
            # 传空串或未传 = 不按地图加权，与旧行为一致。
            map_name = data.get("map") or ""
            if map_name and map_name not in eng.maps():
                return self._json(400, {"ok": False,
                                        "error": f"未知地图 {map_name}"})
            res = bp_compute(eng, team, player, game, n_hunters, bantop,
                             n_combos=_combos_arg(data.get("combos")),
                             global_ban_surv=gban_s, global_ban_hunt=gban_h,
                             our_pool=pool,
                             scores_source=data.get("scores_source"),
                             roster_source=data.get("roster_source"),
                             map_name=map_name or None)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        # 生成的报告存到 data/reports/，文件名按规范自动编号。
        # 两个版本号取自本次用的两份配置文件的数字版本：
        #   监管那一位 ← 监管数据配置（data/hunter/）
        #   队伍那一位 ← 求生角色池配置（data/survivor/）
        try:
            hv = file_api.ver_of(data.get("scores_source"), 1)
            tv = file_api.ver_of(data.get("roster_source"), 1)
            path = file_api.next_report_name(player, team, game,
                                             hunter_ver=hv, team_ver=tv)
            file_api.write_json(f"{file_api.DIR_REPORTS}/{path}", res,
                                overwrite=False)
        except FileExistsError:
            path = None
        except Exception as e:
            path = None
            print(f"  报告写入 data/reports 失败：{e}", flush=True)
        self._json(200, {"ok": True, "meta": res["meta"],
                         "combos": len(res.get("combos") or []),
                         "branches": len(res.get("branches") or []),
                         "saved": path})

    def _api_recompute(self):
        """「重新搜索」按钮：用当前自定义数据新建引擎并重算。

        引擎每次都是新建的，不共享缓存，所以这里是真的重算。
        """
        try:
            data = self._read_json()
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"bad json: {e}"})
        kind = data.get("kind")
        try:
            from bp_search_engine import Engine
            eng = Engine()
            if kind == "lineup":
                from lineup_expect import evaluate
                chars = data.get("lineup") or []
                if len(chars) < 4:
                    return self._json(400, {
                        "ok": False, "error": f"阵容只有 {len(chars)} 人，需要 4 人"})
                player = data.get("player")
                if not player:
                    return self._json(400, {
                        "ok": False, "error": "必须选择监管选手 —— 期望分按选手的折合分计算，不做无选手的全局评估"})
                map_name = data.get("map") or ""
                if map_name and map_name not in eng.maps():
                    return self._json(400, {"ok": False,
                                            "error": f"未知地图 {map_name}"})
                res = evaluate(eng, chars, player,
                               data.get("hunters") or None,
                               map_name=map_name or None)
                return self._json(200, {"ok": True, "result": res})
            if kind == "bp":
                from bp_combo_report import compute as bp_compute
                map_name = data.get("map") or ""
                if map_name and map_name not in eng.maps():
                    return self._json(400, {"ok": False,
                                            "error": f"未知地图 {map_name}"})
                res = bp_compute(eng, data.get("team"), data.get("player"),
                                 int(data.get("game") or 3),
                                 int(data.get("hunters") or 4),
                                 int(data.get("bantop") or 4),
                                 _combos_arg(data.get("combos")),
                                 map_name=map_name or None)
                return self._json(200, {"ok": True, "result": res})
            return self._json(400, {"ok": False, "error": f"未知的计算类型 {kind}"})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})


    def _read_json(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode("utf-8")
        return json.loads(raw)

    def log_message(self, fmt, *args):
        # 只打 POST，静态请求太吵
        if args and str(args[0]).startswith(("POST", "4", "5")):
            print(fmt % args, flush=True)


def _pick_port(preferred: int, tries: int = 20) -> int:
    """找一个能用的端口：优先用 preferred，被占用就往后顺延。

    小白最常撞的坑是「端口被占用 → 服务起不来 → 不知道为什么」。
    这里直接自动换端口，保证双击就能用。

    探测方式：尝试 bind + listen，能绑上就说明没人占用。
    注意不能用 SO_REUSEADDR —— 那样即使端口已被别的服务
    占用也能 bind 成功，探测结果会是假的。
    """
    for p in range(preferred, preferred + tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", p))
                s.listen(1)
            return p
        except OSError:
            continue
    raise OSError(f"{preferred}~{preferred + tries - 1} 端口都被占用了，"
                   f"请关闭占用端口的程序后重试")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    try:
        port = _pick_port(port)
    except OSError as e:
        print(f"启动失败：{e}", flush=True)
        return
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}/web/index.html"
    print(f"服务已启动 {url}", flush=True)
    print(f"配置目录：{file_api.DIR_HUNTER}（监管）"
          f" / {file_api.DIR_SURVIVOR}（角色池）"
          f" / {file_api.DIR_REPORTS}（报告）", flush=True)
    print("按 Ctrl+C 停止服务", flush=True)

    # 自动打开浏览器（延迟一点，等服务真正开始监听）
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: _open_browser(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


def _open_browser(url: str) -> None:
    """打开浏览器。无桌面环境（SSH / Docker / 无 GUI 的 Linux）下会失败，
    这属于正常情况 —— 打印网址让用户自己复制即可，不该抛错中断服务。"""
    try:
        ok = webbrowser.open(url)
    except Exception:
        ok = False
    if not ok:
        print(f"未能自动打开浏览器，请手动访问：{url}", flush=True)


if __name__ == "__main__":
    main()
