"""Read-only annotated disassembly of a local EVPlayer2 analysis copy."""
import argparse
import struct
import pefile
import capstone

parser = argparse.ArgumentParser()
parser.add_argument('image')
parser.add_argument('addresses', nargs='+', type=lambda s: int(s, 0))
parser.add_argument('--refs', action='store_true')
args = parser.parse_args()
pe = pefile.PE(args.image)
base = pe.OPTIONAL_HEADER.ImageBase
imports = {i.address: (i.name or b'ordinal').decode() for d in pe.DIRECTORY_ENTRY_IMPORT for i in d.imports}
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
md.detail = True

def resolve(address):
    for _ in range(8):
        b = pe.get_data(address - base, 6)
        if b[:1] != b'\xe9':
            break
        address += 5 + struct.unpack_from('<i', b, 1)[0]
    return address

def label(address):
    if address in imports:
        return imports[address]
    rva = address - base
    if not 0 <= rva < pe.OPTIONAL_HEADER.SizeOfImage:
        return ''
    b = pe.get_data(rva, 120).split(b'\0')[0]
    if len(b) >= 3 and all(32 <= x < 127 for x in b):
        return repr(b.decode())
    return ''

if args.refs:
    targets = {base+a for a in args.addresses}
    for s in pe.sections:
        if not s.Characteristics & 0x20000000:
            continue
        b = s.get_data()
        for j in range(len(b)-4):
            target = base+s.VirtualAddress+j+4+struct.unpack_from('<i', b, j)[0]
            if target in targets:
                print(hex(s.VirtualAddress+j), b[max(0,j-3):j+4].hex(), '->', hex(target-base))
else:
    for rva in args.addresses:
        rva = resolve(base+rva)-base
        end = rva+0x300
        for e in pe.DIRECTORY_ENTRY_EXCEPTION:
            if e.struct.BeginAddress <= rva < e.struct.EndAddress:
                rva, end = e.struct.BeginAddress, e.struct.EndAddress
                break
        print('\nFUNCTION', hex(rva), hex(end))
        for i in md.disasm(pe.get_data(rva,end-rva),base+rva):
            notes=[]
            for o in i.operands:
                if o.type == capstone.x86.X86_OP_MEM and o.mem.base == capstone.x86.X86_REG_RIP:
                    notes.append(label(i.address+i.size+o.mem.disp))
                if o.type == capstone.x86.X86_OP_IMM and i.mnemonic in ('call','jmp'):
                    notes.append('target='+hex(resolve(o.imm)-base))
            if i.mnemonic != 'int3':
                print(hex(i.address-base), i.mnemonic, i.op_str, ' '.join(n for n in notes if n))
