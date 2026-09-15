<#
EVPlayer2 5.0.5 live manifest collector.
Run this inside the same Windows session as the playing EVPlayer2 process.
It supports only the verified 64-bit PlayerLibRender56_vs.dll layout for 5.0.5.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InputPath,
    [string]$OutputDir = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$toolErrorPath = Join-Path $OutputDir 'capture_error.txt'
trap {
    $_ | Out-File -LiteralPath $toolErrorPath -Encoding UTF8
    exit 1
}

Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
public static class EVLive {
 [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint a,bool i,int p);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
 [DllImport("kernel32.dll")] static extern bool ReadProcessMemory(IntPtr h,IntPtr a,byte[] b,UIntPtr n,out UIntPtr r);
 [DllImport("kernel32.dll")] static extern UIntPtr VirtualQueryEx(IntPtr h,IntPtr a,out MBI m,UIntPtr n);
 [StructLayout(LayoutKind.Sequential)] struct MBI { public ulong BaseAddress,AllocationBase; public uint AllocationProtect; public ushort PartitionId; public ulong RegionSize; public uint State,Protect,Type; }
 public class Context { public string File,Token,Schedule; public int Index; public bool Ready; }
 static byte[] Read(IntPtr h,ulong a,int n) { var b=new byte[n];UIntPtr r;if(!ReadProcessMemory(h,(IntPtr)a,b,(UIntPtr)n,out r)||r.ToUInt64()!=(ulong)n)return null;return b; }
 static string Str(IntPtr h,byte[] b,int o) { ulong n=BitConverter.ToUInt64(b,o+16),c=BitConverter.ToUInt64(b,o+24);if(n>8192||c<n||c>1048576)return null;byte[] s;if(c<16){s=new byte[n];Array.Copy(b,o,s,0,(int)n);}else{s=Read(h,BitConverter.ToUInt64(b,o),(int)n);}return s==null?null:Encoding.UTF8.GetString(s); }
 static bool Hex(string s) { if(s==null||s.Length!=32)return false;foreach(char c in s)if(!Uri.IsHexDigit(c))return false;return true; }
 static IEnumerable<Tuple<ulong,ulong>> Regions(IntPtr h) { ulong a=0;MBI m;while(VirtualQueryEx(h,(IntPtr)a,out m,(UIntPtr)Marshal.SizeOf(typeof(MBI))).ToUInt64()!=0){ulong end=m.BaseAddress+m.RegionSize;if(end<=a)yield break;if(m.State==0x1000&&(m.Protect&0x101)==0)yield return Tuple.Create(m.BaseAddress,end);a=end;} }
 public static List<Context> Capture(int pid) {
   var p=Process.GetProcessById(pid);ulong dll=0;foreach(ProcessModule m in p.Modules)if(m.ModuleName.Equals("PlayerLibRender56_vs.dll",StringComparison.OrdinalIgnoreCase))dll=(ulong)m.BaseAddress.ToInt64();if(dll==0)throw new Exception("PlayerLibRender56_vs.dll is not loaded.");
   IntPtr h=OpenProcess(0x410,false,pid);if(h==IntPtr.Zero)throw new Exception("Cannot read EVPlayer2 process memory.");var outp=new List<Context>();var seen=new HashSet<ulong>();
   try { foreach(var region in Regions(h)) if(region.Item1!=0) for(ulong chunk=region.Item1;chunk<region.Item2;chunk+=0x100000){int n=(int)Math.Min((ulong)0x100300,region.Item2-chunk);var d=Read(h,chunk,n);if(d==null)continue;for(int i=0;i+8<=n;i+=8){ulong vt=BitConverter.ToUInt64(d,i);if(vt<dll+0x802000||vt>dll+0x804000)continue;ulong o=chunk+(ulong)i;if(!seen.Add(o))continue;var b=Read(h,o,0x2a8);if(b==null)continue;var mask=Str(h,b,0x268);var file=Str(h,b,0x18);if(!Hex(mask)||file==null||!file.EndsWith(".ts",StringComparison.OrdinalIgnoreCase))continue;var sch=new byte[32];Array.Copy(b,0x120,sch,0,32);outp.Add(new Context{File=file,Token=Str(h,b,0x288),Schedule=BitConverter.ToString(sch).Replace("-",""),Index=BitConverter.ToInt32(b,8),Ready=b[0x264]==1});}} }
   finally { CloseHandle(h); } return outp;
 }
 static byte XT(byte x){return (byte)((x<<1)^((x&0x80)!=0?0x1b:0));}
 static byte[] Mix(byte[] b){var o=new byte[16];for(int i=0;i<16;i+=4){byte t=(byte)(b[i]^b[i+1]^b[i+2]^b[i+3]);for(int j=0;j<4;j++)o[i+j]=(byte)(b[i+j]^t^XT((byte)(b[i+j]^b[i+(j+1)%4])));}return o;}
 public static string KeyFromSchedule(string hex){var s=new byte[32];for(int i=0;i<32;i++)s[i]=Convert.ToByte(hex.Substring(i*2,2),16);var k=new byte[32];var second=new byte[16];Array.Copy(s,k,16);Array.Copy(s,16,second,0,16);Array.Copy(Mix(second),0,k,16,16);return Encoding.ASCII.GetString(k);}
 static string Md5(string s){using(var m=MD5.Create())return BitConverter.ToString(m.ComputeHash(Encoding.UTF8.GetBytes(s))).Replace("-","").ToLowerInvariant();}
 public static string FindSalt(int pid,string token,string file,string expected) {
   IntPtr h=OpenProcess(0x410,false,pid);if(h==IntPtr.Zero)return null;var tested=new HashSet<string>();byte[] prefix=Encoding.UTF8.GetBytes(token+file);
   try { foreach(var region in Regions(h)) for(ulong chunk=region.Item1;chunk<region.Item2;chunk+=0x100000){int n=(int)Math.Min((ulong)0x100100,region.Item2-chunk);var b=Read(h,chunk,n);if(b==null)continue;for(int i=0;i<n;i++){if(b[i]<32||b[i]>126)continue;int start=i;while(i<n&&b[i]>=32&&b[i]<=126)i++;int len=i-start;if(len<1||len>128||i>=n||b[i]!=0)continue;string candidate=Encoding.ASCII.GetString(b,start,len);if(!tested.Add(candidate))continue;var input=new byte[prefix.Length+len];Array.Copy(prefix,input,prefix.Length);Array.Copy(b,start,input,prefix.Length,len);using(var md5=MD5.Create()){var hsh=BitConverter.ToString(md5.ComputeHash(input)).Replace("-","").ToLowerInvariant();if(hsh==expected)return candidate;}}} }
   finally { CloseHandle(h); } return null;
 }
}
'@

function Get-InputMembers {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path -PathType Container) {
        $map = @{}
        Get-ChildItem -LiteralPath $Path -File | ForEach-Object {
            if ($map.ContainsKey($_.Name)) { throw "Duplicate input filename: $($_.Name)" }
            $map[$_.Name] = @{ Bytes = [IO.File]::ReadAllBytes($_.FullName) }
        }
        return $map
    }
    $zip = [IO.Compression.ZipFile]::OpenRead($Path)
    try {
        $map = @{}
        foreach ($entry in $zip.Entries) {
            if ([string]::IsNullOrEmpty($entry.Name)) { continue }
            if ($map.ContainsKey($entry.Name)) { throw "Duplicate archive filename: $($entry.Name)" }
            $stream = $entry.Open(); $buffer = New-Object IO.MemoryStream
            try { $stream.CopyTo($buffer); $map[$entry.Name] = @{ Bytes = $buffer.ToArray() } }
            finally { $buffer.Dispose(); $stream.Dispose() }
        }
        return $map
    } finally { $zip.Dispose() }
}

$source = (Resolve-Path -LiteralPath $InputPath).Path
$destination = [IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Force -Path $destination | Out-Null
$members = Get-InputMembers $source
$proc = Get-Process -Name EVPlayer2 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $proc) { throw 'EVPlayer2 is not running. Start the target video, wait for playback, then run this script again.' }
$contexts = @([EVLive]::Capture($proc.Id) | Where-Object { $members.ContainsKey($_.File) -and $_.Token -and $_.Token.Length -eq 32 } | Sort-Object Index)
if ($contexts.Count -eq 0) { throw 'No current EVPlayer2 playback segments match the supplied ZIP or directory.' }
$indices = @($contexts | ForEach-Object Index)
if (($indices -join ',') -ne ((0..($contexts.Count - 1)) -join ',')) { throw 'The matched segment list is incomplete or has duplicate playback indexes. Let the video keep playing, then retry.' }
$probe = $contexts | Where-Object Ready | Select-Object -First 1
if ($null -eq $probe) { throw 'The player has no initialized segment yet. Start or resume the target video, wait a few seconds, then retry.' }
$salt = [EVLive]::FindSalt($proc.Id, $probe.Token, $probe.File, [EVLive]::KeyFromSchedule($probe.Schedule))
if ([string]::IsNullOrEmpty($salt)) { throw 'Could not recover the active EVPlayer2 runtime parameter. This build may not match 5.0.5, or playback is not fully initialized.' }
$sha = [Security.Cryptography.SHA256]::Create()
$md5 = [Security.Cryptography.MD5]::Create()
try {
    $segments = foreach ($ctx in $contexts) {
        $keyText = ([BitConverter]::ToString($md5.ComputeHash([Text.Encoding]::UTF8.GetBytes($ctx.Token + $ctx.File + $salt)))).Replace('-', '').ToLower()
        $maskText = ([BitConverter]::ToString($md5.ComputeHash([Text.Encoding]::UTF8.GetBytes($ctx.File)))).Replace('-', '').ToLower().Substring(0, 16)
        $keyAscii = [Text.Encoding]::ASCII.GetBytes($keyText)
        $maskAscii = [Text.Encoding]::ASCII.GetBytes($maskText)
        [pscustomobject]@{ index=$ctx.Index; file=$ctx.File; key_hex=[BitConverter]::ToString($keyAscii).Replace('-','').ToLower(); xor_mask_hex=[BitConverter]::ToString($maskAscii).Replace('-','').ToLower(); encrypted_sha256=[BitConverter]::ToString($sha.ComputeHash($members[$ctx.File].Bytes)).Replace('-','').ToLower() }
    }
} finally { $sha.Dispose(); $md5.Dispose() }
$manifest = [pscustomobject]@{ tool='EVPlayer2 5.0.5 live collector'; generated_at=(Get-Date -Format o); source=(Split-Path -Leaf $source); segment_count=@($segments).Count; segments=@($segments) }
$path = Join-Path $destination 'evplayer2_manifest.json'
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $path -Encoding UTF8
Remove-Item -LiteralPath $toolErrorPath -Force -ErrorAction SilentlyContinue
Write-Host "Created $path with $(@($segments).Count) matching segments."
