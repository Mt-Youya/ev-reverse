"""扫描播放器内存中的分片密钥，测试全部已下载分片，结果完整写入 JSON。

关键：这次不做任何 tail 截断，全部结果落盘到 captured/keys_found.json。
"""
import hashlib, json, os, sys, time
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

segs = [n for n in os.listdir(DL) if n.endswith(".ts")]
print("磁盘分片: %d" % len(segs), flush=True)
heads, masks = {}, {}
for n in segs:
    try:
        with open(os.path.join(DL, n), "rb") as fh: data = fh.read(16)
        if len(data) < 16: continue
        m = hashlib.md5(n.encode()).hexdigest()[:16].encode()
        masks[n] = m
        heads[n] = bytes(b ^ m[i % 16] for i, b in enumerate(data))
    except OSError: continue
print("可读: %d" % len(heads), flush=True)

session = frida.attach(pid)
script = session.create_script(JS)
script.on("message", lambda m, d: None)
script.load()
t0 = time.time()
cands = [c.lower() for c in script.exports_sync.hexstrings()]
print("内存中 32 位十六进制串: %d (%.1fs)" % (len(cands), time.time() - t0), flush=True)

surv = []
for c in cands:
    aes = AES.new(c.encode(), AES.MODE_ECB)
    for n, blk in heads.items():
        if aes.decrypt(blk)[0] == 0x47:
            surv.append((c, n))
print("通过首字节筛选: %d" % len(surv), flush=True)

found = {}
for c, n in surv:
    data = open(os.path.join(DL, n), "rb").read(188 * 40)
    m = masks[n]
    buf = bytes(b ^ m[i % 16] for i, b in enumerate(data))
    p = AES.new(c.encode(), AES.MODE_ECB).decrypt(buf)
    if sum(1 for i in range(0, len(p), 188) if p[i] == 0x47) == len(p) // 188:
        found[n] = c
print("严格验证命中: %d" % len(found), flush=True)

os.makedirs("captured", exist_ok=True)
json.dump({"pid": pid, "candidates": len(cands), "found": found},
          open("captured/keys_found.json", "w"), indent=1)
print("已写入 captured/keys_found.json", flush=True)
