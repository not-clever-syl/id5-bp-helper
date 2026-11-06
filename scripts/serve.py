# -*- coding: utf-8 -*-
"""带保存接口的静态服务：在本地起一个 http server，额外支持
GET  /api/roster       —— 各队求生角色池（只读，基准数据）
GET  /api/scores       —— 监管 × 求生 分数总榜（只读，基准数据）
GET  /api/player_scores—— 选手榜（每个监管选手 × 求生 × 他用过的监管）
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
import urllib.parse
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import custom_data  # noqa: E402
import file_api  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


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
                                      dirs=dirs or None)
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
            res = file_api.write_json(data.get("path"), data.get("data"),
                                      overwrite=bool(data.get("overwrite")))
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
        })

    def _api_expect_meta(self):
        self._json(200, {
            "scores_status": custom_data.scores_status(),
            "roster_status": custom_data.roster_status(),
        })

    def _api_maps(self):
        """地图统计：每张图的局数、实测逃脱、监管出场、求生职能倾向。

        只读，由 bp_core.json 的 map 字段即时算出（不落盘）。
        多张图的倍数表是给页面看「这张图谁更可能上场」的，
        参数 ?map=图名 只返回那一张。
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
            "scores": {
                "base": base_cells,
                "player_cells": player_cells,
                "player_hunters": player_hunters,
            },
            "reports": reports,
        })

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
                             n_combos=int(data.get("combos") or 8),
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
                                 int(data.get("combos") or 8),
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
