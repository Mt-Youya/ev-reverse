(function () {
  // For each part row, list the controls that sit inside it. A delete control is not a button
  // with the text 删除 in this list -- it is an icon -- so report every clickable-looking child
  // with its class and position and let the caller pick.
  var titles = document.querySelectorAll('[class*="file-item-content-text"]');
  var out = [];
  for (var i = 0; i < titles.length; i++) {
    var text = (titles[i].textContent || '').replace(/\s+/g, ' ').trim();
    if (text.indexOf('35. [测试体系]单元测试') === -1) continue;

    // Walk up until the ancestor holds more than the title, i.e. the whole row.
    var row = titles[i];
    for (var up = 0; up < 6 && row.parentElement; up++) {
      row = row.parentElement;
      if (row.querySelectorAll('*').length > 4) break;
    }
    var controls = [];
    var kids = row.querySelectorAll('button,span,div,i,svg,[class*="icon"],[class*="delete"],[class*="close"],[class*="remove"]');
    for (var k = 0; k < kids.length && controls.length < 12; k++) {
      var el = kids[k];
      var cls = String(el.className || '');
      if (!/delete|close|remove|icon|btn|button|operate|action/i.test(cls)) continue;
      var r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      var t = (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 12);
      controls.push({ tag: el.tagName, cls: cls.slice(0, 46), text: t,
                      box: [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)] });
    }
    out.push({ row: i, title: text.slice(0, 40), controls: controls });
  }
  return JSON.stringify(out);
})()
