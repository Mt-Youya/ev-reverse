(function () {
  // What the edit page offers for adding a part. A part row carries a file input of its own, so
  // "can I add parts here" is answered by finding the control that appends a row, not by looking
  // for the upload form's 「添加分P」 button.
  var out = { url: location.href, buttons: [], fileInputs: 0, rows: 0 };

  var inputs = document.querySelectorAll('input[type=file]');
  for (var i = 0; i < inputs.length; i++) {
    var accept = inputs[i].accept || '';
    if (accept.indexOf('.mp4') !== -1) out.fileInputs++;
  }

  var rows = document.querySelectorAll('[class*="file-item"],[class*="part-item"],[class*="video-item"]');
  out.rows = rows.length;

  var all = document.querySelectorAll('button,span,div,a');
  for (var k = 0; k < all.length && out.buttons.length < 25; k++) {
    var el = all[k];
    var text = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!text || text.length > 12) continue;
    if (['添加分P', '添加视频', '继续添加', '上传视频', '替换', '删除', '添加'].indexOf(text) === -1) continue;
    if (el.children.length > 1) continue;
    var r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    var dup = false;
    for (var j = 0; j < out.buttons.length; j++) {
      if (out.buttons[j].text === text && Math.abs(out.buttons[j].x - r.left) < 3) dup = true;
    }
    if (dup) continue;
    out.buttons.push({ text: text, tag: el.tagName,
                       cls: String(el.className).slice(0, 40),
                       x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) });
  }
  return JSON.stringify(out);
})()
