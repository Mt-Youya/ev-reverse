(function () {
  // The part title is an editable field in its row. Report the rows' inputs so a rename can be
  // aimed at the right one -- and report whether the value is a plain input or a contenteditable,
  // because the two need different ways of being written to.
  var titles = document.querySelectorAll('[class*="file-item-content-text"]');
  var out = [];
  for (var i = 0; i < titles.length && i < 5; i++) {
    var text = (titles[i].textContent || '').replace(/\s+/g, ' ').trim();
    var row = titles[i];
    for (var up = 0; up < 6 && row.parentElement; up++) {
      row = row.parentElement;
      if (row.querySelectorAll('*').length > 4) break;
    }
    var fields = [];
    var inputs = row.querySelectorAll('input,textarea,[contenteditable="true"]');
    for (var k = 0; k < inputs.length; k++) {
      var el = inputs[k];
      var r = el.getBoundingClientRect();
      fields.push({ tag: el.tagName, type: el.type || null,
                    cls: String(el.className || '').slice(0, 40),
                    value: (el.value !== undefined ? el.value : el.textContent || '').slice(0, 40),
                    box: [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)],
                    visible: r.width > 0 && r.height > 0 });
    }
    out.push({ row: i, title: text.slice(0, 32), fields: fields });
  }
  return JSON.stringify(out);
})()
