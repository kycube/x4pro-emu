/* Pre-analysis layout script for the xteink_app stock image (see tools/ghidra_stock.py).
 *
 * Run by analyzeHeadless as -preScript, right after the raw BinaryLoader import and before
 * the auto-analysis. It throws away the flat "whole file at 0" block the loader made and
 * rebuilds memory the way the ESP-IDF bootloader does:
 *
 *   - one block per image segment, at the segment's load VA, holding the segment payload;
 *   - IROM (0x42xxxxxx) and IRAM (0x4037xxxx) marked executable, everything else data;
 *   - uninitialized blocks for the ESP32-S3 mask ROM, so that the app's calls to
 *     0x4000xxxx have somewhere to land;
 *   - a label for every ROM symbol in the .nm file, so those calls read as names.
 *
 * Args: <app-image-path> [<rom-nm-path>]
 */
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.SourceType;

import java.io.ByteArrayInputStream;
import java.io.BufferedReader;
import java.io.FileReader;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

public class StockLayout extends GhidraScript {

    /** ESP32-S3 mask ROM windows: name, start, length, executable. */
    private static final Object[][] ROM_BLOCKS = {
        { "rom_code", 0x40000000L, 0x60000L, Boolean.TRUE  },  // ROM .text, symbols to 0x40058190
        { "rom_data", 0x3ff00000L, 0x20000L, Boolean.FALSE },  // ROM .rodata / tables
        { "rom_bss",  0x3fcd0000L, 0x30000L, Boolean.FALSE },  // ROM's DRAM state (above the app's)
    };

    private static long u32(byte[] d, int off) {
        return ((long) (d[off] & 0xff))
             | ((long) (d[off + 1] & 0xff) << 8)
             | ((long) (d[off + 2] & 0xff) << 16)
             | ((long) (d[off + 3] & 0xff) << 24);
    }

    /** The ESP-IDF name for a segment, from where it is loaded. */
    private static String segName(long load, int index) {
        if (load >= 0x42000000L && load < 0x44000000L) return "irom";
        if (load >= 0x3c000000L && load < 0x3d000000L) return "drom";
        if (load >= 0x40370000L && load < 0x40400000L) return "iram";
        if (load >= 0x3fc80000L && load < 0x3fd00000L) return "dram" + index;
        if (load >= 0x50000000L && load < 0x50002000L) return "rtc_slow" + index;
        if (load >= 0x600fe000L && load < 0x60100000L) return "rtc_fast" + index;
        return "seg" + index;
    }

    private static boolean isExec(long load) {
        return (load >= 0x42000000L && load < 0x44000000L)
            || (load >= 0x40370000L && load < 0x40400000L);
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            throw new IllegalArgumentException("StockLayout <app-image> [<rom.nm>]");
        }
        byte[] data = Files.readAllBytes(Paths.get(args[0]));
        if ((data[0] & 0xff) != 0xe9) {
            throw new IllegalArgumentException("no ESP image magic 0xe9 at byte 0 of " + args[0]);
        }
        Memory mem = currentProgram.getMemory();

        // 1. drop whatever the raw loader made (one flat block at 0).
        List<MemoryBlock> old = new ArrayList<>();
        for (MemoryBlock b : mem.getBlocks()) {
            old.add(b);
        }
        for (MemoryBlock b : old) {
            mem.removeBlock(b, monitor);
        }

        // 2. one block per segment, at its load address.
        int count = data[1] & 0xff;
        long entry = u32(data, 4);
        int off = 24;
        println("StockLayout: " + count + " segments, entry 0x" + Long.toHexString(entry));
        for (int i = 0; i < count; i++) {
            long load = u32(data, off);
            long len = u32(data, off + 4);
            off += 8;
            int start = off;
            off += (int) len;
            if (len == 0) {
                continue;
            }
            String name = segName(load, i);
            Address addr = toAddr(load);
            MemoryBlock blk = mem.createInitializedBlock(
                    name, addr, new ByteArrayInputStream(data, start, (int) len), len, monitor, false);
            boolean x = isExec(load);
            blk.setPermissions(true, !x, x);
            blk.setComment(String.format("image segment %d, file offset 0x%x, length 0x%x",
                                         i, start, len));
            println(String.format("  %-9s 0x%08x + 0x%06x  %s (file 0x%06x)",
                                  name, load, len, x ? "r-x" : "rw-", start));
        }

        // 3. ROM windows, so calls to 0x4000xxxx resolve instead of dangling.
        for (Object[] rb : ROM_BLOCKS) {
            MemoryBlock blk = mem.createUninitializedBlock(
                    (String) rb[0], toAddr((Long) rb[1]), (Long) rb[2], false);
            boolean x = (Boolean) rb[3];
            blk.setPermissions(true, !x, x);
            blk.setComment("ESP32-S3 mask ROM (no bytes; labels only)");
        }

        // 4. entry point.
        currentProgram.getSymbolTable().addExternalEntryPoint(toAddr(entry));

        // 5. ROM symbols as labels.
        if (args.length > 1) {
            int n = loadNm(args[1]);
            println("StockLayout: " + n + " ROM labels from " + args[1]);
        }
    }

    /**
     * `nm` output: "40000000 T symbol". Only symbols that land inside a block we created are
     * used; the file also holds absolute/undefined junk (0x22222112 &c.) that is skipped.
     */
    private int loadNm(String path) throws Exception {
        Memory mem = currentProgram.getMemory();
        Set<String> seen = new HashSet<>();
        int made = 0;
        try (BufferedReader r = new BufferedReader(new FileReader(path))) {
            String line;
            while ((line = r.readLine()) != null) {
                if (line.length() < 11 || line.charAt(8) != ' ' || line.charAt(10) != ' ') {
                    continue;
                }
                char type = line.charAt(9);
                if ("TtWwDdBbRrVv".indexOf(type) < 0) {
                    continue;
                }
                long addr;
                try {
                    addr = Long.parseLong(line.substring(0, 8), 16);
                }
                catch (NumberFormatException e) {
                    continue;
                }
                String name = line.substring(11).trim();
                if (addr == 0 || name.isEmpty()) {
                    continue;
                }
                Address a = toAddr(addr);
                if (!mem.contains(a) || !seen.add(addr + ":" + name)) {
                    continue;
                }
                createLabel(a, name, true, SourceType.IMPORTED);
                made++;
            }
        }
        return made;
    }
}
