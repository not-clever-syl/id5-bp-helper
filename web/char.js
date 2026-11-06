/* 角色显示的统一组件：头像 + 名字
 *
 *   【rules】所有页面里出现角色名的地方，一律用 Char.icon(name) 或
 *   Char.chip(name) 渲染，不要直接写裸文字，否则教练会分不清同名或近似名。
 *
 *   头像文件在 web/avatars/<角色名>.png，由 scripts/build_avatars.py 生成。
 *   加载失败时退化成名字首字，不会留下空格子。
 */
(function (global) {
  'use strict';

  var BASE = 'avatars/';

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (m) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[m];
    });
  }

  /** 头像 <img>，size 为边长（px） */
  function icon(name, size, cls) {
    size = size || 25;
    var url = BASE + encodeURIComponent(name) + '.png';
    return '<img class="char-avatar ' + (cls || '') + '" src="' + url +
      '" alt="' + esc(name) + '" width="' + size + '" height="' + size +
      '" loading="lazy" title="' + esc(name) + '"' +
      ' onerror="this.classList.add(\'broken\');this.removeAttribute(\'src\')">';
  }

  /** 头像 + 名字，inline 一行 */
  function chip(name, opt) {
    opt = opt || {};
    return '<span class="char-chip' + (opt.className ? ' ' + opt.className : '') +
      '" data-char="' + esc(name) + '">' +
      icon(name, opt.size || 25) +
      '<span class="char-name">' + esc(name) + '</span>' +
      (opt.extra || '') + '</span>';
  }

  /** 名字 → 头像 URL（需要单独用时） */
  function url(name) {
    return BASE + encodeURIComponent(name) + '.png';
  }

  /** 批量：把数组渲染成一串 chip */
  function chips(list, opt) {
    return (list || []).map(function (n) { return chip(n, opt); }).join('');
  }

  global.Char = { icon: icon, chip: chip, chips: chips, url: url, esc: esc };
})(window);
