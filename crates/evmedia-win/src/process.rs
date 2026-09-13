//! Raw access to another process's memory.
//!
//! Nothing here knows about EVPlayer2: it is a read handle, a region walker, and the decoder
//! for the string fields the playback contexts use. The constants below describe the context
//! object layout and belong with the code that reads it.

use anyhow::{bail, Result};
use std::ffi::c_void;
use windows_sys::Win32::{
    Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE},
    System::{
        Diagnostics::{
            Debug::ReadProcessMemory,
            ToolHelp::{
                CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
                TH32CS_SNAPPROCESS,
            },
        },
        Memory::{
            VirtualQueryEx, MEMORY_BASIC_INFORMATION, MEM_COMMIT, MEM_IMAGE, PAGE_EXECUTE_READ,
            PAGE_EXECUTE_READWRITE, PAGE_EXECUTE_WRITECOPY, PAGE_GUARD, PAGE_NOACCESS,
            PAGE_READONLY, PAGE_READWRITE, PAGE_WRITECOPY,
        },
        Threading::{OpenProcess, PROCESS_QUERY_INFORMATION, PROCESS_VM_READ},
    },
};

/// The player's image name, as the process list spells it.
const PLAYER_IMAGE: &str = "EVPlayer2.exe";

/// The pid of the running EVPlayer2, if there is one.
///
/// This is the only step here that is about EVPlayer2 rather than about reading memory, and it
/// exists so no caller has to find the pid by hand. When several copies are running the first
/// one the snapshot yields wins; pass `--pid` to be specific.
pub fn find_player_pid() -> Option<u32> {
    unsafe {
        let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snapshot == INVALID_HANDLE_VALUE {
            return None;
        }
        let mut entry: PROCESSENTRY32W = std::mem::zeroed();
        entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
        let mut found = None;
        if Process32FirstW(snapshot, &mut entry) != 0 {
            loop {
                let end = entry.szExeFile.iter().position(|unit| *unit == 0).unwrap_or(0);
                let name = String::from_utf16_lossy(&entry.szExeFile[..end]);
                if name.eq_ignore_ascii_case(PLAYER_IMAGE) {
                    found = Some(entry.th32ProcessID);
                    break;
                }
                if Process32NextW(snapshot, &mut entry) == 0 {
                    break;
                }
            }
        }
        CloseHandle(snapshot);
        found
    }
}

/// Size of one playback-context object, and the offsets the scan reads out of it.
pub const CONTEXT_SIZE: usize = 0x2a8;
/// Segment index within the lesson (`u32`).
pub const OFF_INDEX: usize = 8;
/// Segment filename.
pub const OFF_FILE: usize = 0x18;
/// The 32-byte key schedule, which holds the segment key once the player has decrypted it.
///
/// This is the cheapest way to learn a key: index, filename and key all come out of one read,
/// with no heap-wide search and nothing to test. What it does not do is outlive the context —
/// a key does, and past that point it is a bare 32-hex string that can only be found by testing
/// candidates against segment bytes. See `keyscan`.
pub const OFF_SCHEDULE: usize = 0x120;
/// What the key-schedule slot holds before the player has decrypted that segment
/// (`0xBAADF00D` little-endian). A slot starting with this, or zeroed, is untouched.
pub const HEAP_FILL: [u8; 4] = [0x0d, 0xf0, 0xad, 0xba];

/// Owns the process handle and closes it on drop.
struct Process(HANDLE);

impl Drop for Process {
    fn drop(&mut self) {
        unsafe { CloseHandle(self.0) };
    }
}

/// A live handle onto the player, opened once and reused for every poll.
pub struct Player {
    process: Process,
    pub(crate) pid: u32,
}

impl Player {
    pub fn open(pid: u32) -> Result<Self> {
        let handle = unsafe { OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, 0, pid) };
        if handle == 0 {
            bail!("cannot read process {pid}; run as the same user or with sufficient rights");
        }
        Ok(Self { process: Process(handle), pid })
    }

    pub fn pid(&self) -> u32 {
        self.pid
    }

    pub(crate) fn read(&self, address: usize, len: usize) -> Option<Vec<u8>> {
        if len == 0 {
            return Some(Vec::new());
        }
        let mut bytes = vec![0u8; len];
        let mut actual = 0usize;
        let ok = unsafe {
            ReadProcessMemory(
                self.process.0,
                address as *const c_void,
                bytes.as_mut_ptr() as *mut c_void,
                len,
                &mut actual,
            )
        };
        (ok != 0 && actual == len).then_some(bytes)
    }

    /// Committed, readable regions, excluding the loaded module images.
    ///
    /// Excluding only `MEM_IMAGE` — rather than keeping just `MEM_PRIVATE` — is load-bearing. The
    /// signed URLs the player builds live in *mapped* memory, so a private-only walk finds none of
    /// them: the index scan still succeeds (those context objects are private) while the lesson
    /// scan silently comes back empty. That asymmetry is easy to miss, because both walks use this
    /// function and only one of them has anything to find where the other looks.
    pub(crate) fn regions(&self) -> Vec<(usize, usize)> {
        let mut output = Vec::new();
        let mut address = 0usize;
        loop {
            let mut info: MEMORY_BASIC_INFORMATION = unsafe { std::mem::zeroed() };
            let length = unsafe {
                VirtualQueryEx(
                    self.process.0,
                    address as *const c_void,
                    &mut info,
                    std::mem::size_of::<MEMORY_BASIC_INFORMATION>(),
                )
            };
            if length == 0 || info.RegionSize == 0 {
                break;
            }
            let base = info.BaseAddress as usize;
            let end = base.saturating_add(info.RegionSize);
            const READABLE: u32 = PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY
                | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY;
            if info.State == MEM_COMMIT
                && info.Type != MEM_IMAGE
                && info.Protect & (PAGE_GUARD | PAGE_NOACCESS) == 0
                && info.Protect & READABLE != 0
            {
                output.push((base, end));
            }
            if end <= address {
                break;
            }
            address = end;
        }
        output
    }

    /// Walk every readable private region, handing each chunk to `visit`.
    pub(crate) fn for_each_chunk<F: FnMut(usize, &[u8])>(&self, chunk_size: usize, mut visit: F) {
        for (base, end) in self.regions() {
            let mut cursor = base;
            while cursor < end {
                let size = (end - cursor).min(chunk_size);
                if let Some(bytes) = self.read(cursor, size) {
                    visit(cursor, &bytes);
                }
                cursor = cursor.saturating_add(size);
            }
        }
    }

    /// Decode the `{inline[16] | pointer, len@+16, cap@+24}` string layout the contexts use.
    pub(crate) fn string_field(&self, object: &[u8], offset: usize) -> Option<String> {
        let number = |at: usize| -> Option<u64> {
            object.get(at..at + 8).map(|value| u64::from_le_bytes(value.try_into().unwrap()))
        };
        let len = number(offset + 16)? as usize;
        let cap = number(offset + 24)? as usize;
        if len > 8192 || cap < len || cap > 1_048_576 {
            return None;
        }
        let bytes = if cap < 16 {
            object.get(offset..offset + len)?.to_vec()
        } else {
            self.read(number(offset)? as usize, len)?
        };
        String::from_utf8(bytes).ok()
    }

    /// Base address of a loaded module, needed to recognise the context vtable pointers.
    pub(crate) fn module_base(&self, name: &str) -> Option<usize> {
        use windows_sys::Win32::System::Diagnostics::ToolHelp::{
            CreateToolhelp32Snapshot, Module32FirstW, Module32NextW, MODULEENTRY32W,
            TH32CS_SNAPMODULE, TH32CS_SNAPMODULE32,
        };
        let snapshot =
            unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, self.pid) };
        if snapshot == -1isize as isize {
            return None;
        }
        let mut entry: MODULEENTRY32W = unsafe { std::mem::zeroed() };
        entry.dwSize = std::mem::size_of::<MODULEENTRY32W>() as u32;
        let mut result = None;
        let mut ok = unsafe { Module32FirstW(snapshot, &mut entry) };
        while ok != 0 {
            let end = entry.szModule.iter().position(|c| *c == 0).unwrap_or(entry.szModule.len());
            let module = String::from_utf16_lossy(&entry.szModule[..end]);
            if module.eq_ignore_ascii_case(name) {
                result = Some(entry.modBaseAddr as usize);
                break;
            }
            ok = unsafe { Module32NextW(snapshot, &mut entry) };
        }
        unsafe { CloseHandle(snapshot) };
        result
    }
}
