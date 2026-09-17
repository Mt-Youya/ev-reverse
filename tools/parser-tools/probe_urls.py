#!/usr/bin/env python3
"""EVPlayer2 URL probe — attach to the running player and print the HTTP(S)
request URLs it issues through Qt5Network, plus writes them to urls.log.

Usage:
    python probe_urls.py
Keep EVPlayer2 running, then browse the course catalog / open a video.
"""
import sys
import frida

PROCESS = "EVPlayer2.exe"

JS = r"""
'use strict';
function findExports(mod, cls, sub) {
  const out = [];
  try {
    Module.enumerateExportsSync(mod).forEach(function (e) {
      if (e.name.indexOf(cls) === -1) return;
      if (e.name.indexOf(sub) === -1) return;
      if (e.name.indexOf('??_E') !== -1) return; // skip deleting destructors
      out.push(e.name);
    });
  } catch (err) {}
  return out;
}

let reqUrlFn = null;
let urlToStringFn = null;
const scratch1 = Memory.alloc(64);
const scratch2 = Memory.alloc(64);

function setup() {
  // QNetworkRequest::url() const -> QUrl  (MSVC sret: rcx=buf, rdx=this, rax=buf)
  const reqCands = findExports('Qt5Network.dll', 'QNetworkRequest', '@url@');
  if (reqCands.length) {
    const addr = Module.findExportByName('Qt5Network.dll', reqCands[0]);
    reqUrlFn = new NativeFunction(addr, 'pointer', ['pointer', 'pointer']);
    send({event: 'setup', item: 'request.url()', symbol: reqCands[0]});
  }
  // QUrl::toString(FormattingOptions, QUrlTwoFlags) const -> QString (sret)
  const urlCands = findExports('Qt5Core.dll', 'QUrl', '@toString@');
  if (urlCands.length) {
    const addr = Module.findExportByName('Qt5Core.dll', urlCands[0]);
    urlToStringFn = new NativeFunction(addr, 'pointer', ['pointer', 'pointer', 'uint', 'uint']);
    send({event: 'setup', item: 'QUrl::toString', symbol: urlCands[0]});
  }
}

function utf16len(ptr) {
  // ptr points at a Qt5 QString object: single QArrayData* at offset 0
  try {
    const d = ptr.readPointer();
    if (d.isNull()) return null;
    const len = d.add(4).readU32();
    if (len > 4096) return null;
    if (len === 0) return '';
    return d.add(16).readUtf16String(len);
  } catch (e) { return null; }
}

function readUrl(reqPtr) {
  try {
    if (!reqUrlFn) return '(no url() hook)';
    scratch1.writeU64(0); scratch1.add(8).writeU64(0);
    const qurlObj = reqUrlFn(scratch1, reqPtr);
    if (qurlObj.isNull()) return '(null)';
    const qurlPriv = qurlObj.readPointer();
    if (qurlPriv.isNull()) return '(empty url)';
    if (!urlToStringFn) return '(no toString hook)';
    scratch2.writeU64(0); scratch2.add(8).writeU64(0);
    const qstrObj = urlToStringFn(scratch2, qurlObj, 0, 0);
    if (qstrObj.isNull()) return '(null string)';
    return utf16len(qstrObj) || '(unreadable)';
  } catch (e) { return '(error: ' + e + ')'; }
}

function hookMethods() {
  const mod = 'Qt5Network.dll';
  ['get@', 'post@', 'put@', 'sendCustomRequest@', 'deleteResource@'].forEach(function (sub) {
    findExports(mod, 'QNetworkAccessManager', sub).forEach(function (sym) {
      const addr = Module.findExportByName(mod, sym);
      if (!addr) return;
      try {
        Interceptor.attach(addr, {
          onEnter: function (args) {
            const method = sub.split('@')[0];
            const url = readUrl(args[1]);
            const line = '[' + new Date().toISOString().slice(11, 19) + '] '
              + method.toUpperCase() + ' ' + url;
            send({event: 'url', line: line});
          }
        });
        send({event: 'setup', item: 'hook', symbol: sym});
      } catch (e) {
        send({event: 'setup', item: 'hook-failed', symbol: sym, error: String(e)});
      }
    });
  });
}

setup();
hookMethods();
send({event: 'ready'});
"""


def main():
    try:
        session = frida.attach(PROCESS)
    except frida.ProcessNotFoundError:
        print("找不到 %s 进程，请先启动 EVPlayer2" % PROCESS)
        return 1

    def on_message(msg, data):
        if msg.get('type') == 'send':
            p = msg['payload']
            if p.get('event') == 'url':
                print(p['line'], flush=True)
                with open('urls.log', 'a', encoding='utf-8') as f:
                    f.write(p['line'] + '\n')
            elif p.get('event') == 'setup':
                print('[setup]', p.get('item'), p.get('symbol', ''),
                      p.get('error', ''), flush=True)
            elif p.get('event') == 'ready':
                print('探针已挂载。现在去播放器里浏览课程目录、点开视频，'
                      '这里会实时打印请求 URL（同时写入 urls.log）。'
                      'Ctrl+C 退出。', flush=True)

    script = session.create_script(JS)
    script.on('message', on_message)
    script.load()
    try:
        sys.stdin.read()
    except KeyboardInterrupt:
        pass
    session.detach()
    return 0


if __name__ == '__main__':
    sys.exit(main())
