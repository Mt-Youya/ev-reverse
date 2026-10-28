// Reload a page and report everything it logs, so "the console is clean now" is a measurement.
//
//   node capture-console.mjs <port> <url> [seconds]
//
// Watches Log.entryAdded (browser-level: CORS, network, security) and Runtime.consoleAPICalled
// (page-level: console.error and friends), which are different channels -- a CORS block shows up
// only on the first one.

const [port, url, secondsArg] = process.argv.slice(2);
const seconds = Number(secondsArg || 6);
if (!port || !url) {
  console.error('usage: node capture-console.mjs <port> <url> [seconds]');
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
const page = targets.find((t) => t.type === 'page');
if (!page) {
  console.error('no page target');
  process.exit(2);
}

const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve);
  socket.addEventListener('error', () => reject(new Error('websocket failed')));
});

const found = [];
socket.addEventListener('message', (event) => {
  const message = JSON.parse(event.data);
  if (message.method === 'Log.entryAdded') {
    const entry = message.params.entry;
    found.push(`[log:${entry.level}] ${entry.text}`);
  } else if (message.method === 'Runtime.consoleAPICalled') {
    const text = (message.params.args || [])
      .map((a) => a.value !== undefined ? String(a.value) : (a.description || a.type))
      .join(' ');
    found.push(`[console:${message.params.type}] ${text}`);
  } else if (message.method === 'Runtime.exceptionThrown') {
    const d = message.params.exceptionDetails;
    found.push(`[exception] ${d.text} ${(d.exception && d.exception.description) || ''}`);
  }
});

await rpc(socket, 'Log.enable', {});
await rpc(socket, 'Runtime.enable', {});
await rpc(socket, 'Page.enable', {});
await rpc(socket, 'Page.navigate', { url });
await new Promise((r) => setTimeout(r, seconds * 1000));

const generated = await rpc(socket, 'Runtime.evaluate',
                            { expression: 'window.COURSE_DATA ? window.COURSE_DATA.generated_at : "NO DATA"',
                              returnByValue: true });
const meta = await rpc(socket, 'Runtime.evaluate',
                       { expression: 'document.getElementById("meta") ? document.getElementById("meta").textContent.slice(0,80) : "no meta"',
                         returnByValue: true });

console.log(`page reports: ${generated.result.value}`);
console.log(`meta: ${meta.result.value}`);
console.log('');
const problems = found.filter((line) => /CORS|error|blocked|Refused|Failed/i.test(line));
console.log(`${found.length} console/log line(s), ${problems.length} of them problems:`);
for (const line of found) console.log(`   ${line.slice(0, 200)}`);
socket.close();
process.exit(problems.length ? 1 : 0);
