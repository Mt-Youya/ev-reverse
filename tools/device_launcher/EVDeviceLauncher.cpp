#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <bcrypt.h>
#include <tlhelp32.h>
#include <string>
#include <vector>
#include <fstream>
#include <stdexcept>
#include <algorithm>
#pragma comment(lib,"bcrypt.lib")
#pragma comment(lib,"shell32.lib")
#pragma comment(lib,"user32.lib")

// Only the verified, original EVPlayer2 5.0.5 distribution is supported.
const wchar_t* ExpectedHash=L"873f936d3c4555bcbb0cd725b5d0731cfe63407049999e98864d8a734e4ca47a";
struct Handle {
    HANDLE h=nullptr;
    explicit Handle(HANDLE x=nullptr):h(x){}
    ~Handle(){if(h&&h!=INVALID_HANDLE_VALUE)CloseHandle(h);}
    Handle(const Handle&)=delete;
    Handle& operator=(const Handle&)=delete;
};
void require(bool ok,const char* message){if(!ok)throw std::runtime_error(message);}
std::wstring directory(std::wstring s){return s.substr(0,s.find_last_of(L"\\/"));}
std::wstring ini(const std::wstring& path,const wchar_t* key){
    wchar_t b[2048]; GetPrivateProfileStringW(L"Device",key,L"",b,2048,path.c_str()); return b;
}
std::string ascii(const std::wstring& s){
    std::string out; for(auto c:s){require(c>=32&&c<127,"Device profile contains unsupported characters");out+=(char)c;} return out;
}
std::wstring sha256(const std::wstring& path){
    Handle file(CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,0,nullptr));
    require(file.h!=INVALID_HANDLE_VALUE,"Cannot open EVPlayer2 executable");
    BCRYPT_ALG_HANDLE alg=nullptr; BCRYPT_HASH_HANDLE hash=nullptr;
    require(BCryptOpenAlgorithmProvider(&alg,BCRYPT_SHA256_ALGORITHM,nullptr,0)>=0,"SHA256 provider failed");
    if(BCryptCreateHash(alg,&hash,nullptr,0,nullptr,0,0)<0){BCryptCloseAlgorithmProvider(alg,0);throw std::runtime_error("SHA256 init failed");}
    unsigned char buffer[65536],digest[32]; DWORD n=0; bool ok=true;
    for(;;){if(!ReadFile(file.h,buffer,sizeof(buffer),&n,nullptr)){ok=false;break;}if(!n)break;if(BCryptHashData(hash,buffer,n,0)<0){ok=false;break;}}
    if(ok)ok=BCryptFinishHash(hash,digest,sizeof(digest),0)>=0;
    BCryptDestroyHash(hash); BCryptCloseAlgorithmProvider(alg,0); require(ok,"SHA256 read failed");
    std::wstring out;for(auto c:digest){out+=L"0123456789abcdef"[c>>4];out+=L"0123456789abcdef"[c&15];}return out;
}
void write(HANDLE p,uintptr_t address,const void* data,size_t n){
    SIZE_T done=0;require(WriteProcessMemory(p,(void*)address,data,n,&done)&&done==n,"Process write failed");
}
void append64(std::vector<unsigned char>& b,uintptr_t x){for(int j=0;j<8;j++)b.push_back((unsigned char)(x>>(j*8)));}
std::vector<unsigned char> jump(uintptr_t destination){
    std::vector<unsigned char>b={0xff,0x25,0,0,0,0};append64(b,destination);return b;
}
struct Replacement {uintptr_t rva;std::string value;std::vector<unsigned char> signature;};
void install(HANDLE process,uintptr_t base,const std::vector<Replacement>& replacements){
    // All signatures are checked before any code is changed.
    for(const auto&r:replacements){
        unsigned char b[16]; SIZE_T got=0;
        require(ReadProcessMemory(process,(void*)(base+r.rva),b,r.signature.size(),&got)&&got==r.signature.size(),"Cannot read unpacked function");
        require(std::equal(r.signature.begin(),r.signature.end(),b),"Unpacked function signature mismatch");
    }
    for(const auto&r:replacements){
        auto memory=(uintptr_t)VirtualAllocEx(process,nullptr,4096,MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);
        require(memory!=0,"Cannot allocate process-local identity stub");
        // Construct an empty MSVC string, then tail-call the APP'S string assignment
        // helper. Allocation and destruction therefore use the same application CRT.
        std::vector<unsigned char> stub={
            0x48,0xc7,0x01,0,0,0,0,
            0x48,0xc7,0x41,0x08,0,0,0,0,
            0x48,0xc7,0x41,0x10,0,0,0,0,
            0x48,0xc7,0x41,0x18,15,0,0,0,
            0x48,0xba};
        append64(stub,memory+256);stub.insert(stub.end(),{0x49,0xb8});append64(stub,r.value.size());
        auto tail=jump(base+0x35bb0);stub.insert(stub.end(),tail.begin(),tail.end());
        write(process,memory,stub.data(),stub.size());write(process,memory+256,r.value.c_str(),r.value.size()+1);
        DWORD old=0;require(VirtualProtectEx(process,(void*)memory,4096,PAGE_EXECUTE_READ,&old),"Cannot protect identity stub");
        auto branch=jump(memory);require(VirtualProtectEx(process,(void*)(base+r.rva),branch.size(),PAGE_EXECUTE_READWRITE,&old),"Cannot update identity getter");
        write(process,base+r.rva,branch.data(),branch.size());DWORD ignored=0;
        require(VirtualProtectEx(process,(void*)(base+r.rva),branch.size(),old,&ignored),"Cannot restore getter protection");
    }
    require(FlushInstructionCache(process,nullptr,0),"Cannot flush instruction cache");
}
bool alreadyRunning(const std::wstring& target){
    Handle snap(CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0));PROCESSENTRY32W e={sizeof(e)};
    if(Process32FirstW(snap.h,&e))do{
        if(_wcsicmp(e.szExeFile,L"EVPlayer2.exe"))continue;
        Handle p(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,FALSE,e.th32ProcessID));
        wchar_t b[32768];DWORD n=32768;
        if(p.h&&QueryFullProcessImageNameW(p.h,0,b,&n)&&!_wcsicmp(target.c_str(),b))return true;
    }while(Process32NextW(snap.h,&e));return false;
}
std::vector<wchar_t> childEnvironment(){
    // The original player requests administrator elevation. The device getters
    // below need no elevation: use Windows' per-child RunAsInvoker compatibility
    // setting and keep the host environment/registry unchanged.
    auto block=GetEnvironmentStringsW();require(block!=nullptr,"Cannot read environment");
    std::vector<std::wstring> entries;
    for(auto p=block;*p;p+=wcslen(p)+1)if(_wcsnicmp(p,L"__COMPAT_LAYER=",15))entries.emplace_back(p);
    FreeEnvironmentStringsW(block);entries.emplace_back(L"__COMPAT_LAYER=RunAsInvoker");
    std::sort(entries.begin(),entries.end(),[](const auto&a,const auto&b){return _wcsicmp(a.c_str(),b.c_str())<0;});
    std::vector<wchar_t> out;for(auto&e:entries){out.insert(out.end(),e.begin(),e.end());out.push_back(0);}out.push_back(0);return out;
}
int WINAPI wWinMain(HINSTANCE,HINSTANCE,PWSTR,int){
    PROCESS_INFORMATION pi={};bool debugging=false,launched=false,quiet=false;
    wchar_t self[32768];GetModuleFileNameW(nullptr,self,32768);
    std::ofstream log(directory(self)+L"\\launcher.log",std::ios::app);
    try{
        int argc=0;auto argv=CommandLineToArgvW(GetCommandLineW(),&argc);
        require(argv!=nullptr,"Cannot read arguments");
        std::wstring target=argc>1?argv[1]:L"D:\\StudyApps\\EVPlayer2\\EVPlayer2.exe";
        std::wstring profile=argc>2?argv[2]:directory(self)+L"\\device-profile.ini";
        quiet=argc>3&&!wcscmp(argv[3],L"--quiet");LocalFree(argv);
        require(target.find(L'"')==std::wstring::npos,"Invalid executable path");
        require(sha256(target)==ExpectedHash,"Unsupported executable SHA256; original EVPlayer2 5.0.5 required");
        require(ini(profile,L"Schema")==L"1"&&ini(profile,L"ImageSHA256")==ExpectedHash,"Missing or unsupported device profile");
        auto machine=ascii(ini(profile,L"ComputerName")),mac=ascii(ini(profile,L"MAC")),hardware=ascii(ini(profile,L"HardwareID"));
        require(!machine.empty()&&machine.size()<=63,"Invalid computer name in profile");
        require(mac.size()==17,"Invalid MAC length in profile");
        for(size_t i=0;i<mac.size();i++)require(i%3==2?mac[i]==':':isxdigit((unsigned char)mac[i])!=0,"Invalid MAC in profile");
        require(hardware.size()>=18&&hardware.size()<512&&hardware.find('@')!=std::string::npos,"Invalid hardware identity in profile");
        require(!alreadyRunning(target),"EVPlayer2 is already running. Close it before using this launcher.");
        auto command=L"\""+target+L"\"";STARTUPINFOW si={sizeof(si)};
        auto environment=childEnvironment();
        require(CreateProcessW(target.c_str(),command.data(),nullptr,nullptr,FALSE,DEBUG_ONLY_THIS_PROCESS|CREATE_UNICODE_ENVIRONMENT,environment.data(),directory(target).c_str(),&si,&pi),"Cannot launch EVPlayer2");
        debugging=true;launched=true;uintptr_t base=0;bool armed=false,installed=false;
        auto deadline=GetTickCount64()+30000;
        while(GetTickCount64()<deadline){
            DEBUG_EVENT event={};if(!WaitForDebugEvent(&event,250))continue;
            DWORD disposition=DBG_CONTINUE;bool ready=false;
            if(event.dwDebugEventCode==CREATE_PROCESS_DEBUG_EVENT){
                base=(uintptr_t)event.u.CreateProcessInfo.lpBaseOfImage;
                if(event.u.CreateProcessInfo.hFile)CloseHandle(event.u.CreateProcessInfo.hFile);
                if(event.u.CreateProcessInfo.hThread!=pi.hThread)CloseHandle(event.u.CreateProcessInfo.hThread);
                if(event.u.CreateProcessInfo.hProcess!=pi.hProcess)CloseHandle(event.u.CreateProcessInfo.hProcess);
            }else if(event.dwDebugEventCode==LOAD_DLL_DEBUG_EVENT){
                if(event.u.LoadDll.hFile)CloseHandle(event.u.LoadDll.hFile);
            }else if(event.dwDebugEventCode==CREATE_THREAD_DEBUG_EVENT){
                if(event.u.CreateThread.hThread!=pi.hThread)CloseHandle(event.u.CreateThread.hThread);
            }else if(event.dwDebugEventCode==EXIT_PROCESS_DEBUG_EVENT){
                log<<"Child exit code="<<std::hex<<event.u.ExitProcess.dwExitCode<<" base="<<base<<" armed="<<armed<<std::dec<<std::endl;
                ContinueDebugEvent(event.dwProcessId,event.dwThreadId,DBG_CONTINUE);debugging=false;launched=false;throw std::runtime_error("EVPlayer2 exited before identity setup");
            }else if(event.dwDebugEventCode==EXCEPTION_DEBUG_EVENT){
                auto& ex=event.u.Exception.ExceptionRecord;
                log<<"Exception code="<<std::hex<<ex.ExceptionCode<<" rva="<<((uintptr_t)ex.ExceptionAddress-base)<<" first="<<event.u.Exception.dwFirstChance<<std::dec<<std::endl;
                if(ex.ExceptionCode==EXCEPTION_BREAKPOINT&&!armed){
                    require(base!=0,"Missing image base");
                    // UPX's final jump is already mapped and is outside its output
                    // region. A one-byte software breakpoint survives unpacking.
                    DWORD old=0;unsigned char trap=0xcc;
                    require(VirtualProtectEx(pi.hProcess,(void*)(base+0xa332cc),1,PAGE_EXECUTE_READWRITE,&old),"Cannot set UPX-tail breakpoint");
                    write(pi.hProcess,base+0xa332cc,&trap,1);DWORD ignored=0;
                    require(VirtualProtectEx(pi.hProcess,(void*)(base+0xa332cc),1,old,&ignored),"Cannot restore UPX page protection");
                    FlushInstructionCache(pi.hProcess,(void*)(base+0xa332cc),1);armed=true;
                }else if(ex.ExceptionCode==EXCEPTION_BREAKPOINT&&armed&&(uintptr_t)ex.ExceptionAddress==base+0xa332cc){
                    Handle thread(OpenThread(THREAD_GET_CONTEXT|THREAD_SET_CONTEXT,FALSE,event.dwThreadId));
                    CONTEXT ctx={};ctx.ContextFlags=CONTEXT_CONTROL;
                    require(GetThreadContext(thread.h,&ctx),"Cannot read UPX-tail context");ctx.Rip=base+0xa332cc;
                    require(SetThreadContext(thread.h,&ctx),"Cannot restore UPX-tail context");
                    DWORD old=0;unsigned char original=0xe9;
                    require(VirtualProtectEx(pi.hProcess,(void*)(base+0xa332cc),1,PAGE_EXECUTE_READWRITE,&old),"Cannot restore UPX jump");
                    write(pi.hProcess,base+0xa332cc,&original,1);DWORD ignored=0;
                    require(VirtualProtectEx(pi.hProcess,(void*)(base+0xa332cc),1,old,&ignored),"Cannot restore UPX page protection");
                    const std::vector<unsigned char> common={0x48,0x89,0x5c,0x24,0x10,0x48,0x89,0x74,0x24,0x18,0x48,0x89,0x7c,0x24,0x20,0x55};
                    install(pi.hProcess,base,{
                        {0x17ddc0,machine,{0x40,0x53,0x48,0x81,0xec,0x60,0x08,0,0,0x48,0x8b,0x05,0x58,0x5f,0x81,0}},
                        {0x17ec30,mac,common},{0x17f930,hardware,common}});
                    installed=true;ready=true;
                }else disposition=DBG_EXCEPTION_NOT_HANDLED;
            }
            require(ContinueDebugEvent(event.dwProcessId,event.dwThreadId,disposition),"Cannot continue child process");
            if(ready)break;
        }
        require(installed,"Timed out waiting for UPX unpacked entry point");
        require(DebugActiveProcessStop(pi.dwProcessId),"Cannot detach startup debugger");debugging=false;
        log<<"SUCCESS pid="<<pi.dwProcessId<<" identity getters installed before original entry; debugger detached\n";
        CloseHandle(pi.hThread);CloseHandle(pi.hProcess);return 0;
    }catch(const std::exception&e){
        log<<"ERROR "<<e.what()<<" win32="<<GetLastError()<<std::endl;
        if(launched&&pi.hProcess)TerminateProcess(pi.hProcess,1);
        if(debugging)DebugActiveProcessStop(pi.dwProcessId);
        if(pi.hThread)CloseHandle(pi.hThread);if(pi.hProcess)CloseHandle(pi.hProcess);
        if(!quiet)MessageBoxA(nullptr,e.what(),"EVDeviceLauncher",MB_ICONERROR);return 1;
    }
}
