(function () {
  // The edit page's save control carries the same 「立即投稿」 text as the upload form's. Only the
  // innermost element holding the text is tagged -- an outer wrapper's centre can sit on nothing.
  var all = document.querySelectorAll('span,div,button');
  var best = null;
  for (var i = 0; i < all.length; i++) {
    var el = all[i];
    var text = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (text !== '\u7acb\u5373\u6295\u7a3f') continue;
    if (el.children.length > 0) continue;
    var r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    best = { el: el, r: r };
    break;
  }
  if (!best) return 'submit button not found';
  best.el.id = 'ab-submit';
  return JSON.stringify({ tagged: 'ab-submit', tag: best.el.tagName,
                          cls: String(best.el.className),
                          box: [Math.round(best.r.left), Math.round(best.r.top),
                                Math.round(best.r.width), Math.round(best.r.height)] });
})()
