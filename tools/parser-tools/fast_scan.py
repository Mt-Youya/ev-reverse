"""在播放器内存里找 32 位十六进制串，只针对"正在播放的分片"（最近被改写的文件）测试。

全量 4153 个文件做测试太慢，而真正在解密的一定是最近被写入的那批。
"""
import hashlib, os, sys, time
import frida
from Crypto.Cipher import AES

DL = r"D:\Downloads\EVPlayer2Downloads"
JS = r"""
function isHex(c){return (c>=0x30&&c<=0x39)||(c>=0x61&&c<=0x66)||(c>=0x41&&c<=0x46);}
rpc.exports = { hexstrings: function(){
  var found={};
  Process.enumerateRanges('rw-').forEach(function(r){
    if (r.size > 0x10000000) return;
    var CH=0x200000;
    for (var off=0; off<r.size; off+=CH){
      var len=Math.min(CH, r.size-off), buf;
      try { buf = r.base.add(off).readByteArray(len); } catch(e){ continue; }
      if(!buf) continue;
      var u=new Uint8Array(buf), start=-1;
      for (var i=0;i<=u.length;i++){
        var c=(i<u.length)?u[i]:0;
        if(isHex(c)){ if(start<0) start=i; }
        else { if(start>=0 && (i-start)===32){ var s=''; for(var k=start;k<i;k++) s+=String.fromCharCode(u[k]); found[s.toLowerCase()]=1; } start=-1; }
      }
    }
  });
  return Object.keys(found);
}};
send({t:'ready'});
"""
pid = int(sys.argv[1]) if len(sys.argv) > 1 else 12788
mins = int(sys.argv[2]) if len(sys.argv) > 2 else 6

segs = []
cut = time.time() - mins * 60
for n in os.listdir(DL):
    if not n.endswith(".ts"): continue
    p = os.path.join(DL, n)
    try:
        if os.path.getmtime(p) >= cut: segs.append(n)
    except OSError: pass
print("最近 %d 分钟被写过的分片: %d" % (mins, len(segs)))

heads, masks = {}, {}
for n in segs:
    try:
        with open(os.path.join(DL, n), "rb") as fh: data = fh.read(16)
        if len(data) < 16: continue
        m = hashlib.md5(n.encode()).hexdigest()[:16].encode()
        masks[n] = m
        heads[n] = bytes(b ^ m[i % 16] for i, b in enumerate(data))
    except OSError: continue
print("可读: %d" % len(heads))

session = frida.attach(pid)
script = session.create_script(JS)
script.on("message", lambda m, d: None)
script.load()
t0 = time.time()
cands = [c.lower() for c in script.exports_sync.hexstrings()]
print("内存中 32 位十六进制串: %d  (耗时 %.1fs)" % (len(cands), time.time() - t0))

surv = []
for c in cands:
    aes = AES.new(c.encode(), AES.MODE_ECB)
    for n, blk in heads.items():
        if aes.decrypt(blk)[0] == 0x47:
            surv.append((c, n))
print("通过首字节筛选: %d" % len(surv))

hits = 0
for c, n in surv:
    data = open(os.path.join(DL, n), "rb").read(188 * 60)
    m = masks[n]
    buf = bytes(b ^ m[i % 16] for i, b in enumerate(data))
    p = AES.new(c.encode(), AES.MODE_ECB).decrypt(buf)
    if sum(1 for i in range(0, len(p), 188) if p[i] == 0x47) == len(p) // 188:
        hits += 1
        print("\n*** 找到密钥 ***")
        print("    分片: %s" % n)
        print("    密钥: %s" % c)
print("\n严格验证命中: %d" % hits)
