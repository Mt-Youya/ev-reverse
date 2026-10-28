(function () {
  // The edit page keeps one row per part, and a part being uploaded shows its own progress text.
  // Counting rows and looking for 上传中/上传完成 is how progress is read here; the upload form's
  // 「等待视频上传完成后会自动提交」 banner never appears on this page.
  var body = document.body.innerText;
  var rows = document.querySelectorAll('[class*="file-item-content-text"]');
  var titles = [];
  for (var i = 0; i < rows.length && i < 80; i++) {
    titles.push((rows[i].textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60));
  }
  var uploading = 0;
  for (var k = 0; k < rows.length; k++) {
    if ((rows[k].textContent || '').indexOf('\u4e0a\u4f20\u4e2d') !== -1) uploading++;
  }
  return JSON.stringify({
    url: location.href,
    parts: rows.length,
    uploading: uploading,
    saving: body.indexOf('\u4fdd\u5b58') !== -1,
    submitQueued: body.indexOf('\u7b49\u5f85\u89c6\u9891\u4e0a\u4f20\u5b8c\u6210\u540e\u4f1a\u81ea\u52a8\u63d0\u4ea4') !== -1,
    first: titles.slice(0, 3),
    last: titles.slice(-4)
  });
})()
