// Find API/Qt handler strings and decompile their callers in the unpacked player.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.mem.MemoryBlock;
import java.util.HashSet;

public class CatalogEndpoints extends GhidraScript {
    public void run() throws Exception {
        String[] terms = getScriptArgs();
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        HashSet<Address> printed = new HashSet<>();
        for (String term : terms) {
            if (term.startsWith("0x")) {
                printCallers(toAddr(term), decompiler, printed);
                continue;
            }
            byte[] pattern = term.getBytes("UTF-8");
            for (MemoryBlock block : currentProgram.getMemory().getBlocks()) {
                if (!block.isInitialized()) continue;
                Address cursor = block.getStart();
                while (cursor.compareTo(block.getEnd()) < 0) {
                    Address hit = currentProgram.getMemory().findBytes(cursor, block.getEnd(), pattern, null, true, monitor);
                    if (hit == null) break;
                    println("TERM " + term + " @ " + hit);
                    printCallers(hit, decompiler, printed);
                    cursor = hit.add(pattern.length);
                }
            }
        }
        decompiler.dispose();
    }

    private void printCallers(Address address, DecompInterface decompiler,
            HashSet<Address> printed) throws Exception {
        for (var ref : getReferencesTo(address)) {
            Function fn = getFunctionContaining(ref.getFromAddress());
            println("XREF " + ref.getFromAddress() + " " + (fn == null ? "no function" : fn.getName()));
            if (fn != null && printed.add(fn.getEntryPoint())) {
                var result = decompiler.decompileFunction(fn, 60, monitor);
                if (result.decompileCompleted()) println(result.getDecompiledFunction().getC());
            }
        }
    }
}
