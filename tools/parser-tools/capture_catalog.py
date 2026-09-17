"""Capture catalog/download button traffic with call stacks and decrypted payloads.

Evidence stays in a separate ignored directory; console output omits credentials.
"""
import argparse
import datetime
import json
from pathlib import Path
import struct
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "captured/runtime"))
import frida
from capture_all import JS as NETWORK_HOOKS

EXTRA = r"""
const playerDll = Process.getModuleByName('PlayerLibRender56_vs.dll');
const playerExe = Process.getModuleByName('EVPlayer2.exe');
for (const [rva, expected] of [
  [0xb6450, '48895c24205556574154415541564157'],
  [0xa3b50, '48895c24085556574154415541564157'],
  [0xa3780, '48895c2408555657415641574881ecd0'],
  [0x134940, '40555356574154415541564157488dac']
]) {
  const actual = Array.from(new Uint8Array(playerExe.base.add(rva).readByteArray(expected.length / 2)))
    .map(x => x.toString(16).padStart(2,'0')).join('');
  if (actual !== expected) throw new Error('Unsupported executable at RVA ' + rva.toString(16));
}
function stack(ctx) {
  return Thread.backtrace(ctx, Backtracer.ACCURATE).map(a => {
    const m = Process.findModuleByAddress(a);
    return m ? m.name + '+' + a.sub(m.base) : a.toString();
  });
}
function bytes(p) {
  const n = p.add(16).readU64().toNumber(), cap = p.add(24).readU64().toNumber();
  if(n < 0 || n > 16 * 1024 * 1024 || cap < n) return null;
  return (cap < 16 ? p : p.readPointer()).readByteArray(n);
}
// Located through getEvsSignUrl/getDownEVSKey xrefs in the local Ghidra image.
Interceptor.attach(playerExe.base.add(0xb6450), {
  onEnter(args) { try { const data=bytes(args[1]);
    if(data) send({event:'request-json',trace:stack(this.context)},data);
  }catch(e){} }
});
Interceptor.attach(playerExe.base.add(0xa3b50), {
  onEnter(args) { this.out=args[1]; },
  onLeave() { try { const data=bytes(this.out);
    if(data) send({event:'signed-json'},data);
  }catch(e){} }
});
Interceptor.attach(playerExe.base.add(0xa3780), {
  onEnter(args) { try {
    const endpoint=bytes(args[1]), data=bytes(args[2]);
    if(endpoint) send({event:'dispatch-endpoint',trace:stack(this.context)},endpoint);
    if(data) send({event:'dispatch-body'},data);
  }catch(e){} }
});
// courseform.cpp's refresh completion callback, found through the error log xref.
Interceptor.attach(playerExe.base.add(0x134940), {
  onEnter(args) { try {
    const data=bytes(args[3]);
    if(data) send({event:'catalog-response',code:args[1].toInt32()},data);
  }catch(e){} }
});
Interceptor.attach(playerDll.base.add(0x1EA10), {
  onEnter(args) { this.out=args[0]; this.trace=stack(this.context); },
  onLeave() { try { const data=bytes(this.out); if(data) send({event:'decoded',trace:this.trace},data); }catch(e){} }
});
Interceptor.attach(playerDll.base.add(0x1FD60), {
  onEnter(args) { try { const input=args[1].readUtf8String();
    if(input && input.startsWith('app_version=')) send({event:'sign-input',input,trace:stack(this.context)});
  }catch(e){} }
});
const traced=new Set();
for(const name of ['nghttp2_submit_request','nghttp2_submit_request2']) {
  const p=Process.getModuleByName('nghttp2.dll').findExportByName(name);
  if(!p || traced.has(p.toString()))continue;
  traced.add(p.toString());
  Interceptor.attach(p, {onEnter(args){send({event:'request-stack',trace:stack(this.context),session:args[0].toString()});}});
}
rpc.exports = {
  modules() {return Process.enumerateModules().map(m=>({name:m.name,path:m.path,base:m.base.toString(),size:m.size}));},
  read(address,size) {return ptr(address).readByteArray(size);}
};
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--seconds', type=int, default=1800)
    parser.add_argument('--dump-exe', action='store_true')
    args = parser.parse_args()
    out = HERE / 'captured/catalog' / datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    out.mkdir(parents=True)
    print(f'Evidence: {out}', flush=True)
    session = frida.attach(args.pid)
    script = session.create_script(NETWORK_HOOKS + EXTRA)
    sequence = 0
    with (out / 'events.jsonl').open('w', encoding='utf-8') as log:
        def received(message, data):
            nonlocal sequence
            if message.get('type') != 'send':
                print(message.get('description', 'capture error'), flush=True)
                return
            event = dict(message['payload'], at=time.time())
            kind = event.get('event')
            if data is not None:
                sequence += 1
                filename = f'{sequence:06d}-{kind}.bin'
                (out / filename).write_bytes(data)
                event.update(file=filename, size=len(data))
            log.write(json.dumps(event, ensure_ascii=False) + '\n')
            log.flush()
            if kind == 'h2req':
                headers = dict(event.get('hdrs', []))
                print('REQUEST', headers.get(':path'), flush=True)
            elif kind in ('ready', 'decoded', 'z-inflate', 'z-uncompress'):
                print(kind, event.get('size', ''), flush=True)
        script.on('message', received)
        try:
            script.load()
            modules = script.exports_sync.modules()
            (out / 'modules.json').write_text(json.dumps(modules, indent=2), encoding='utf-8')
            if args.dump_exe:
                module = next(m for m in modules if m['name'].lower() == 'evplayer2.exe')
                base = int(module['base'], 16)
                dump = bytearray(module['size'])
                for offset in range(0, len(dump), 4096):
                    try:
                        size = min(4096, len(dump) - offset)
                        dump[offset:offset + size] = script.exports_sync.read(hex(base + offset), size)
                    except frida.RPCException:
                        pass
                # Map the in-memory image as a PE whose raw section offsets equal RVAs.
                pe = struct.unpack_from('<I', dump, 0x3c)[0]
                count = struct.unpack_from('<H', dump, pe + 6)[0]
                opt_size = struct.unpack_from('<H', dump, pe + 20)[0]
                sections = pe + 24 + opt_size
                for index in range(count):
                    pos = sections + index * 40
                    virtual_size, rva = struct.unpack_from('<II', dump, pos + 8)
                    struct.pack_into('<II', dump, pos + 16, virtual_size, rva)
                (out / 'EVPlayer2-memory.exe').write_bytes(dump)
                print('Executable memory image saved', flush=True)
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline and not (out / 'STOP').exists():
                time.sleep(.5)
        finally:
            session.detach()


if __name__ == '__main__':
    main()
