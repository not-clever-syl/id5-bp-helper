## 项目简介

本项目是一个简易的 IVL 求生方 BP 辅助工具：把这一季的比赛数据整理成几张表，也能生成和浏览 BP 报告。下面按页面列一下。

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
| 数据 | 每个求生角色碰上每个监管平均能跑几个人，也能单独看某个监管选手 |
| 阵容期望分 | 挑 4 个求生，看这套阵容碰上对手的监管选手大概能拿几分 |
| 报告生成 | 选我方队伍、对手监管选手、第几局，生成一份 BP 报告 |
| BP 报告 | 看生成的报告：对面 ban 谁 → 他可能拿什么 → 我们怎么应对 |
| 地图偏好 | 看一支队伍第几局都选过哪些图 |
| 对位情报 | 哪些求生和监管碰在一起时双方都躲着走 |
| 原始数据 | 这一季比赛记录的编辑页，可新建、修改比赛；保存后自动重算其它页面用的数据，数据不合逻辑时只保存不重算并提示 |
| 队伍 / 选手 | 维护参赛队伍和监管选手名单：登记没打过比赛的队和人，改名会把历史数据一起改，数据里还在用的不能删 |

不想开网页的话，命令行也能直接出报告：

```bash
python3 scripts/bp_coach_report.py ACT YuM 3 --ban 幻灯师 弓箭手 --map 月亮河公园
python3 scripts/bp_combo_report.py ACT YuM 3 --map 月亮河公园
```

地图名按 `data/bp_core.json` 里的写法写；不传 `--map` 就按所有人的平均算。

数据改错了可以在页面上撤销；每次保存前旧文件会备份到 `bp_core.json.bak` 和 `tmp/bp_core_snapshots/`。

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
