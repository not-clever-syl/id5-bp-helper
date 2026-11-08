## 项目简介

本项目是一个简易的 IVL 求生方 BP 辅助工具。它把这一季 260 个半局的比赛数据整理成几张表，帮助你快速查看：

- 每个求生角色对监管角色在指定地图或监管选手的筛选条件下的预期局均逃脱（带 AI 设计的熟练度修正项）
- 某套阵容在某张地图某个监管选手手下大概期望逃脱是多少。
- 某个队伍在第1/2/3/4（加赛）局的选图分布。

另外支持：
- 选择第几局、地图、全局 ban 位与选手角色池，自动生成一份可供参考的 BP 报告。
- 浏览解析生成的 BP 报告。

点两下就能看到结果。工具在本地运行，不联网，也不会上传任何数据。

> **许可证提醒**：本项目采用 PolyForm Noncommercial 1.0.0 许可证，允许个人学习、研究和非商业实验使用。**禁止任何形式的商业使用**，包括但不限于打包出售、付费分发、SaaS 收费、广告变现，或将代码作为付费课程、教程、资源包的一部分销售。

> **温馨提示与免责声明**：结果均基于历史比赛数据计算，数据可能略有错误，可能有比赛未经记录，仅供备赛参考；实际 BP 请以教练和选手判断为准。

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
| 数据 | 每个求生角色碰上每个监管，平均能跑掉几个人；也能单独看某个监管选手打得怎么样。任意一格点开，能看到组成这一格的是哪几局 |
| 阵容期望分 | 挑 4 个求生、再选对手的监管选手，看这套阵容碰上他的每个监管大概能拿几分，最危险的排前面 |
| 报告生成 | 选我方队伍、对手的监管选手、第几局，生成一份报告 |
| BP 报告 | 打开生成的报告，按顺序看：对面先 ban 了谁 → 他可能想拿哪个监管 → 我们该 ban 谁 → 我们先选谁 → 后面怎么走 |
| 地图偏好 | 选一支队伍，看他第几局都选过哪些图 |

阵容期望分和报告生成都能选这局打哪张图，也可以不选。选了之后，推荐会往这张图上常见的角色那边偏一些，页面也会顺带列出这张图的常用监管。

数据页选地图是另一回事：整张表会换成只算这张图，行和列都只留这张图出场过的角色，每格是这张图的平均逃生人数，下面小字标着算了几局。想看具体是哪几局，点开那一格就行。

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
  map_scores.py           # 单张地图的实测表，以及某一格的半局明细
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
