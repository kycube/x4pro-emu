/* Decompile one function of the analysed stock image (see tools/ghidra_stock.py decompile).
 *
 * Runs against the already-analysed project with -noanalysis, so it costs a JVM start plus a
 * few seconds of decompiler, not a re-analysis.
 *
 * Args: <address> [<outfile>]   -- address may be anywhere inside the function.
 *       Without <outfile> the C goes to the headless log via println().
 */
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

import java.io.PrintWriter;

public class StockDecompile extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            throw new IllegalArgumentException("StockDecompile <address> [<outfile>]");
        }
        long off = Long.parseLong(args[0].replaceFirst("^0[xX]", ""), 16);
        Address addr = toAddr(off);

        Function f = getFunctionContaining(addr);
        String text;
        if (f == null) {
            text = String.format("// no function contains 0x%08x\n", off);
        }
        else {
            DecompInterface di = new DecompInterface();
            di.setOptions(new DecompileOptions());
            if (!di.openProgram(currentProgram)) {
                throw new IllegalStateException("decompiler would not open: " + di.getLastMessage());
            }
            try {
                DecompileResults res = di.decompileFunction(f, 180, monitor);
                if (res.decompileCompleted() && res.getDecompiledFunction() != null) {
                    text = String.format("// %s  @ %s  (%d bytes)\n%s",
                                         f.getName(), f.getEntryPoint(),
                                         f.getBody().getNumAddresses(),
                                         res.getDecompiledFunction().getC());
                }
                else {
                    text = "// decompilation failed: " + res.getErrorMessage() + "\n";
                }
            }
            finally {
                di.dispose();
            }
        }

        if (args.length > 1) {
            try (PrintWriter w = new PrintWriter(args[1])) {
                w.print(text);
            }
        }
        else {
            println(text);
        }
    }
}
