# IVL 2026 秋季赛 · 求生方 BP 参考辅助工具

《第五人格》IVL 2026 秋季赛的求生方 BP 辅助工具，基于 260 个半局的官方战报数据做统计与推演，供备赛时参考。所有分数和推荐都只是历史数据的归纳，不构成决策依据，最终判断请以教练和选手的实际经验为准。

纯 Python 标准库实现（3.9+），无第三方依赖，前端为原生 HTML/JS/CSS，无需构建。

## 快速开始

### Windows

1. **装 Python**（只需一次）：去 <https://www.python.org/downloads/> 下载 Python 3.9 或更高版本。安装时**务必勾选 `Add Python to PATH`**（安装界面最下方），否则系统认不出 Python 命令。
2. **下载代码**：在仓库页面点 <kbd>Code</kbd> → <kbd>Download ZIP</kbd>，解压。
3. **双击 `启动.bat`**：黑窗口出现「正在启动服务」后，**浏览器会自动打开工具首页**。关闭那个黑窗口就等于停止服务。

> 第一次运行时 Windows 可能弹出「是否允许运行 Python」，选「允许」即可。

### macOS / Linux

1. **装 Python**：
   - macOS：`brew install python3`（或从 python.org 下载）
   - Debian / Ubuntu：`sudo apt install python3`
   - Fedora / RHEL：`sudo dnf install python3`
2. **下载代码**：
   ```bash
   git clone https://github.com/not-clever-syl/id5-bp-helper.git
   cd id5-bp-helper
   ```
   或在仓库页面下载 ZIP 后解压。
3. **启动**：
   ```bash
   chmod +x 启动.sh    # 只需第一次
   ./启动.sh
   ```
   浏览器会自动打开首页。按 `Ctrl+C` 停止服务。

### 通用命令（任何平台）

```bash
python scripts/serve.py 8766
```

浏览器访问 <http://localhost:8766/web/index.html>

可选参数：

```bash
python scripts/serve.py 8080                # 换端口
python scripts/serve.py 8766 --no-browser   # 不自动打开浏览器
```

- 端口被占用时会自动往后顺延（8766 → 8767 → …），不会报错
- 仓库已包含全部预编辑数据（分数表、BP 决策树、监管选手与角色池配置），克隆后无需额外下载或构建即可运行
- 后续更新代码：`git pull`

## 使用方法

### 1. 数据查询

首页进入「数据」页：

- **总榜**：查看每个求生角色面对每个监管的局均逃脱分（2.00 居中，越低越危险）
- **选手榜**：查看单个监管选手的实测 / 全局 / 折合分
- **角色池**：查看各战队的可选求生角色

### 2. 阵容期望分

进入「阵容期望分」页：

1. 从角色池点选 4 名求生角色组成阵容
2. 选择对手的监管选手
3. 点击搜索，得到这套阵容面对该选手每个监管角色的期望得分（按最危险排序），以及阵容结构分析

### 3. 生成 BP 报告

进入「报告生成」页：

1. 选择我方战队、对手监管选手、局次
2. 如需调整，导入监管配置（`data/hunter/`）或角色池（`data/survivor/`）覆盖预编辑数据
3. 一键生成报告，自动按局数给出 ban 名额与全局禁选提示

### 4. 查看 BP 报告

进入「BP 报告」页，按五段顺序阅读：

① 对手前两个 ban 位预测 → ② 对手可能的监管 → ③ 我方应 ban → ④ 我方前两手 → ⑤ 后续博弈树

每段附对位情报卡。

## 数据口径

- 分数单位是「局均逃脱」（0~4 人），**2.00 为居中值**
- 数据只使用 2026 秋季赛战报，权威源为 `data/bp_core.json`
- 阵容期望分必须选择监管选手，整表按该选手的折合分计算

---

## 开发者信息

### 命令行启动

```bash
git clone https://github.com/not-clever-syl/id5-bp-helper.git
cd id5-bp-helper
python scripts/serve.py 8766
```

浏览器访问 <http://localhost:8766/web/index.html>

常用参数：

```bash
python scripts/serve.py 8080          # 换端口
python scripts/serve.py 8766 --no-browser  # 不自动打开浏览器
```

后续更新代码：

```bash
git pull
```

### 目录结构

```
├── 启动.bat              # Windows 双击启动
├── 启动.sh               # Linux / macOS 启动（chmod +x 后执行）
├── scripts/              # 后端（Python 标准库，无第三方依赖）
│   ├── serve.py          # 本地 HTTP 服务 + 全部 API 入口
│   ├── bp_search_engine.py # 核心 BP 搜索引擎（α-β 剪枝 + 阵容聚合评分）
│   ├── bp_combo_report.py  # 教练报告主逻辑（以对手 ban 组合为轴）
│   ├── bp_coach_report.py  # 单个 ban 组合的完整推演
│   ├── ban_intel.py      # ban 位情报（监管 ban 指纹、PMI、互斥对）
│   ├── lineup_expect.py  # 阵容期望分计算（吃选手折合分）
│   ├── custom_data.py    # 预编辑基准数据加载与缓存
│   └── file_api.py       # 页面导入/导出的文件读写 API
├── web/                  # 前端（原生 HTML/JS/CSS，无构建步骤）
│   ├── index.html        # 导航首页
│   ├── data.html         # 数据页
│   ├── expect.html       # 阵容期望分页
│   ├── report.html       # BP 报告页
│   ├── generate.html     # 报告生成页
│   ├── char.js / char.css # 角色头像+名字的统一组件
│   ├── filemodal.js      # 导入/导出文件选择弹窗
│   └── avatars/          # 角色头像
└── data/                 # 数据
    ├── bp_core.json      # ★ 权威数据源：260 半局战报明细
    ├── bp_query.json     # 预编辑分数表
    ├── bp_tree.json      # BP 决策树 + 选手威胁分布 + ban 统计
    ├── web_bp.json       # 前端数据汇总
    ├── hunter/           # 监管选手配置（每人一份）
    ├── survivor/         # 角色池配置（每队一份）
    └── reports/          # 生成的 BP 报告（本地产物，不入库）
```

### 数据说明

- `data/` 根目录的 7 个 JSON 是**只读预编辑数据**，程序不写它们
- 三个子目录 `hunter/`、`survivor/`、`reports/` 是**可写目录**，页面导入导出直接读写
- 监管选手配置文件的 `cells` 字段是 (求生 × 监管角色) 的折合分格子，阵容期望分整表吃这份折合分

### 注意事项

- 纯本地工具，服务只监听 `127.0.0.1`，没有鉴权，不要暴露到公网
