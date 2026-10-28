// Rename one part on Bilibili's edit page.
//
//   node rename-part.mjs <port> <bvid> <occurrence> "<current title>" "<new title>" [--apply]
//
// `occurrence` is 1-based among the rows whose title matches exactly, which is what makes this
// safe on 前端工具链: `01. ESLint介绍` appears twice there -- once as the course overview wearing
// the wrong name (2342s), once as the real ESLint intro (1864s) -- and only the first may change.
//
// The title is not an input until it is clicked: the row renders `div.pure-text.pure-text-editable`
// (cursor: pointer) and swaps in a field on click, so the write has to happen after that swap.

import { setTimeout as sleep } from 'node:timers/promises';

const [port, bvid, occurrenceArg, current, replacement] = process.argv.slice(2);
const apply = process.argv.includes('--apply');
if (!port || !bvid || !occurrenceArg || !current || !replacement) {
  console.error('usage: node rename-part.mjs <port> <bvid> <occurrence> "<current>" "<new>" [--apply]');
  process.exit(2);
}
const occurrence = Number(occurrenceArg);

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

// The editable title of the Nth matching row, plus its text so the caller can see what it is
// about to overwrite.
const TARGET = `(function(){
  var want = ${JSON.stringify(current)};
  var seen = 0;
  var all = document.querySelectorAll('div.pure-text.pure-text-editable');
  for (var i = 0; i < all.length; i++) {
    var el = all[i];
    var text = (el.textContent || '').replace(/\\s+/g, ' ').trim();
    if (text !== want) continue;
    seen++;
    if (seen !== ${occurrence}) continue;
    el.setAttribute('data-ab-target', '1');
    var r = el.getBoundingClientRect();
    return { text: text, seen: seen,
             box: [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)] };
  }
  return { text: null, seen: seen };
})()`;

const target = await evaluate(TARGET);
if (!target || !target.text) {
  console.error(`found ${target ? target.seen : '?'} row(s) titled ${JSON.stringify(current)}; ` +
                `occurrence ${occurrence} is not among them`);
  process.exit(1);
}
console.log(`target: occurrence ${occurrence} of ${JSON.stringify(current)} at ` +
            `${target.box[0]},${target.box[1]}`);

if (!apply) {
  console.log(`dry run: would rename it to ${JSON.stringify(replacement)} (pass --apply)`);
  socket.close();
  process.exit(0);
}

await click(target.box[0], target.box[1]);
await sleep(900);

// Clicking the label swaps the element out for a field, so the marker placed on the label is gone
// by the time the field exists. The field is found by what it holds instead: it starts out with the
// title being edited.
const written = await evaluate(`(function(){
  var want = ${JSON.stringify(current)};
  var fields = document.querySelectorAll('input,textarea');
  for (var i = 0; i < fields.length; i++) {
    var f = fields[i];
    if (String(f.value || '').replace(/\\s+/g, ' ').trim() !== want) continue;
    var r = f.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    var value = ${JSON.stringify(replacement)};
    var proto = f.tagName === 'INPUT' ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(f, value);
    f.dispatchEvent(new Event('input', { bubbles: true }));
    f.dispatchEvent(new Event('change', { bubbles: true }));
    f.focus();
    return f.tagName + '#' + i + '=' + f.value;
  }
  var editable = document.querySelector('[contenteditable="true"]');
  if (editable) {
    editable.textContent = ${JSON.stringify(replacement)};
    editable.dispatchEvent(new Event('input', { bubbles: true }));
    return 'contenteditable=' + editable.textContent;
  }
  return 'no field holds ' + JSON.stringify(want);
})()`);
console.log(`write: ${written}`);

// Enter commits the row's edit; the page then saves on submit.
await rpc(socket, 'Input.dispatchKeyEvent',
          { type: 'rawKeyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
await rpc(socket, 'Input.dispatchKeyEvent',
          { type: 'char', key: 'Enter', text: '\r', unmodifiedText: '\r' });
await rpc(socket, 'Input.dispatchKeyEvent',
          { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
await sleep(1500);

const after = await evaluate(`(function(){
  var out = [];
  var all = document.querySelectorAll('div.pure-text.pure-text-editable');
  for (var i = 0; i < all.length && out.length < 4; i++) {
    var t = (all[i].textContent || '').replace(/\\s+/g, ' ').trim();
    if (t.indexOf('ESLint') !== -1 || t.indexOf('课程概述') !== -1) out.push(t);
  }
  return out;
})()`);
console.log('rows now:', JSON.stringify(after));
console.log('submit the edit to save it');
socket.close();
