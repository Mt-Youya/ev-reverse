// Delete the duplicate parts of one lesson on Bilibili's edit page.
//
//   node delete-duplicate-parts.mjs <port> <bvid> "<title prefix>" [--apply]
//
// The edit page is the only place a part can be removed: `member.bilibili.com/x/vu/web/delete`
// answers 404, and the edit API wants the whole archive payload including each part's original
// storage filename, which is not worth reconstructing by hand. The page itself is drivable --
// issue 02 said the controls ignore synthetic clicks, but that was measured before agent-browser
// and CDP were wired up; 投稿 and 添加分P both work through this same path.
//
// Per row the sequence is: hover the row to reveal its controls, click the trash icon
// (`div.icon-btn`), then confirm. Deleting shifts the rows, so the duplicate is re-found each
// round and always the LAST one is removed -- the first is the one to keep.

import { setTimeout as sleep } from 'node:timers/promises';

const [port, bvid, prefix] = process.argv.slice(2);
const apply = process.argv.includes('--apply');
if (!port || !bvid || !prefix) {
  console.error('usage: node delete-duplicate-parts.mjs <port> <bvid> "<title prefix>" [--apply]');
  process.exit(2);
}

let nextId = 0;
function rpc(socket, method, params) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    const onMessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.id !== id) return;
      socket.removeEventListener('message', onMessage);
      if (message.error) reject(new Error(`${method}: ${JSON.stringify(message.error)}`));
      else resolve(message.result);
    };
    socket.addEventListener('message', onMessage);
    socket.send(JSON.stringify({ id, method, params }));
  });
}

const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const page = targets.find((t) => t.type === 'page' && String(t.url).includes(bvid));
if (!page) {
  console.error(`no tab showing ${bvid}`);
  process.exit(2);
}

const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve);
  socket.addEventListener('error', () => reject(new Error('websocket failed')));
});

async function evaluate(expression) {
  const result = await rpc(socket, 'Runtime.evaluate',
                           { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result.value;
}

async function click(x, y) {
  const base = { x: Math.round(x), y: Math.round(y), button: 'left', clickCount: 1 };
  await rpc(socket, 'Input.dispatchMouseEvent', { type: 'mouseMoved', ...base });
  await rpc(socket, 'Input.dispatchMouseEvent', { type: 'mousePressed', ...base });
  await rpc(socket, 'Input.dispatchMouseEvent', { type: 'mouseReleased', ...base });
}

/// The rows carrying the title, oldest first, with the trash icon's viewport position. The list is
/// inside its own scroller, so `scrollIntoView` is what brings a row to a clickable coordinate.
function rowsExpression() {
  return `(function(){
    var want = ${JSON.stringify(prefix)};
    var titles = document.querySelectorAll('[class*="file-item-content-text"]');
    var out = [];
    for (var i = 0; i < titles.length; i++) {
      var text = (titles[i].textContent || '').replace(/\\s+/g, ' ').trim();
      if (text.indexOf(want) === -1) continue;
      var row = titles[i];
      for (var up = 0; up < 6 && row.parentElement; up++) {
        row = row.parentElement;
        if (row.querySelectorAll('*').length > 4) break;
      }
      var icon = row.querySelector('div.icon-btn');
      if (!icon) { out.push({ index: i, title: text, icon: null }); continue; }
      icon.scrollIntoView({ block: 'center' });
      var r = icon.getBoundingClientRect();
      out.push({ index: i, title: text,
                 icon: { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
                         w: Math.round(r.width), h: Math.round(r.height) } });
    }
    return out;
  })()`;
}

const CONFIRM = `(function(){
  var all = document.querySelectorAll('span,div,button');
  for (var i = 0; i < all.length; i++) {
    var el = all[i];
    var text = (el.textContent || '').replace(/\\s+/g, ' ').trim();
    if (text !== '\u786e\u8ba4') continue;
    if (el.children.length > 0) continue;
    var r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
  }
  return null;
})()`;

let rows = await evaluate(rowsExpression());
console.log(`${rows.length} row(s) titled ${JSON.stringify(prefix)}`);
if (rows.length < 2) {
  console.log('nothing to delete');
  socket.close();
  process.exit(0);
}
if (!apply) {
  console.log('dry run: would delete the last ' + (rows.length - 1) + ' of them (pass --apply)');
  socket.close();
  process.exit(0);
}

while (rows.length > 1) {
  const target = rows[rows.length - 1];
  if (!target.icon) {
    console.error('no trash icon on the row');
    process.exit(1);
  }
  // Hover first: the controls are only wired up once the row has been entered.
  await rpc(socket, 'Input.dispatchMouseEvent',
            { type: 'mouseMoved', x: target.icon.x, y: target.icon.y, button: 'none', clickCount: 0 });
  await sleep(700);
  await click(target.icon.x, target.icon.y);
  await sleep(1200);

  const confirm = await evaluate(CONFIRM);
  if (!confirm) {
    console.error('confirm button did not appear');
    process.exit(1);
  }
  await click(confirm.x, confirm.y);
  await sleep(1800);

  const before = rows.length;
  rows = await evaluate(rowsExpression());
  console.log(`deleted one: ${before} -> ${rows.length}`);
  if (rows.length === before) {
    console.error('the row count did not drop; stopping');
    process.exit(1);
  }
}

console.log(`now ${rows.length} row(s) left -- submit the edit to save it`);
socket.close();
