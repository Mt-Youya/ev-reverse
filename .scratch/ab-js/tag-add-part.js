(function () {
  // The edit page shows two things carrying this text -- a DIV.btns wrapper and the real BUTTON.
  // Tagging the wrapper aims the click at a rectangle that is not the control.
  var all = document.querySelectorAll('button');
  for (var i = 0; i < all.length; i++) {
    var el = all[i];
    var text = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (text.indexOf('\u6dfb\u52a0\u5206P') === -1 || text.length > 12) continue;
    el.id = 'ab-add-part';
    var r = el.getBoundingClientRect();
    return JSON.stringify({ tagged: 'ab-add-part', cls: String(el.className),
                            box: [Math.round(r.left), Math.round(r.top),
                                  Math.round(r.width), Math.round(r.height)] });
  }
  return 'no add-part button';
})()
