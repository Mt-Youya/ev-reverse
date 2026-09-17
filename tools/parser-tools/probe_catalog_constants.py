"""Read the local player's obfuscated catalog field names through its string decoder."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'captured/runtime'))
import frida

JS = r"""
rpc.exports.session = function() {
  const m=Process.getModuleByName('EVPlayer2.exe');
  const result={};
  for(const [name,rva] of [['data_key',0x96110],['sign_secret',0x97320],['token',0x97540],
                          ['machine_id',0x96f90]]) {
    const out=Memory.alloc(32);
    new NativeFunction(m.base.add(rva),'pointer',['pointer'])(out);
    const size=out.add(16).readU64().toNumber(), cap=out.add(24).readU64().toNumber();
    if(size>8192 || size===0 || cap<size) throw new Error('Missing '+name);
    result[name]=(cap<16?out:out.readPointer()).readUtf8String(size);
  }
  result.busi_id=new NativeFunction(m.base.add(0x96610),'int',[])();
  return result;
};
rpc.exports.refresh = function(account) {
  const m=Process.getModuleByName('EVPlayer2.exe'), fn=m.base.add(0xa79e0);
  const expected='4055535657415441564157488dac24d0';
  const actual=Array.from(new Uint8Array(fn.readByteArray(expected.length/2)))
    .map(x=>x.toString(16).padStart(2,'0')).join('');
  if(actual!==expected) throw new Error('Unsupported catalog request function');
  const net=m.base.add(0x99a0b0).readPointer();
  if(net.isNull()) throw new Error('Player network context is not initialized');
  const callback=Memory.alloc(64);
  callback.writeByteArray(new Uint8Array(64));
  new NativeFunction(fn,'void',['pointer','uint64','uint32','pointer'])(net,0,account,callback);
  return {requested_account:account};
};
rpc.exports.constants = function(items) {
  const m = Process.getModuleByName('EVPlayer2.exe');
  const bridge = m.base.add(0x999ff8).readPointer().add(0x78).readPointer();
  const fn = bridge.readPointer().add(0xd0).readPointer();
  const module = Process.findModuleByAddress(fn);
  if(module.name !== 'Bridge.dll' || !fn.sub(module.base).equals(ptr(0x29c00)))
    throw new Error('Unsupported Bridge decoder');
  const decode = new NativeFunction(fn,'pointer',['pointer','pointer','pointer','uint64']);
  const construct = new NativeFunction(m.base.add(0x9db128).readPointer(),'pointer',['pointer','pointer']);
  const destroy = new NativeFunction(m.base.add(0x9db160).readPointer(),'void',['pointer']);
  const get = new NativeFunction(bridge.readPointer().add(0x40).readPointer(),
    'pointer',['pointer','pointer','pointer','int']);
  return items.map(([rva,length]) => {
    const input = m.base.add(rva), out=Memory.alloc(32);
    decode(bridge,out,input,length);
    const size = out.add(16).readU64().toNumber(), cap=out.add(24).readU64().toNumber();
    if(size > 1024 || cap < size) throw new Error('Invalid decoder result: size='+size+' cap='+cap);
    const name=Memory.alloc(64), value=Memory.alloc(64);
    construct(name,out);
    const result=get(bridge,value,name,0xa6);
    const pointer=result.add(0x30).readPointer();
    const text=pointer.isNull()?null:pointer.readUtf8String();
    destroy(value); destroy(name);
    return {rva:'0x'+rva.toString(16),value:text};
  });
};
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--refresh-account', type=int)
    parser.add_argument('--session-out', type=Path)
    parser.add_argument('rva', nargs='*', type=lambda value: [int(x, 0) for x in value.split(':')])
    args = parser.parse_args()
    session = frida.attach(args.pid)
    try:
        script = session.create_script(JS)
        script.load()
        if args.rva:
            print(json.dumps(script.exports_sync.constants(args.rva), ensure_ascii=False, indent=2))
        if args.refresh_account is not None:
            print(json.dumps(script.exports_sync.refresh(args.refresh_account)))
        if args.session_out:
            args.session_out.write_text(json.dumps(script.exports_sync.session()), encoding='utf-8')
            print('Session saved; credentials omitted from console')
    finally:
        session.detach()


if __name__ == '__main__':
    main()
