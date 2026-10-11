// Attach files to a page's <input type=file> over CDP.
//
// agent-browser 0.19.0's own `upload` command prints nothing and never returns in this
// environment -- with a hidden input, a visible one, an MP4, a PNG, a Chinese path and an ASCII
// one alike -- and it leaves the daemon unable to answer anything until it is restarted. Every
// other command works, so this replaces that single step and leaves agent-browser driving.
//
//   node cdp-set-files.mjs <cdp-port> <css-selector> <file...>
//
// The port is the first line of the browser profile's DevToolsActivePort file.

import { readFileSync } from 'node:fs';

const [, , port, selector, ...files] = process.argv;
if (!port || !selector || files.length === 0) {
  console.error('usage: node cdp-set-files.mjs <cdp-port> <css-selector> <file...>');
  process.exit(1);
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
const page = targets.find((t) => t.type === 'page' && String(t.url).includes('bilibili'));
if (!page) {
  console.error('no bilibili page target on port ' + port);
  process.exit(2);
}

const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve);
  socket.addEventListener('error', () => reject(new Error('websocket failed')));
});

const handle = await rpc(socket, 'Runtime.evaluate', {
  expression: `document.querySelector(${JSON.stringify(selector)})`
});
if (!handle?.result?.objectId) {
  console.error('selector matched nothing: ' + selector);
  process.exit(3);
}

await rpc(socket, 'DOM.setFileInputFiles', { files, objectId: handle.result.objectId });
console.log(`attached ${files.length} file(s) to ${selector} on ${page.url}`);
socket.close();
