## 项目简介

本项目是一个简易的 IVL 求生方 BP 辅助工具：把这一季 260 个半局的比赛数据整理成几张表，也能生成和浏览 BP 报告。下面按页面列一下。

工具在本地运行，不联网，也不会上传任何数据。

> **许可证提醒**：本项目采用 PolyForm Noncommercial 1.0.0 许可证，允许个人学习、研究和非商业实验使用。**禁止任何形式的商业使用**，包括但不限于打包出售、付费分发、SaaS 收费、广告变现，或将代码作为付费课程、教程、资源包的一部分销售。

> **温馨提示与免责声明**：结果均基于历史比赛数据计算，数据可能略有错误，可能有比赛未经记录，仅供备赛参考；实际 BP 请以教练和选手判断为准。

## 快速开始

### Windows

1. 装 Python 3.8 或更新的版本：<https://www.python.org/downloads/>，安装时记得勾上 **Add Python to PATH**
2. 从仓库页面下载 ZIP，解压到任意位置
3. 双击 `启动.bat`。浏览器会自动打开，关掉那个黑窗口就停了

### macOS / Linux

```bash
git clone https://github.com/not-clever-syl/id5-bp-helper.git
cd id5-bp-helper
./start.sh
```

如果提示 `Permission denied`，先 `chmod +x start.sh`，或者直接 `sh start.sh`。`Ctrl+C` 停止。

### 想自己起服务

```bash
# Windows
python scripts/serve.py 8766

# macOS / Linux
python3 scripts/serve.py 8766
```

换端口就改数字；加 `--no-browser` 不自动开浏览器。然后访问 <http://localhost:8766/web/index.html>。

数据都在仓库里，克隆下来就能用。更新用 `git pull`。

## 实现的辅助功能

| 页面 | 干什么 |
|---|---|
| 数据 | 每个求生角色碰上每个监管，平均能跑掉几个人；也能单独看某个监管选手打得怎么样。任意一格点开，能看到组成这一格的是哪几局 |
| 阵容期望分 | 挑 4 个求生、再选对手的监管选手，看这套阵容碰上他的每个监管大概能拿几分，最危险的排前面 |
| 报告生成 | 选我方队伍、对手的监管选手、第几局，生成一份报告 |
| BP 报告 | 打开生成的报告，按顺序看：对面先 ban 了谁 → 他可能想拿哪个监管 → 我们该 ban 谁 → 我们先选谁 → 后面怎么走 |
| 地图偏好 | 选一支队伍，看他第几局都选过哪些图 |
| 对位情报 | 哪些求生和监管碰在一起时双方都躲着走，以及是对面主动 ban 掉、还是我们自己一直不选 |
| 原始数据 | 这一季所有半局的原始表，一行一场比赛（主客队、大分、小分），点比赛行展开看每个半局，点半局弹窗改。保存后自动重算上面所有页面用的分数表、决策树、选手榜和可导入配置 |

报告里「我们先选谁」只给结论，不再列备选；「后面怎么走」是从上往下的树，白线是主线，同层备选收在「再看一个」里，点一次多放一个。树能拖动、滚轮缩放，右上角有放大缩小和「适配」，点「恢复主线」回到刚打开的样子。

最后一层「监管选人」显示对手真掏某个监管时这套阵容能打几分，按最伤排序；还能用「再看一个」看其他监管，但不再往下展开。

对位情报统计这一季实际怎么 ban、怎么选，不受下拉和地图影响。阵容期望分和报告生成可以选地图，选了会偏向这张图的常见角色；数据页选地图则是整张表只算这张图。某张图只打过两三局就别选，留空按所有人平均更稳。

不想开网页的话，命令行也能直接出报告：

```bash
python3 scripts/bp_coach_report.py ACT YuM 3 --ban 幻灯师 弓箭手 --map 月亮河公园
python3 scripts/bp_combo_report.py ACT YuM 3 --map 月亮河公园
```

`bp_coach_report.py` 算一组固定 ban 的完整走向；`bp_combo_report.py` 先猜几组最可能的 ban 再各推一遍（网页上的「报告生成」用的就是它）。

地图名按 `data/bp_core.json` 里的写法写；不传 `--map` 就按所有人的平均算。

原始数据页改完保存，`data/bp_core.json` 会被覆盖（旧文件留一份 `bp_core.json.bak`），随后所有派生数据自动重算，不用手工跑任何命令。也可以在命令行里重算：

```bash
python3 scripts/rebuild.py
```

原始数据页有两种加法：比赛行的「+」加一整场比赛，展开后的「+ 加一局」在这一场里加一对配对半局。保存前会自动查一遍逻辑（比分和逃脱人数对不对、上下半局队伍有没有互换、ban 名额对不对），不合逻辑的行会标黄并禁止保存，命令行里也能单独查：

```bash
python3 scripts/core_check.py
```

## 目录结构

```
启动.bat / start.sh      # 一键启动
scripts/                  # 后端
  serve.py                # 起本地服务，页面上所有接口都在这
  rebuild.py              # 从 bp_core.json 重算全部派生数据
  bp_search_engine.py     # 算分的核心
  bp_combo_report.py      # 一次算多组对手 ban 的报告
  bp_coach_report.py      # 算一组对手 ban 的完整推演
  ban_intel.py            # 对手 ban 人的习惯
  lineup_expect.py        # 阵容期望分
  map_pick.py             # 地图偏好统计
  map_scores.py           # 单张地图的实测表，以及某一格的半局明细
  core_check.py           # 查 bp_core.json 的逻辑对不对（保存前也会自动查一遍）
  custom_data.py          # 读预置数据
  file_api.py             # 导入 / 导出文件
  build/                  # 派生数据的生成脚本
web/                      # 八张页面 + 角色头像组件 + avatars/
data/
  bp_core.json            # ★ 这一季的比赛记录，其它表都从它算出来
  hunter/                 # 监管选手的配置（每人一份）
  survivor/               # 各队的角色池（每队一份）
  reports/                # 生成的报告存在这里
```

`data/bp_core.json` 是唯一的数据源，可在页面上改（改前会自动备份）。
`data/` 根目录下其它 JSON 都是从它算出来的，别手改；要改就改 `bp_core.json` 再重算。
`hunter/`、`survivor/`、`reports/` 也可以写，页面上的导入导出就是读写这三个目录。
