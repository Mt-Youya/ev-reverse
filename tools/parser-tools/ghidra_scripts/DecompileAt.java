// Print the decompiled C for each address given as a script argument.
//
// Used to read the two decryption call sites that never fire: the call site alone says where a
// response is decrypted, and only the decompiled body says what the flow does with the plaintext.
//
//   analyzeHeadless <proj> <name> -process <binary> -noanalysis \
//       -postScript DecompileAt.java 0x34390 0x1b480 0x385b0
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class DecompileAt extends GhidraScript {
    @Override
    public void run() throws Exception {
        DecompInterface decompiler = new DecompInterface();
        decompiler.toggleCCode(true);
        decompiler.toggleSyntaxTree(true);
        decompiler.openProgram(currentProgram);

        for (String argument : getScriptArgs()) {
            Address address = toAddr(argument);
            Function function = getFunctionContaining(address);
            if (function == null) {
                println("### no function contains " + argument);
                continue;
            }
            println("### " + function.getName() + " @ " + function.getEntryPoint()
                    + "  (" + function.getBody().getNumAddresses() + " bytes)");
            DecompileResults results = decompiler.decompileFunction(function, 120, monitor);
            if (!results.decompileCompleted()) {
                println("### decompilation failed: " + results.getErrorMessage());
                continue;
            }
            println(results.getDecompiledFunction().getC());
        }
        decompiler.dispose();
    }
}
