# IVL 2026 秋季赛 · 求生方 BP 辅助工具

把这一季 260 个半局的比赛数据整理成几张表。对面可能会拿谁、我们该 ban 谁、这套阵容好不好打，点两下就能看到。

在本地跑，不联网，也不会上传任何东西。

（提醒一句：这些结果都是从历史比赛里算出来的参考，不是标准答案。真上场还是听教练和选手的。）

## 快速开始

### Windows

1. 装 Python 3.8 或更新的版本：<https://www.python.org/downloads/>，安装时记得勾上 **Add Python to PATH**
2. 在仓库页面点 <kbd>Code</kbd> → <kbd>Download ZIP</kbd>，解压到任意位置
3. 双击 `启动.bat`。浏览器会自动打开，关掉那个黑窗口就停了

### macOS / Linux

```bash
git clone https://github.com/not-clever-syl/id5-bp-helper.git
cd id5-bp-helper
./start.sh
```

如果提示 `Permission denied`，先 `chmod +x start.sh`，或者直接 `sh start.sh`。`Ctrl+C` 停止。

Linux / macOS 上的解释器叫 `python3`，脚本会自己去找，不用你改。

### 想自己起服务

```bash
# Windows
python scripts/serve.py 8766

# macOS / Linux
python3 scripts/serve.py 8766
```

换端口就改数字，端口被占了会自动往后顺延。加上 `--no-browser` 就不会自动开浏览器。然后访问 <http://localhost:8766/web/index.html>。

数据都已经放在仓库里了，克隆下来就能用，不用另下载，也不用编译。更新代码用 `git pull`。

## 实现的辅助功能

| 页面 | 干什么 |
|---|---|
| 数据 | 每个求生角色碰上每个监管，平均能跑掉几个人；也能单独看某个监管选手打得怎么样，以及各支队伍常用的角色 |
| 阵容期望分 | 挑 4 个求生、再选对手的监管选手，看这套阵容碰上他的每个监管大概能拿几分，最危险的排前面 |
| 报告生成 | 选我方队伍、对手的监管选手、第几局，生成一份报告 |
| BP 报告 | 打开生成的报告，按顺序看：对面先 ban 了谁 → 他可能想拿哪个监管 → 我们该 ban 谁 → 我们先选谁 → 后面怎么走 |
| 地图偏好 | 选一支队伍，看他第几局都选过哪些图 |

阵容期望分和报告生成都能选这局打哪张图，也可以不选。选了之后，推荐会往这张图上常见的角色那边偏一些，页面也会顺带列出这张图的常用监管。数据页选地图只是把监管那一列重新排一下、标个倍数，分数本身不变。

**如果某张图这一季只打过两三局，可以干脆不选。** 这么点数据基本看不出什么，选了反而可能把明显该拿的监管挤到后面。这种情况把地图留空，按所有人的平均来算更稳。

不想开网页的话，命令行也能直接出报告：

```bash
python3 scripts/bp_coach_report.py ACT YuM 3 --ban 幻灯师 弓箭手 --map 月亮河公园
python3 scripts/bp_combo_report.py ACT YuM 3 --map 月亮河公园
```

这两个脚本的分工：

- `bp_coach_report.py` 算一组。给定对面 ban 了哪两个角色，把这一局的走向从头到尾推一遍
- `bp_combo_report.py` 先猜对面最可能 ban 的哪几组，然后每组都推一遍。备赛时想看几套预案，用这个

网页上的「报告生成」用的是后面那个。

地图名按 `data/bp_core.json` 里的写法写。不加 `--map` 就是按所有人的平均算。

## 目录结构

```
启动.bat / start.sh      # 一键启动
scripts/                  # 后端
  serve.py                # 起本地服务，页面上所有接口都在这
  bp_search_engine.py     # 算分的核心
  bp_combo_report.py      # 一次算多组对手 ban 的报告
  bp_coach_report.py      # 算一组对手 ban 的完整推演
  ban_intel.py            # 对手 ban 人的习惯
  lineup_expect.py        # 阵容期望分
  map_pick.py             # 地图偏好统计
  custom_data.py          # 读预置数据
  file_api.py             # 导入 / 导出文件
web/                      # 六张页面 + 角色头像组件 + avatars/
data/
  bp_core.json            # ★ 这一季的比赛记录，其它表都从它算出来
  hunter/                 # 监管选手的配置（每人一份）
  survivor/               # 各队的角色池（每队一份）
  reports/                # 生成的报告存在这里（不会上传）
```

`data/` 根目录下那几个 JSON 是只读的，程序不会去改；`hunter/`、`survivor/`、`reports/` 可以写，页面上的导入导出就是读写这三个目录。
