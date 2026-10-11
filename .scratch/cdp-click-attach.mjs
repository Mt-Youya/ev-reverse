// Click an element with a real mouse event and hand the resulting file chooser a list of files.
//
// This is the whole of what agent-browser's `upload` was supposed to do. In 0.19.0 that command
// prints nothing and never returns here, and it leaves the daemon unable to answer anything until
// it is restarted; a raw CDP click plus file-chooser interception does the same job and returns.
//
// Bilibili needs the trusted event: a synthetic element.click() on its Vue components does
// nothing, which is why the click goes through Input.dispatchMouseEvent rather than JS.
//
//   node cdp-click-attach.mjs <cdp-port> <selector> <file...>
//
// Prints "chooser" when the click opened a file chooser, or "no-chooser" when it did not, so the
// caller can tell which of the page's two upload paths it just took.

const [, , port, selector, ...files] = process.argv;
if (!port || !selector || files.length === 0) {
  console.error('usage: node cdp-click-attach.mjs <cdp-port> <selector> <file...>');
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

const chooser = new Promise((resolve) => {
  const onMessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.method === 'Page.fileChooserOpened') {
      socket.removeEventListener('message', onMessage);
      resolve(message.params);
    }
  };
  socket.addEventListener('message', onMessage);
  setTimeout(() => resolve(null), 8000);
});

await rpc(socket, 'Page.enable', {});
await rpc(socket, 'Page.setInterceptFileChooserDialog', { enabled: true });

const box = await rpc(socket, 'Runtime.evaluate', {
  expression: `(function(){var e=document.querySelector(${JSON.stringify(selector)});
    if(!e) return null; e.scrollIntoView({block:'center'});
    var r=e.getBoundingClientRect();
    return {x: r.left + r.width/2, y: r.top + r.height/2, tag: e.tagName};})()`,
  returnByValue: true
});
const point = box?.result?.value;
if (!point) {
  console.error('selector matched nothing: ' + selector);
  process.exit(3);
}

const base = { x: Math.round(point.x), y: Math.round(point.y), button: 'left', clickCount: 1 };
await rpc(socket, 'Input.dispatchMouseEvent', { type: 'mouseMoved', ...base });
await rpc(socket, 'Input.dispatchMouseEvent', { type: 'mousePressed', ...base });
await rpc(socket, 'Input.dispatchMouseEvent', { type: 'mouseReleased', ...base });

const opened = await chooser;
if (!opened) {
  console.log(`no-chooser (clicked ${point.tag} ${selector} at ${base.x},${base.y})`);
  socket.close();
  process.exit(0);
}

await rpc(socket, 'DOM.setFileInputFiles', { files, backendNodeId: opened.backendNodeId });
console.log(`chooser: attached ${files.length} file(s) to the input the click opened`);
socket.close();
