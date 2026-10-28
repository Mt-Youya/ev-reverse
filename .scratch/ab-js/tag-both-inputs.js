(function () {
  var inputs = document.querySelectorAll('input[type=file]');
  var main = null, batch = null;
  for (var i = 0; i < inputs.length; i++) {
    var accept = inputs[i].accept || '';
    if (accept.indexOf('.mp4') === -1) continue;
    var style = window.getComputedStyle(inputs[i]);
    if (style.display === 'none') { if (!main) main = inputs[i]; }
    else if (!batch) batch = inputs[i];
  }
  var out = { total: inputs.length, main: !!main, batch: !!batch, url: location.href };
  if (main) main.id = 'ab-main-input';
  if (batch) batch.id = 'ab-batch-input';
  return JSON.stringify(out);
})()
