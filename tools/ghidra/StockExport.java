/* Post-analysis exporter for the xteink_app stock image (see tools/ghidra_stock.py).
 *
 * Dumps everything the plain-Python query side of ghidra_stock.py needs, so that after one
 * analysis no further Ghidra run is required for "who calls this", "what points at this
 * string", "how big is this function".
 *
 * Written into <outdir> (tab-separated, '#' header line, addresses as 0x%08x):
 *   functions.txt  addr  size  name
 *   xrefs.txt      from  to    type  fromFuncAddr  fromFuncName
 *   strings.txt    addr  string          (C escapes for control characters)
 *   calls.txt      callerFuncAddr  callerName  callSite  calleeAddr  calleeName
 *
 * Args: <outdir>
 */
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressIterator;
import ghidra.program.model.data.AbstractStringDataType;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.DataIterator;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceManager;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolTable;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;

public class StockExport extends GhidraScript {

    private static String hex(Address a) {
        return String.format("0x%08x", a.getOffset());
    }

    /** One line of text, with tabs/newlines/non-printables escaped so the file stays line-oriented. */
    private static String esc(String s) {
        StringBuilder b = new StringBuilder(s.length() + 8);
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                default:
                    if (c < 0x20 || c == 0x7f) {
                        b.append(String.format("\\x%02x", (int) c));
                    }
                    else {
                        b.append(c);
                    }
            }
        }
        return b.toString();
    }

    private PrintWriter open(File dir, String name, String header) throws Exception {
        PrintWriter w = new PrintWriter(new BufferedWriter(new FileWriter(new File(dir, name))));
        w.println("# " + header);
        return w;
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            throw new IllegalArgumentException("StockExport <outdir>");
        }
        File dir = new File(args[0]);
        dir.mkdirs();

        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        SymbolTable st = currentProgram.getSymbolTable();

        // --- functions.txt -------------------------------------------------------------
        int nFunc = 0;
        try (PrintWriter w = open(dir, "functions.txt", "addr\tsize\tname")) {
            FunctionIterator it = fm.getFunctions(true);
            while (it.hasNext() && !monitor.isCancelled()) {
                Function f = it.next();
                w.println(hex(f.getEntryPoint()) + "\t" + f.getBody().getNumAddresses()
                          + "\t" + esc(f.getName()));
                nFunc++;
            }
        }
        println("StockExport: " + nFunc + " functions");

        // --- xrefs.txt and calls.txt ---------------------------------------------------
        long nRef = 0, nCall = 0;
        try (PrintWriter wx = open(dir, "xrefs.txt", "from\tto\ttype\tfromFuncAddr\tfromFuncName");
             PrintWriter wc = open(dir, "calls.txt",
                                   "callerFuncAddr\tcallerName\tcallSite\tcalleeAddr\tcalleeName")) {
            AddressIterator ai = rm.getReferenceSourceIterator(
                    currentProgram.getMemory().getLoadedAndInitializedAddressSet(), true);
            while (ai.hasNext() && !monitor.isCancelled()) {
                Address from = ai.next();
                Function ff = fm.getFunctionContaining(from);
                String ffa = ff == null ? "-" : hex(ff.getEntryPoint());
                String ffn = ff == null ? "-" : esc(ff.getName());
                for (Reference ref : rm.getReferencesFrom(from)) {
                    Address to = ref.getToAddress();
                    if (to == null) {
                        continue;
                    }
                    String type = ref.getReferenceType().getName();
                    wx.println(hex(from) + "\t" + hex(to) + "\t" + type + "\t" + ffa + "\t" + ffn);
                    nRef++;
                    if (ref.getReferenceType().isCall()) {
                        Function cf = fm.getFunctionAt(to);
                        String cn;
                        if (cf != null) {
                            cn = esc(cf.getName());
                        }
                        else {
                            Symbol s = st.getPrimarySymbol(to);
                            cn = s == null ? "-" : esc(s.getName());
                        }
                        wc.println(ffa + "\t" + ffn + "\t" + hex(from) + "\t" + hex(to) + "\t" + cn);
                        nCall++;
                    }
                }
            }
        }
        println("StockExport: " + nRef + " references, " + nCall + " call edges");

        // --- strings.txt ---------------------------------------------------------------
        long nStr = 0;
        try (PrintWriter w = open(dir, "strings.txt", "addr\tstring")) {
            DataIterator di = currentProgram.getListing().getDefinedData(true);
            while (di.hasNext() && !monitor.isCancelled()) {
                Data d = di.next();
                if (!(d.getDataType() instanceof AbstractStringDataType)) {
                    continue;
                }
                Object v = d.getValue();
                if (!(v instanceof String)) {
                    continue;
                }
                w.println(hex(d.getAddress()) + "\t" + esc((String) v));
                nStr++;
            }
        }
        println("StockExport: " + nStr + " strings");
        println("StockExport: wrote to " + dir.getAbsolutePath());
    }
}
