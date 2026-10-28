(function () {
  // Dump the shape of the first part row: what its children are, what is clickable, and whether
  // anything looks like an editable title. The row shows its title as text, so the field to write
  // to only exists in some other state -- this says which state that is.
  var titles = document.querySelectorAll('[class*="file-item-content-text"]');
  if (!titles.length) return 'no rows';
  var row = titles[0];
  for (var up = 0; up < 6 && row.parentElement; up++) {
    row = row.parentElement;
    if (row.querySelectorAll('*').length > 4) break;
  }

  function describe(el, depth) {
    if (depth > 3) return null;
    var r = el.getBoundingClientRect();
    var kids = [];
    for (var i = 0; i < el.children.length && kids.length < 14; i++) {
      var child = describe(el.children[i], depth + 1);
      if (child) kids.push(child);
    }
    return {
      tag: el.tagName,
      cls: String(el.className || '').slice(0, 44),
      text: (el.children.length === 0 ? (el.textContent || '').trim().slice(0, 26) : ''),
      input: el.tagName === 'INPUT' ? { type: el.type, value: String(el.value || '').slice(0, 30),
                                        readonly: el.readOnly, disabled: el.disabled } : null,
      clickable: window.getComputedStyle(el).cursor,
      box: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
      kids: kids
    };
  }
  return JSON.stringify(describe(row, 0));
})()
