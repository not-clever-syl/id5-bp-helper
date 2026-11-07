/* 导入 / 导出用的模态子窗口。
 *
 * 页面不再调用浏览器原生文件选择框（<input type="file">），
 * 而是弹出这个子窗口：候选文件由服务器列出，点一下就完成。
 *
 * 导入：列出服务器上的 json（预制数据 + 三个导出目录），点一个即读入。
 * 导出：一律落到对应的导出目录，文件名由系统按规范生成，
 *       用户不能自己起名；子窗口里可点已有版本覆盖，或新建版本。
 *
 * 目录约定（用户定）：
 *     监管数据    data/hunter/
 *     求生角色池  data/survivor/
 *     报告        data/reports/
 *
 * 用法：
 *   FileModal.pick({ title, onPick(data, file) })                // 导入
 *   FileModal.saveVersion({ title, prefix, dir, data, onDone(res) })  // 导出
 */
(function (global) {
  'use strict';

  const CSS = `
.fmodal-mask{position:fixed;inset:0;background:rgba(6,8,12,.72);
  display:none;align-items:center;justify-content:center;z-index:200;
  padding:24px;backdrop-filter:blur(2px)}
.fmodal-mask.on{display:flex}
.fmodal{background:var(--panel);border:1px solid var(--border);border-radius:12px;
  width:min(680px,100%);max-height:min(78vh,720px);display:flex;flex-direction:column;
  box-shadow:0 18px 60px rgba(0,0,0,.5)}
.fmodal.wide{width:min(1060px,100%);max-height:min(84vh,780px)}
.fmodal h3{margin:0;padding:13px 18px;font-size:14.5px;font-weight:600;
  border-bottom:1px solid var(--border);display:flex;align-items:center;gap:9px}
.fmodal h3 .x{margin-left:auto;cursor:pointer;color:var(--dim);font-size:16px;
  background:none;border:none;padding:2px 6px;line-height:1}
.fmodal h3 .x:hover{color:var(--text)}
.fmodal .fbody{padding:14px 18px;overflow:auto;flex:1;min-height:0}
.fmodal .fbody p.lead{margin:0 0 12px;color:var(--dim);font-size:12px;line-height:1.8}
.fmodal .fbody code{background:var(--panel2);padding:1px 5px;border-radius:4px;
  font:11px Consolas,monospace}
.fmodal .frow{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.fmodal .frow label{color:var(--dim);font-size:12px;flex:none}
.fmodal input[type=text]{flex:1;min-width:180px;background:var(--panel2);color:var(--text);
  border:1px solid var(--border);border-radius:7px;padding:7px 10px;font-size:12.5px}
.fmodal input[type=text]:focus{border-color:var(--accent);outline:none}
.fmodal input[type=text][readonly]{opacity:.75;cursor:default}
.fmodal .flist{display:flex;flex-direction:column;gap:4px;
  border:1px solid var(--border);border-radius:9px;padding:8px;
  background:var(--panel2);max-height:340px;overflow:auto}
.fmodal .fitem{display:flex;align-items:center;gap:10px;padding:7px 10px;
  border-radius:7px;cursor:pointer;border:1px solid transparent;font-size:12.5px}
.fmodal .fitem:hover{background:var(--panel);border-color:var(--border)}
.fmodal .fitem.on{background:#2a3f6e;border-color:var(--accent)}
.fmodal .fitem .fn{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fmodal .fitem .fd{color:var(--dim);font-size:11px;flex:none}
.fmodal .fitem .fs{color:var(--dim);font-size:11px;flex:none;
  font-family:Consolas,monospace}
.fmodal .fempty{color:var(--dim);text-align:center;padding:22px 0;font-size:12px}
/* 报告导入用表格：一行一份，给人读 */
.fmodal .ftable{width:100%;border-collapse:collapse;font-size:12px;table-layout:auto}
.fmodal .ftable th{position:sticky;top:0;z-index:1;text-align:left;font-weight:500;
  color:var(--dim);font-size:11px;padding:6px 8px;background:var(--panel2);
  border-bottom:1px solid var(--border);white-space:nowrap}
.fmodal .ftable td{padding:7px 8px;border-bottom:1px solid rgba(255,255,255,.05);
  white-space:nowrap;overflow:visible;text-overflow:clip}
.fmodal .ftable tbody tr{cursor:pointer}
.fmodal .ftable tbody tr:hover td{background:var(--panel)}
.fmodal .ftable tbody tr:last-child td{border-bottom:none}
.fmodal .ftable .num{font-family:Consolas,monospace}
.fmodal .ftable .muted{color:var(--dim)}
.fmodal .ftable .ver{font-family:Consolas,monospace;font-size:11.5px;color:var(--text)}
.fmodal .fwrap{border:1px solid var(--border);border-radius:9px;overflow:auto;
  max-height:380px;background:var(--panel2)}
.fmodal .ffoot{padding:11px 18px;border-top:1px solid var(--border);
  display:flex;align-items:center;gap:9px}
.fmodal .ffoot .grow{flex:1}
.fmodal .fmsg{font-size:12px;color:var(--dim);margin-left:auto}
.fmodal .fmsg.err{color:#e0837b}
.fmodal .fmsg.ok{color:#6dc4a0}
.fmodal button{background:var(--panel2);color:var(--text);border:1px solid var(--border);
  border-radius:7px;padding:6px 13px;font-size:12.5px;cursor:pointer}
.fmodal button:hover:not(:disabled){border-color:var(--accent)}
.fmodal button:disabled{opacity:.4;cursor:default}
.fmodal button.primary{background:#2a3f6e;border-color:var(--accent);color:#cfe0ff}
.fmodal .warnbox{background:rgba(216,178,95,.10);border:1px solid rgba(216,178,95,.35);
  border-radius:8px;padding:9px 12px;font-size:12px;color:#e2c07a;margin-bottom:12px}
`;

  function injectCss() {
    if (document.getElementById('fmodal-css')) return;
    const st = document.createElement('style');
    st.id = 'fmodal-css';
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function esc(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function fmtSize(n) {
    if (!n && n !== 0) return '';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1048576).toFixed(1) + ' MB';
  }

  function fmtTime(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} `
         + `${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  /** 从文件名取数字版本："YuM_2.json" → 2；没有版本号按 1 算（同样视为预制）。 */
  function verOf(name) {
    const m = /_(\d+)\.json$/i.exec(String(name || ''));
    return m ? +m[1] : 1;
  }

  let mask = null, body = null, foot = null, msgEl = null, titleEl = null;
  let onCloseCb = null;

  function ensure() {
    if (mask) return;
    injectCss();
    mask = document.createElement('div');
    mask.className = 'fmodal-mask';
    mask.innerHTML = `
<div class="fmodal" role="dialog" aria-modal="true">
  <h3><span class="ft"></span><button class="x" type="button" title="关闭">✕</button></h3>
  <div class="fbody"></div>
  <div class="ffoot"><span class="fmsg"></span></div>
</div>`;
    document.body.appendChild(mask);
    body = mask.querySelector('.fbody');
    foot = mask.querySelector('.ffoot');
    msgEl = foot.querySelector('.fmsg');
    titleEl = mask.querySelector('.ft');
    mask.querySelector('.x').onclick = () => close();
    mask.addEventListener('mousedown', ev => {
      if (ev.target === mask) close();
    });
    document.addEventListener('keydown', ev => {
      if (ev.key === 'Escape' && mask.classList.contains('on')) close();
    });
  }

  function setMsg(t, cls) {
    msgEl.textContent = t || '';
    msgEl.className = 'fmsg' + (cls ? ' ' + cls : '');
  }

  function close() {
    if (!mask) return;
    mask.classList.remove('on');
    if (onCloseCb) { const f = onCloseCb; onCloseCb = null; f(); }
  }

  async function api(url, opts) {
    const r = await fetch(url, opts);
    const t = await r.text();
    let j = null;
    try { j = JSON.parse(t); } catch (_) {}
    if (!r.ok) throw new Error((j && j.error) || ('HTTP ' + r.status));
    return j;
  }

  function open(title, bodyHtml, footHtml, onClose) {
    ensure();
    titleEl.textContent = title;
    body.innerHTML = bodyHtml;
    foot.innerHTML = footHtml + '<span class="fmsg"></span>';
    msgEl = foot.querySelector('.fmsg');
    mask.classList.add('on');
    onCloseCb = onClose || null;
    setMsg('');
    setWide(false);
    const first = body.querySelector('input,button,select');
    if (first) setTimeout(() => first.focus(), 30);
  }

  /** 表格模式用宽一点的窗口：列多，窄了会挤成两行。 */
  function setWide(on) {
    mask.querySelector('.fmodal').classList.toggle('wide', !!on);
  }

  /* ── 导入：列出服务器上的文件供点选 ──
   *
   * dir：只列这一个目录（相对项目根）：
   *     data/hunter     监管数据配置
   *     data/survivor   求生角色池配置
   *     data/reports    报告配置
   * 不传就列三个目录。
   *
   * table：把列表渲染成一行一份的表格（报告用），列由 cols 指定，
   *        数据取自每份报告自己的 meta。
   */

  /** 报告表格的列。取值函数返回 {text, cls, title}。 */
  const REPORT_COLS = [
    { key: 'player', label: '监管选手', get: m => m.player },
    { key: 'team', label: '我方战队', get: m => m.team },
    { key: 'game', label: '局次', cls: 'num', get: m => m.game ? '第 ' + m.game + ' 局' : '' },
    { key: 'map', label: '地图', get: m => m.map, empty: '不指定' },
    {
      key: 'scores', label: '监管数据', cls: 'ver',
      get: m => m.scores_source, empty: '预编辑基准',
    },
    {
      key: 'roster', label: '求生数据', cls: 'ver',
      get: m => m.roster_source, empty: '预编辑基准',
    },
    { key: 'time', label: '生成时间', cls: 'num', get: m => m.generated_at, empty: '—' },
  ];

  function cellHtml(col, meta) {
    const v = meta ? col.get(meta) : null;
    const missing = v === null || v === undefined || v === '';
    const text = missing ? (col.empty || '—') : v;
    const cls = [col.cls || '', missing ? 'muted' : ''].filter(Boolean).join(' ');
    return `<td${cls ? ' class="' + cls + '"' : ''}>${esc(text)}</td>`;
  }

  function reportTable(files) {
    const head = REPORT_COLS.map(c => `<th>${esc(c.label)}</th>`).join('');
    const rows = files.map((f, i) => {
      const m = f.meta || null;
      return `<tr data-i="${i}" title="${esc(f.path)}">`
        + REPORT_COLS.map(c => cellHtml(c, m)).join('') + '</tr>';
    }).join('');
    return `<div class="fwrap"><table class="ftable">
      <thead><tr>${head}</tr></thead><tbody>${rows}</tbody>
    </table></div>`;
  }

  async function pick(opts) {
    opts = opts || {};
    const dirs = opts.dir
      ? String(opts.dir).replace(/\/+$/, '')
      : '';
    const table = !!opts.table;
    open(opts.title || '选择要导入的配置文件',
      '<p class="lead">' + (table
        ? '点击一行即可打开那份报告。按生成时间倒序排列。'
        : '从服务器上的配置文件里挑一个点开即可。预制数据只读不改，导入的是它的内容。')
      + '</p>'
      + `<div class="${table ? '' : 'flist'}" id="fmlist">`
      + '<div class="fempty">正在读取…</div></div>',
      '<button id="fmCancel">取消</button>', () => {});
    setWide(table);

    const list = document.getElementById('fmlist');
    document.getElementById('fmCancel').onclick = () => close();

    let files = [];
    try {
      const url = '/api/files/list?kind=import'
        + (dirs ? '&dir=' + encodeURIComponent(dirs) : '')
        + (table ? '&meta=1' : '');
      const j = await api(url);
      files = j.files || [];
    } catch (e) {
      list.innerHTML = `<div class="fempty">读取失败：${esc(e.message)}</div>`;
      return;
    }
    if (!files.length) {
      list.innerHTML = `<div class="fempty">${dirs ? esc(dirs) + '/' : ''}下没有可导入的配置文件</div>`;
      return;
    }
    if (table) {
      list.innerHTML = reportTable(files);
      list.querySelectorAll('tr[data-i]').forEach(el => {
        el.onclick = () => openFile(files[+el.dataset.i]);
      });
      return;
    }
    list.innerHTML = files.map((f, i) => `
      <div class="fitem" data-i="${i}">
        <span class="fn" title="${esc(f.path)}">${esc(f.name)}</span>
        <span class="fd">${esc(f.dir)}</span>
        <span class="fs">${fmtSize(f.size)}</span>
        <span class="fs">${fmtTime(f.mtime)}</span>
      </div>`).join('');
    list.querySelectorAll('.fitem').forEach(el => {
      el.onclick = () => openFile(files[+el.dataset.i]);
    });

    function openFile(f) {
      setMsg(`正在读取 ${f.name}…`);
      api('/api/files/read', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: f.path }),
      }).then(j => {
        close();
        if (opts.onPick) opts.onPick(j.data, f);
      }).catch(e => setMsg('读取失败：' + e.message, 'err'));
    }
  }

  /* ── 另存为版本 ──
   *
   * 命名规范（用户定）：
   *     监管数据    <监管选手id>_<数字>.json     → data/hunter/
   *     求生角色池  <队伍名>_<数字>.json         → data/survivor/
   *     报告        <监管id>_<版本>_vs_<队伍名>_<版本>_game<局数>_<时间戳>.json → data/reports/
   *
   * 用户不能自己起文件名。子窗口里列出现有的同前缀版本：
   * 点一个 = 覆盖那个版本；点「新建版本」= 生成下一个序号。
   *
   * dir：目标目录（相对项目根），默认 data/hunter/
   * listUrl：可选，直接指定列文件的接口（报告页用带 player/team/game 的）。
   */
  async function saveVersion(opts) {
    opts = opts || {};
    const prefix = opts.prefix || 'export';
    const dir = (opts.dir || 'data/hunter').replace(/\/+$/, '');
    let files = [], suggested = prefix + '_1.json';
    try {
      const url = opts.listUrl
        || ('/api/files/list?kind=export&prefix=' + encodeURIComponent(prefix)
            + '&dir=' + encodeURIComponent(dir));
      const j = await api(url);
      files = j.files || [];
      suggested = j.suggested || suggested;
    } catch (e) { /* 拿不到就空着 */ }

    const rule = opts.nameRule
      || (prefix + '_&lt;数字&gt;.json');
    open(opts.title || '另存为版本',
      '<p class="lead">版本文件名由系统按规范生成（<code>' + rule
      + '</code>），不能自己起名。<br>'
      + '点下面已有的版本可以覆盖它，也可以新建一个版本。'
      + '<b style="color:#e2c07a">版本 1（<code>*_1.json</code>）是预制数据，不可覆盖</b>，改动请新建版本。'
      + '所有导出结果都存在 <code>' + esc(dir) + '/</code>，预制数据不会被改动。</p>'      + '<div class="frow"><label style="flex:none">新建版本</label>'
      + `<input type="text" id="fmName" value="${esc(suggested)}" readonly>`
      + '<span style="color:var(--dim);font-size:11.5px">（序号自动递增）</span></div>'
      + '<div class="flist" id="fmlist">'
      + (files.length
        ? files.map((f, i) => `
          <div class="fitem" data-i="${i}">
            <span class="fn" title="${esc(f.path)}">${esc(f.name)}</span>
            <span class="fd">${esc(f.dir)}</span>
            <span class="fs">${fmtSize(f.size)}</span>
            <span class="fs">${fmtTime(f.mtime)}</span>
          </div>`).join('')
        : '<div class="fempty">还没有这个前缀的版本，将新建第一个</div>')
      + '</div>',
      '<button id="fmCancel">取消</button>'
      + '<span class="grow"></span>'
      + '<button class="primary" id="fmSave">新建版本</button>', () => {});

    const nameEl = document.getElementById('fmName');
    const saveBtn = document.getElementById('fmSave');
    document.getElementById('fmCancel').onclick = () => close();

    // 点已有版本 = 选中它，按钮变成「覆盖此版本」
    // 版本 1 是预制数据（*_1.json），不允许被覆盖 —— 不可点选，样式上置灰。
    let target = null;
    const list = document.getElementById('fmlist');
    list.querySelectorAll('.fitem').forEach(el => {
      const f = files[+el.dataset.i];
      if (verOf(f.name) === 1) {
        el.style.opacity = '.45';
        el.style.cursor = 'not-allowed';
        el.title = '版本 1 是预制数据，不允许覆盖；请新建版本';
        return;
      }
      el.onclick = () => {
        target = f;
        list.querySelectorAll('.fitem').forEach(x => x.classList.remove('on'));
        el.classList.add('on');
        nameEl.value = f.name;
        saveBtn.textContent = '覆盖此版本';
      };
    });

    saveBtn.onclick = () => {
      const path = dir + '/' + nameEl.value.trim();
      saveBtn.disabled = true;
      setMsg('正在保存…');
      api('/api/files/write', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path,
          data: opts.data,
          overwrite: !!target,     // 覆盖已有版本才允许 overwrite
        }),
      }).then(j => {
        close();
        if (opts.onDone) opts.onDone(j);
      }).catch(e => {
        saveBtn.disabled = false;
        setMsg(e.message, 'err');
      });
    };
  }

  global.FileModal = { pick, save: saveVersion, saveVersion, close };
})(window);
