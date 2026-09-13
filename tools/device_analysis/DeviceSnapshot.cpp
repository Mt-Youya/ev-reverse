#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <tlhelp32.h>
#include <intrin.h>
#include <string>
#include <fstream>
#include <iostream>
#include <stdexcept>

std::string quote(const std::string& s) {
    std::string out="\"";
    for (unsigned char c:s) { if(c=='"'||c=='\\') out+='\\'; if(c<32) throw std::runtime_error("Non-text identity"); out+=c; }
    return out+'"';
}
void read(HANDLE h, uintptr_t p, void* b, size_t n) {
    SIZE_T got=0; if(!ReadProcessMemory(h,(void*)p,b,n,&got)||got!=n) throw std::runtime_error("ReadProcessMemory failed");
}
std::string field(HANDLE h, uintptr_t p) {
    uintptr_t b[4]; read(h,p,b,sizeof(b));
    if(b[3]<15||b[3]>4096||b[2]>b[3]||b[2]>1024) throw std::runtime_error("Identity cache is not initialized");
    std::string out(b[2],'\0'); if(!out.empty()) read(h,b[3]<16?p:b[0],out.data(),out.size()); return out;
}
int wmain(int argc,wchar_t**argv) {
    try {
        if(argc!=3) throw std::runtime_error("Usage: DeviceSnapshot.exe PID output.json");
        DWORD pid=wcstoul(argv[1],nullptr,10);
        HANDLE snap=CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,pid);
        MODULEENTRY32W mod={sizeof(mod)}; uintptr_t base=0;
        if(Module32FirstW(snap,&mod)) do {if(!_wcsicmp(mod.szModule,L"EVPlayer2.exe")){base=(uintptr_t)mod.modBaseAddr;break;}}while(Module32NextW(snap,&mod));
        CloseHandle(snap); if(!base) throw std::runtime_error("EVPlayer2 module not found");
        HANDLE h=OpenProcess(PROCESS_VM_READ|PROCESS_QUERY_LIMITED_INFORMATION,FALSE,pid);
        if(!h) throw std::runtime_error("Cannot open process for reading");
        unsigned char code[6]; read(h,base+0x96f90,code,6);
        const unsigned char expected[]={0x40,0x53,0x48,0x83,0xec,0x20};
        if(memcmp(code,expected,6)) { for(auto c:code) std::cerr<<std::hex<<(int)c<<' '; throw std::runtime_error("Unsupported EVPlayer2 image layout"); }
        auto machine=field(h,base+0x99a1f8),mac=field(h,base+0x99a1c8),hardware=field(h,base+0x99a198);
        CloseHandle(h);
        int cpu[4]; char part[40]; __cpuidex(cpu,1,0); sprintf_s(part,"%08X%08X",cpu[3],cpu[0]); std::string cpuid=part;
        __cpuidex(cpu,3,0); sprintf_s(part,"%08X%08X",cpu[3],cpu[2]); cpuid+=part;
        std::ofstream out(argv[2],std::ios::binary);
        out<<"{\n  \"schema\": 1,\n  \"version\": \"5.0.5\",\n  \"pid\": "<<pid<<",\n  \"computer_name\": "<<quote(machine)<<",\n  \"mac\": "<<quote(mac)<<",\n  \"hardware_id\": "<<quote(hardware)<<",\n  \"cpuid\": "<<quote(cpuid)<<"\n}\n";
        if(!out) throw std::runtime_error("Cannot write snapshot");
        return 0;
    }catch(const std::exception&e){std::cerr<<e.what()<<std::endl;return 1;}
}
