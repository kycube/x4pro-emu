/*
 * Host unit tests for partial-window fidelity (NEXT_PHASE §4):
 *   (1) SSD1677 data-entry modes 0..7,
 *   (2) SSD1677 windows at the edges of the RAM,
 *   (3) UC8279 PTL windows at the edges,
 *   (4) the RED / DTM1 "old plane" resync after a refresh,
 *   (5) writes outside a window leave the visible image alone.
 *   (6) findings: the SSD1677 gate-scan (0x01) byte.
 *
 * Same harness style as test_epd.c (no framework: asserts + counters), with one
 * addition: an expectation that comes from the datasheet or from the firmware
 * driver but that the model does not meet yet is checked with XCHECK(): it is
 * reported and counted separately instead of failing the build, and every such
 * check carries the finding in its comment. XCHECK() that passes prints XPASS
 * (the model caught up) and does not fail either. No XCHECK() is in use right
 * now -- the three findings this file recorded (X-decrement windows, the AM bit
 * of 0x11, the gate-scan mirror byte) are all fixed in epd_core.c and are
 * checked as ordinary assertions; the harness stays for the next gap.
 *
 * Sources used for the expectations, per test:
 *   SSD16xx datasheet semantics for 0x11 / 0x44 / 0x45 / 0x4E / 0x4F / 0x24,
 *   UC81xx datasheet semantics for 0x90 PTL / 0x91 PTIN / 0x92 PTOUT / 0x13,
 *   firmware/freeink-sdk/libs/display/FreeInkDisplay/src/driver/Ssd1677Driver.cpp,
 *   firmware/freeink-sdk/libs/display/FreeInkDisplay/src/driver/Uc8279X4Driver.cpp,
 *   docs/hardware.md ("SSD1677 command stream", "UC8279 as the stock firmware drives it").
 *
 * x4pro-emu, GPL-2.0-or-later.
 */
#include "epd_core.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int fails = 0;
static int xfails = 0;
static int xpasses = 0;
static int checks = 0;

#define MAX_XMSG 32
static char xmsg[MAX_XMSG][256];
static int nxmsg = 0;

static void expect(int ok, int known_gap, int line, const char *msg)
{
    checks++;
    if (ok) {
        if (known_gap) {
            xpasses++;
            printf("XPASS %s:%d: the model now meets this expectation: %s\n", __FILE__, line, msg);
        }
        return;
    }
    if (known_gap) {
        xfails++;
        printf("XFAIL %s:%d: %s\n", __FILE__, line, msg);
        if (nxmsg < MAX_XMSG) {
            snprintf(xmsg[nxmsg], sizeof(xmsg[0]), "%s:%d: %s", __FILE__, line, msg);
            nxmsg++;
        }
        return;
    }
    fails++;
    printf("FAIL %s:%d: %s\n", __FILE__, line, msg);
}

#define CHECK(cond, ...) do { char _m[240]; snprintf(_m, sizeof(_m), __VA_ARGS__); expect(!!(cond), 0, __LINE__, _m); } while (0)
/* Datasheet/driver expectation the model is known to miss: reported, not fatal. */
#define XCHECK(cond, ...) do { char _m[240]; snprintf(_m, sizeof(_m), __VA_ARGS__); expect(!!(cond), 1, __LINE__, _m); } while (0)

/* ---- wire helpers (identical to test_epd.c) ------------------------------- */
static void cmd(EpdCore *c, uint8_t b) { epd_core_set_dc(c, false); epd_core_set_cs(c, true); epd_core_byte(c, b); epd_core_set_cs(c, false); }
static void dat(EpdCore *c, uint8_t b) { epd_core_set_dc(c, true); epd_core_set_cs(c, true); epd_core_byte(c, b); epd_core_set_cs(c, false); }
static void datn(EpdCore *c, const uint8_t *d, size_t n) { epd_core_set_dc(c, true); epd_core_set_cs(c, true); for (size_t i = 0; i < n; i++) epd_core_byte(c, d[i]); epd_core_set_cs(c, false); }

/* ---- SSD1677 helpers ------------------------------------------------------ */
/* 0x44/0x45/0x4E/0x4F carry 16-bit values low byte first (Ssd1677Driver::setRamArea:
 * bus.data(v % 256); bus.data(v / 256)). */
static void w16(EpdCore *c, int v) { dat(c, (uint8_t)(v & 0xFF)); dat(c, (uint8_t)((v >> 8) & 0xFF)); }

/* setRamArea() as the driver issues it: data entry, X window (start, end),
 * Y window (start, end), then both counters at the window's start. */
static void ssd_window(EpdCore *c, int mode, int xs, int xe, int ys, int ye)
{
    cmd(c, 0x11); dat(c, (uint8_t)mode);
    cmd(c, 0x44); w16(c, xs); w16(c, xe);
    cmd(c, 0x45); w16(c, ys); w16(c, ye);
    cmd(c, 0x4E); w16(c, xs);
    cmd(c, 0x4F); w16(c, ys);
}

/* The driver's fast (0x21 0x00, 0x22 0xFC) and full (0x21 0x40, 0x22 0xF7) triggers. */
static void ssd_refresh(EpdCore *c, int full)
{
    cmd(c, 0x21); dat(c, full ? 0x40 : 0x00);
    cmd(c, 0x3C); dat(c, 0xC0);
    cmd(c, 0x22); dat(c, full ? 0xF7 : 0xFC);
    cmd(c, 0x20);
    epd_core_busy_done(c);
}

/* A frame the tests can regenerate byte by byte (RAM order, 100 bytes per row). */
static uint8_t ssd_fb_byte(int i) { return (uint8_t)(0xFF ^ ((i * 31) & 0x7F)); }

static void ssd_write_plane(EpdCore *c, uint8_t ram_cmd, const uint8_t *buf, size_t n)
{
    cmd(c, ram_cmd);
    datn(c, buf, n);
}

/* ---- UC8279 helpers ------------------------------------------------------- */
/* PTL (0x90): x0, x1, y0, y1 as 16-bit values HIGH byte first, then the scan
 * byte (Uc8279X4Driver::displayStart sends 0x01). Window bounds are inclusive;
 * the low three bits of x0 are ignored by the controller (byte-aligned columns)
 * and the driver sends x1 with its low bits set (xEnd | 0x07). */
static void uc_ptl(EpdCore *c, int x0, int x1, int y0, int y1)
{
    cmd(c, 0x90);
    dat(c, (uint8_t)(x0 >> 8)); dat(c, (uint8_t)(x0 & 0xFF));
    dat(c, (uint8_t)(x1 >> 8)); dat(c, (uint8_t)(x1 & 0xFF));
    dat(c, (uint8_t)(y0 >> 8)); dat(c, (uint8_t)(y0 & 0xFF));
    dat(c, (uint8_t)(y1 >> 8)); dat(c, (uint8_t)(y1 & 0xFF));
    dat(c, 0x01);
}

/* Vendor runtime init as Uc8279X4Driver::initController drives it (PSR, TRES
 * 800x600, GSST, PFS, gate scan), then PON. */
static void uc_init(EpdCore *c)
{
    cmd(c, 0x00); dat(c, 0x37); dat(c, 0x4D);
    cmd(c, 0x61); dat(c, 0x03); dat(c, 0x20); dat(c, 0x02); dat(c, 0x58);
    cmd(c, 0x65); dat(c, 0); dat(c, 0); dat(c, 0); dat(c, 0);
    cmd(c, 0x03); dat(c, 0x20);
    cmd(c, 0xE1); dat(c, 0x02);
    cmd(c, 0x04); epd_core_busy_done(c);          /* PON */
}

/* The refresh trigger: CDI, CCSET, TSSET, PSR rewrite, DRF (fast = the partial
 * values 0xD7 / 0x5A, full = 0x97 / 0x1E). */
static void uc_refresh(EpdCore *c, int fast)
{
    cmd(c, 0x50); dat(c, fast ? 0xD7 : 0x97);
    cmd(c, 0xE0); dat(c, 0x02);
    cmd(c, 0xE5); dat(c, fast ? 0x5A : 0x1E);
    cmd(c, 0x00); dat(c, 0x17); dat(c, 0x4D);
    cmd(c, 0x12);
    epd_core_busy_done(c);
}

static void uc_fill(EpdCore *c, uint8_t ram_cmd, uint8_t value, int nbytes)
{
    uint8_t row[EPD_WB];
    memset(row, value, sizeof(row));
    cmd(c, ram_cmd);
    for (int i = 0; i < nbytes; i += EPD_WB) {
        int n = nbytes - i < EPD_WB ? nbytes - i : EPD_WB;
        datn(c, row, (size_t)n);
    }
}

static uint8_t uc_fb_byte(int y, int xb) { return (uint8_t)(0xFF ^ (((y * 7) + (xb * 13)) & 0x3F)); }

/* The 480 visible rows only: what the stock sends as DTM1 inside a PTL window of
 * gates 120..599 (docs/hardware.md: PTIN, PTL full window, DTM1 48,000 bytes). */
static void uc_stream_fb_rows(EpdCore *c, uint8_t ram_cmd)
{
    uint8_t row[EPD_WB];
    cmd(c, ram_cmd);
    for (int y = 0; y < EPD_H; y++) {
        for (int i = 0; i < EPD_WB; i++) row[i] = uc_fb_byte(y, i);
        datn(c, row, EPD_WB);
    }
}

/* Uc8279X4Driver::streamPlane: gateOffset (120) white rows, the 480 visible rows
 * in forward order, then nothing (gateOffset + height == tresH = 600). */
static void uc_stream_fb(EpdCore *c, uint8_t ram_cmd, int invert)
{
    uint8_t row[EPD_WB];
    cmd(c, ram_cmd);
    memset(row, 0xFF, sizeof(row));
    for (int y = 0; y < 120; y++) datn(c, row, EPD_WB);
    for (int y = 0; y < EPD_H; y++) {
        for (int i = 0; i < EPD_WB; i++) {
            uint8_t b = uc_fb_byte(y, i);
            row[i] = invert ? (uint8_t)~b : b;
        }
        datn(c, row, EPD_WB);
    }
}

/* ---- small utilities ------------------------------------------------------ */
static int count_ne(const uint8_t *p, size_t n, uint8_t v)
{
    int k = 0;
    for (size_t i = 0; i < n; i++) if (p[i] != v) k++;
    return k;
}

static int first_diff(const uint8_t *a, const uint8_t *b, size_t n)
{
    for (size_t i = 0; i < n; i++) if (a[i] != b[i]) return (int)i;
    return -1;
}

static int pixel_of(uint8_t plane_byte, int x) { return ((plane_byte >> (7 - (x & 7))) & 1) ? 255 : 0; }

static uint8_t snap[EPD_W * EPD_H];
static uint8_t shadow[EPD_UC_PLANE_BYTES];
static uint8_t fbbuf[EPD_PLANE_BYTES];

/* ==========================================================================
 * (1) SSD1677 data-entry modes 0..7
 *
 * Datasheet (SSD16xx 0x11, 8 modes): bit0 = X direction (1 increment,
 * 0 decrement), bit1 = Y direction (1 increment, 0 decrement), bit2 = AM
 * (0: the address counter advances in the X direction first, 1: in Y first).
 * 0x44/0x45 give the window's *start* (the counter origin, which 0x4E/0x4F are
 * set to) and its *end* (the terminus) in the direction the mode selects, so on
 * a decrementing axis the start is the higher address -- which is exactly what
 * Ssd1677Driver::setRamArea does for mirrorX: dataEntry 0x00, xStart = x+w-1,
 * xEnd = x, and it always writes the Y pair end-first the same way (0x45 gets
 * y+h-1 then y with the default Y-decrement mode).
 * A RAM write (0x24) stores the byte at the counter and then advances it: along
 * the fast axis until its terminus, where the fast axis returns to its start and
 * the slow axis steps one; at the slow axis' terminus it wraps to its start.
 * ========================================================================== */
typedef struct { int xs, xe, ys, ye, mode, x, y; } Walk;

static void walk_next(Walk *w)
{
    int x_inc = w->mode & 1, y_inc = w->mode & 2, am = w->mode & 4;
    int at_x_end = (w->x / 8) == (w->xe / 8);     /* the controller steps whole bytes */
    int at_y_end = w->y == w->ye;
    if (!am) {
        if (!at_x_end) { w->x = x_inc ? w->x + 8 : w->x - 8; return; }
        w->x = w->xs;
        w->y = at_y_end ? w->ys : (y_inc ? w->y + 1 : w->y - 1);
    } else {
        if (!at_y_end) { w->y = y_inc ? w->y + 1 : w->y - 1; return; }
        w->y = w->ys;
        w->x = at_x_end ? w->xs : (x_inc ? w->x + 8 : w->x - 8);
    }
}

static void test_data_entry_modes(void)
{
    EpdCore *c = malloc(sizeof(*c));
    uint8_t seq[14];
    for (int i = 0; i < 14; i++) seq[i] = (uint8_t)(0x01 + i);

    for (int mode = 0; mode < 8; mode++) {
        int x_inc = mode & 1, y_inc = mode & 2;
        /* 4 byte columns (pixels 16..47) x 3 rows (100..102), start = origin. */
        int xs = x_inc ? 16 : 47, xe = x_inc ? 47 : 16;
        int ys = y_inc ? 100 : 102, ye = y_inc ? 102 : 100;

        epd_core_init(c, EPD_SSD1677);
        ssd_window(c, mode, xs, xe, ys, ye);
        CHECK((int)c->x_cnt == xs && (int)c->y_cnt == ys, "mode %d: counters %u,%u after 0x4E/0x4F (want %d,%d)",
              mode, c->x_cnt, c->y_cnt, xs, ys);
        CHECK((int)c->data_entry == mode, "mode %d: data_entry %u", mode, c->data_entry);

        ssd_write_plane(c, 0x24, seq, sizeof(seq));

        /* Expected RAM: 12 cells, then two more bytes wrapping onto the first two. */
        memset(shadow, 0xFF, EPD_PLANE_BYTES);
        Walk w = { xs, xe, ys, ye, mode, xs, ys };
        for (int i = 0; i < 14; i++) {
            shadow[w.y * EPD_WB + w.x / 8] = seq[i];
            walk_next(&w);
        }
        int d = first_diff(shadow, c->plane0, EPD_PLANE_BYTES);
        {
            char msg[240];
            snprintf(msg, sizeof(msg),
                     "data-entry mode %d (x %s, y %s, AM=%d): plane0 differs at row %d byte %d "
                     "(want %02x, got %02x)", mode, x_inc ? "inc" : "dec", y_inc ? "inc" : "dec",
                     (mode & 4) ? 1 : 0, d < 0 ? -1 : d / EPD_WB, d < 0 ? -1 : d % EPD_WB,
                     d < 0 ? 0 : shadow[d], d < 0 ? 0 : c->plane0[d]);
            expect(d < 0, 0, __LINE__, msg);
        }

        /* The glass: RAM row 479 is the top of the panel, so the window's first
         * cell must appear at glass row 479 - ys. (The first byte written always
         * lands at the counter origin, whatever the mode.) The cell is the whole
         * 8-pixel byte that holds xs -- pixels xs & ~7 .. | 7 -- which for an
         * X-decrement window (xs = 47) is the byte to the *left* of xs. */
        ssd_refresh(c, 0);
        CHECK(c->refresh_count == 1 && !c->image_gray_approx, "mode %d: one plain refresh", mode);
        uint8_t want = shadow[ys * EPD_WB + xs / 8];
        int x0 = xs & ~7;
        int bad = -1;
        for (int b = 0; b < 8; b++) {
            if (c->image[(EPD_H - 1 - ys) * EPD_W + x0 + b] != pixel_of(want, x0 + b)) { bad = b; break; }
        }
        {
            char msg[240];
            snprintf(msg, sizeof(msg), "mode %d: glass row %d, pixel %d of byte %02x wrong",
                     mode, EPD_H - 1 - ys, bad, want);
            expect(bad < 0, 0, __LINE__, msg);
        }
        CHECK(c->unknown_cmds == 0, "mode %d: unknown commands %llu", mode, (unsigned long long)c->unknown_cmds);
    }
    free(c);
}

/* ==========================================================================
 * (2) SSD1677 windows at the edges
 * Datasheet: 0x44/0x45 are inclusive; X is given in pixels with the low three
 * bits ignored (the controller addresses whole 8-pixel bytes); a write past the
 * window's last cell wraps to the window's start in the direction the mode gives.
 * The driver only ever uses mode 0x01 (X increment, Y decrement) on this board
 * (Ssd1677Driver::setRamArea, mirrorX false in the X4-class NO_FLIP profile).
 * ========================================================================== */
static void test_ssd_edge_windows(void)
{
    EpdCore *c = malloc(sizeof(*c));

    /* (2a) The rightmost byte column: x 792..799 (the last addressable pixel),
     * two rows, Y decrementing as the driver drives it. */
    epd_core_init(c, EPD_SSD1677);
    ssd_window(c, 0x01, 792, 799, 301, 300);
    { uint8_t b[3] = { 0xAA, 0x55, 0x0F }; ssd_write_plane(c, 0x24, b, 3); }
    CHECK(c->plane0[300 * EPD_WB + 99] == 0x55, "2a: row 300 last byte %02x", c->plane0[300 * EPD_WB + 99]);
    /* the third byte wraps back onto the window's first cell */
    CHECK(c->plane0[301 * EPD_WB + 99] == 0x0F, "2a: row 301 last byte %02x (wrap)", c->plane0[301 * EPD_WB + 99]);
    CHECK(count_ne(c->plane0, EPD_PLANE_BYTES, 0xFF) == 2, "2a: %d bytes touched (want 2)",
          count_ne(c->plane0, EPD_PLANE_BYTES, 0xFF));

    /* (2b) x starting at 0, full width, on the last RAM row (479 = the top row of
     * the glass): 100 bytes fill the row, the 101st wraps back to x = 0. */
    epd_core_init(c, EPD_SSD1677);
    ssd_window(c, 0x01, 0, 799, 479, 479);
    {
        uint8_t row[101];
        memset(row, 0x00, 100);
        row[100] = 0xFF;
        ssd_write_plane(c, 0x24, row, sizeof(row));
    }
    CHECK(c->plane0[479 * EPD_WB + 0] == 0xFF, "2b: wrapped byte %02x", c->plane0[479 * EPD_WB + 0]);
    CHECK(c->plane0[479 * EPD_WB + 99] == 0x00, "2b: last byte of the row");
    CHECK(count_ne(c->plane0, EPD_PLANE_BYTES, 0xFF) == 99, "2b: %d bytes black (want 99)",
          count_ne(c->plane0, EPD_PLANE_BYTES, 0xFF));
    ssd_refresh(c, 1);
    CHECK(c->image[0] == 255 && c->image[7] == 255, "2b: glass row 0 starts white (the wrapped byte)");
    CHECK(c->image[8] == 0 && c->image[799] == 0, "2b: rest of glass row 0 black");
    CHECK(c->image[EPD_W] == 255, "2b: glass row 1 untouched");

    /* (2c) A one-byte-wide window, four rows, Y decrementing: one byte per row,
     * the fifth byte wraps to the window's first row. */
    epd_core_init(c, EPD_SSD1677);
    ssd_window(c, 0x01, 8, 15, 13, 10);
    { uint8_t b[5] = { 0x11, 0x22, 0x33, 0x44, 0x55 }; ssd_write_plane(c, 0x24, b, 5); }
    CHECK(c->plane0[13 * EPD_WB + 1] == 0x55, "2c: row 13 (wrapped) %02x", c->plane0[13 * EPD_WB + 1]);
    CHECK(c->plane0[12 * EPD_WB + 1] == 0x22 && c->plane0[11 * EPD_WB + 1] == 0x33 &&
          c->plane0[10 * EPD_WB + 1] == 0x44, "2c: rows 12..10");
    CHECK(count_ne(c->plane0, EPD_PLANE_BYTES, 0xFF) == 4, "2c: %d bytes touched (want 4)",
          count_ne(c->plane0, EPD_PLANE_BYTES, 0xFF));

    /* (2d) Counters set inside the window but not at its start: the write starts
     * there and still wraps at the window's edges (datasheet: 0x4E/0x4F set the
     * counter anywhere inside the window). Window x 16..47, rows 102..100. */
    epd_core_init(c, EPD_SSD1677);
    ssd_window(c, 0x01, 16, 47, 102, 100);
    cmd(c, 0x4E); w16(c, 40);          /* third byte column */
    cmd(c, 0x4F); w16(c, 101);         /* middle row */
    { uint8_t b[4] = { 0xA0, 0xA1, 0xA2, 0xA3 }; ssd_write_plane(c, 0x24, b, 4); }
    CHECK(c->plane0[101 * EPD_WB + 5] == 0xA0, "2d: first byte at the counter");
    CHECK(c->plane0[100 * EPD_WB + 2] == 0xA1 && c->plane0[100 * EPD_WB + 3] == 0xA2 &&
          c->plane0[100 * EPD_WB + 4] == 0xA3, "2d: continues on the next row from the window's x start");
    CHECK(count_ne(c->plane0, EPD_PLANE_BYTES, 0xFF) == 4, "2d: %d bytes touched (want 4)",
          count_ne(c->plane0, EPD_PLANE_BYTES, 0xFF));

    /* (2e) Counters set outside the window. The datasheet does not define this
     * (0x4E/0x4F are specified to point inside the window), so the only thing
     * asserted is what every reading agrees on: the bytes stay inside the RAM,
     * they do not land in the window that is 98 rows away, and no more cells are
     * written than bytes sent. */
    epd_core_init(c, EPD_SSD1677);
    ssd_window(c, 0x01, 16, 47, 102, 100);
    cmd(c, 0x4E); w16(c, 200);
    cmd(c, 0x4F); w16(c, 200);
    { uint8_t b[4] = { 0xB0, 0xB1, 0xB2, 0xB3 }; ssd_write_plane(c, 0x24, b, 4); }
    {
        int touched = count_ne(c->plane0, EPD_PLANE_BYTES, 0xFF);
        CHECK(touched >= 1 && touched <= 4, "2e: %d cells written for 4 bytes", touched);
        int in_window = 0;
        for (int y = 100; y <= 102; y++)
            for (int xb = 2; xb <= 5; xb++)
                if (c->plane0[y * EPD_WB + xb] != 0xFF) in_window++;
        CHECK(in_window == 0, "2e: %d bytes landed in a window 98 rows away", in_window);
    }

    /* (2f) Wrap in Y-increment mode (0x03): a two-byte, two-row window, five
     * bytes; the fifth returns to the window's first cell. */
    epd_core_init(c, EPD_SSD1677);
    ssd_window(c, 0x03, 0, 15, 200, 201);
    { uint8_t b[5] = { 0xC0, 0xC1, 0xC2, 0xC3, 0xC4 }; ssd_write_plane(c, 0x24, b, 5); }
    CHECK(c->plane0[200 * EPD_WB + 0] == 0xC4 && c->plane0[200 * EPD_WB + 1] == 0xC1,
          "2f: row 200 %02x %02x", c->plane0[200 * EPD_WB + 0], c->plane0[200 * EPD_WB + 1]);
    CHECK(c->plane0[201 * EPD_WB + 0] == 0xC2 && c->plane0[201 * EPD_WB + 1] == 0xC3, "2f: row 201");
    CHECK(count_ne(c->plane0, EPD_PLANE_BYTES, 0xFF) == 4, "2f: %d bytes touched (want 4)",
          count_ne(c->plane0, EPD_PLANE_BYTES, 0xFF));
    free(c);
}

/* ==========================================================================
 * (3) UC8279 PTL windows at the edges
 * Datasheet: PTL (0x90) bounds are inclusive; the low three bits of the X start
 * are ignored and the X end names the byte that contains it, so the window is a
 * whole number of bytes; Y is in gate rows of the addressed scan (TRES 800x600
 * here, of which 120..599 are visible -- Uc8279X4Driver cfg.gateOffset = 120).
 * Inside PTIN, DTM1/DTM2 write only the window, row by row, and the stock sends
 * exactly the window's byte count (docs/hardware.md: a clock repaint = 112 bytes).
 * ========================================================================== */
static void test_uc_edge_windows(void)
{
    EpdCore *c = malloc(sizeof(*c));

    /* (3a) Full-width window on the first ten visible rows, exactly 10 x 100
     * bytes of DTM2 -- the stock's own window shape (90 00 00 03 1F ...). */
    epd_core_init(c, EPD_UC8279);
    uc_init(c);
    cmd(c, 0x91); uc_ptl(c, 0, 799, 120, 129);
    uc_fill(c, 0x13, 0x00, 10 * EPD_WB);
    cmd(c, 0x92);
    CHECK(c->plane1[120 * EPD_WB] == 0x00 && c->plane1[129 * EPD_WB + 99] == 0x00, "3a: window filled");
    CHECK(count_ne(c->plane1, EPD_UC_PLANE_BYTES, 0xFF) == 10 * EPD_WB, "3a: %d bytes touched (want %d)",
          count_ne(c->plane1, EPD_UC_PLANE_BYTES, 0xFF), 10 * EPD_WB);
    CHECK(c->plane1[119 * EPD_WB] == 0xFF && c->plane1[130 * EPD_WB] == 0xFF, "3a: neighbour rows untouched");
    cmd(c, 0x91); uc_refresh(c, 1); cmd(c, 0x92);
    CHECK(c->last_mode == EPD_MODE_FAST, "3a: partial refresh mode %s", epd_mode_name(c->last_mode));
    CHECK(c->image[0] == 0 && c->image[9 * EPD_W + 799] == 0, "3a: glass rows 0..9 black");
    CHECK(c->image[10 * EPD_W] == 255, "3a: glass row 10 white");

    /* (3b) An X start that is not a multiple of 8: the controller ignores the low
     * three bits, so x0 = 100 means byte 12 (pixels 96..103) and x1 = 131 the byte
     * that holds pixel 131 (byte 16, pixels 128..135): five bytes per row. */
    epd_core_init(c, EPD_UC8279);
    uc_init(c);
    cmd(c, 0x91); uc_ptl(c, 100, 131, 200, 201);
    uc_fill(c, 0x13, 0x3C, 2 * 5);
    cmd(c, 0x92);
    CHECK(count_ne(c->plane1, EPD_UC_PLANE_BYTES, 0xFF) == 10, "3b: %d bytes touched (want 10)",
          count_ne(c->plane1, EPD_UC_PLANE_BYTES, 0xFF));
    CHECK(c->plane1[200 * EPD_WB + 12] == 0x3C && c->plane1[200 * EPD_WB + 16] == 0x3C &&
          c->plane1[201 * EPD_WB + 12] == 0x3C && c->plane1[201 * EPD_WB + 16] == 0x3C,
          "3b: bytes 12..16 of rows 200, 201");
    CHECK(c->plane1[200 * EPD_WB + 11] == 0xFF && c->plane1[200 * EPD_WB + 17] == 0xFF,
          "3b: columns outside the byte-aligned window untouched");

    /* (3c) A one-row window on the first visible gate (120 -> glass row 0). */
    epd_core_init(c, EPD_UC8279);
    uc_init(c);
    cmd(c, 0x91); uc_ptl(c, 0, 799, 120, 120);
    uc_fill(c, 0x13, 0x00, EPD_WB);
    cmd(c, 0x92);
    cmd(c, 0x91); uc_refresh(c, 1); cmd(c, 0x92);
    CHECK(c->image[0] == 0 && c->image[799] == 0, "3c: glass row 0 black");
    CHECK(c->image[EPD_W] == 255, "3c: glass row 1 white");

    /* (3d) A one-row window on the last gate (599 -> glass row 479). */
    epd_core_init(c, EPD_UC8279);
    uc_init(c);
    cmd(c, 0x91); uc_ptl(c, 0, 799, 599, 599);
    uc_fill(c, 0x13, 0x00, EPD_WB);
    cmd(c, 0x92);
    cmd(c, 0x91); uc_refresh(c, 1); cmd(c, 0x92);
    CHECK(c->image[479 * EPD_W] == 0 && c->image[479 * EPD_W + 799] == 0, "3d: glass row 479 black");
    CHECK(c->image[478 * EPD_W] == 255, "3d: glass row 478 white");
    CHECK(count_ne(c->plane1, EPD_UC_PLANE_BYTES, 0xFF) == EPD_WB, "3d: only one row written");

    /* (3e) A window entirely inside the invisible 0..119 gate band: the RAM takes
     * it, the glass shows nothing (the visible scan starts at gate 120). */
    epd_core_init(c, EPD_UC8279);
    uc_init(c);
    cmd(c, 0x91); uc_ptl(c, 0, 799, 100, 119);
    uc_fill(c, 0x13, 0x00, 20 * EPD_WB);
    cmd(c, 0x92);
    CHECK(count_ne(c->plane1, EPD_UC_PLANE_BYTES, 0xFF) == 20 * EPD_WB, "3e: RAM took the window");
    cmd(c, 0x91); uc_refresh(c, 1); cmd(c, 0x92);
    CHECK(count_ne(c->image, sizeof(c->image), 255) == 0, "3e: %d visible pixels changed by an invisible window",
          count_ne(c->image, sizeof(c->image), 255));

    /* (3f) A second PTL after PTOUT: two windows in one paint, each written with
     * exactly its own byte count, and one byte too many in the second (the extra
     * byte must not reach any pixel outside the window). */
    epd_core_init(c, EPD_UC8279);
    uc_init(c);
    cmd(c, 0x91); uc_ptl(c, 0, 15, 200, 201); uc_fill(c, 0x13, 0x11, 2 * 2); cmd(c, 0x92);
    cmd(c, 0x91); uc_ptl(c, 400, 431, 300, 300); uc_fill(c, 0x13, 0x22, 4 + 1); cmd(c, 0x92);
    CHECK(c->plane1[200 * EPD_WB + 0] == 0x11 && c->plane1[201 * EPD_WB + 1] == 0x11, "3f: first window");
    CHECK(c->plane1[300 * EPD_WB + 50] == 0x22 && c->plane1[300 * EPD_WB + 53] == 0x22, "3f: second window");
    CHECK(c->plane1[300 * EPD_WB + 54] == 0xFF && c->plane1[301 * EPD_WB + 50] == 0xFF,
          "3f: the extra byte stayed inside the window");
    CHECK(count_ne(c->plane1, EPD_UC_PLANE_BYTES, 0xFF) == 4 + 4, "3f: %d bytes touched (want 8)",
          count_ne(c->plane1, EPD_UC_PLANE_BYTES, 0xFF));

    /* (3g) PTOUT restores full-plane addressing: a DTM2 stream after it fills the
     * plane from its first byte, not the last window. */
    cmd(c, 0x92);
    uc_fill(c, 0x13, 0x7E, 3 * EPD_WB);
    CHECK(c->plane1[0] == 0x7E && c->plane1[2 * EPD_WB + 99] == 0x7E, "3g: full-plane write after PTOUT");
    CHECK(c->plane1[3 * EPD_WB] == 0xFF, "3g: stopped after three rows");
    free(c);
}

/* ==========================================================================
 * (4) The "old plane" resync after a refresh
 * Neither firmware relies on the controller copying anything on its own:
 *   - Uc8279X4Driver::displayFinish() re-streams DTM1 with the frame it has just
 *     displayed ("Sync the OLD plane (0x10) with the just-displayed frame so the
 *     NEXT partial diffs against it"), and the stock does the same right after
 *     every DRF (docs/hardware.md: PTOUT, PTIN, PTL, DTM1 48,000 bytes).
 *   - Ssd1677Driver::displayImpl() / displayWindow() rewrite BOTH 0x24 and 0x26
 *     after the activation when they hold no separate previous frame ("Stock X4
 *     syncs both controller RAM planes after activation").
 * So the expectation checked here is the one the drivers depend on: after their
 * post-refresh sequence the old plane equals the new one, and the resync writes
 * themselves neither refresh the panel nor disturb the image.
 * ========================================================================== */
static void test_old_plane_resync(void)
{
    EpdCore *c = malloc(sizeof(*c));

    /* (4a) UC full refresh: DTM2 = frame, DTM1 = white seed, DRF, DTM1 = frame. */
    epd_core_init(c, EPD_UC8279);
    uc_init(c);
    uc_stream_fb(c, 0x13, 0);
    uc_fill(c, 0x10, 0xFF, EPD_UC_PLANE_BYTES);
    uc_refresh(c, 0);
    CHECK(c->refresh_count == 1 && c->last_mode == EPD_MODE_FULL, "4a: one full refresh (%s)",
          epd_mode_name(c->last_mode));
    uc_stream_fb(c, 0x10, 0);                   /* displayFinish(): old plane <- fb */
    CHECK(memcmp(c->plane0, c->plane1, EPD_UC_PLANE_BYTES) == 0, "4a: old plane != new plane after the resync");
    CHECK(c->refresh_count == 1, "4a: the resync must not refresh (count %llu)", (unsigned long long)c->refresh_count);
    {
        int bad = 0;
        for (int r = 0; r < EPD_H && !bad; r++)
            for (int x = 0; x < EPD_W; x++)
                if (c->image[r * EPD_W + x] != pixel_of(uc_fb_byte(r, x / 8), x)) { bad = 1; break; }
        CHECK(!bad, "4a: the glass does not show the streamed frame");
    }

    /* (4b) UC partial: old plane pre-sent in full, then a window in DTM2, DRF,
     * PTOUT, and the old plane resynced with what is now on the glass. */
    memcpy(snap, c->image, sizeof(snap));
    cmd(c, 0x91); uc_ptl(c, 0, 799, 120, 599); uc_stream_fb_rows(c, 0x10); cmd(c, 0x92);
    CHECK(memcmp(c->plane0, c->plane1, EPD_UC_PLANE_BYTES) == 0,
          "4b: the pre-sent old plane (48,000 bytes into the full visible window) differs from the new one");
    cmd(c, 0x91); uc_ptl(c, 200, 231, 300, 303);
    uc_fill(c, 0x13, 0x00, 4 * 4);
    cmd(c, 0x92);
    cmd(c, 0x91); uc_refresh(c, 1); cmd(c, 0x92);
    CHECK(c->last_mode == EPD_MODE_FAST, "4b: partial refresh (%s)", epd_mode_name(c->last_mode));
    CHECK(c->image[(300 - 120) * EPD_W + 200] == 0, "4b: the window is black on the glass");
    CHECK(c->image[(299 - 120) * EPD_W + 200] == snap[(299 - 120) * EPD_W + 200],
          "4b: the row above the window is unchanged");
    cmd(c, 0x91); uc_ptl(c, 200, 231, 300, 303); uc_fill(c, 0x10, 0x00, 4 * 4); cmd(c, 0x92);
    CHECK(memcmp(c->plane0, c->plane1, EPD_UC_PLANE_BYTES) == 0,
          "4b: old plane != new plane after the windowed resync");

    /* (4c) SSD1677 full frame, single-buffer path: 0x24 = frame, 0x26 = previous,
     * activation, then both planes rewritten with the frame. */
    epd_core_init(c, EPD_SSD1677);
    for (int i = 0; i < EPD_PLANE_BYTES; i++) fbbuf[i] = ssd_fb_byte(i);
    ssd_window(c, 0x01, 0, 799, 479, 0);
    ssd_write_plane(c, 0x24, fbbuf, EPD_PLANE_BYTES);
    ssd_refresh(c, 0);
    memcpy(snap, c->image, sizeof(snap));
    ssd_window(c, 0x01, 0, 799, 479, 0);
    ssd_write_plane(c, 0x24, fbbuf, EPD_PLANE_BYTES);
    ssd_write_plane(c, 0x26, fbbuf, EPD_PLANE_BYTES);
    CHECK(memcmp(c->plane0, c->plane1, EPD_PLANE_BYTES) == 0, "4c: RED != BW after the driver's resync");
    CHECK(c->refresh_count == 1, "4c: the resync must not refresh");
    CHECK(memcmp(snap, c->image, sizeof(snap)) == 0, "4c: the resync changed the glass before the next 0x20");
    /* The frame reached RAM in the order the driver streams it: byte i of the
     * stream is row 479 - i/100, column i%100 (0x45 starts at row 479, Y down). */
    {
        int bad = -1;
        for (int i = 0; i < EPD_PLANE_BYTES && bad < 0; i++) {
            int row = 479 - i / EPD_WB, col = i % EPD_WB;
            if (c->plane0[row * EPD_WB + col] != ssd_fb_byte(i)) bad = i;
        }
        CHECK(bad < 0, "4c: stream byte %d landed wrong", bad);
    }

    /* (4d) SSD1677 windowed update with no previous buffer (displayWindow,
     * prev == nullptr): after the activation the window is rewritten into BW and
     * RED, and nothing outside it is touched. Glass rows 100..103, x 32..63
     * -> RAM rows 376..379 (y = _h - y - h = 376). */
    epd_core_init(c, EPD_SSD1677);
    {
        uint8_t win[16];
        for (int i = 0; i < 16; i++) win[i] = (uint8_t)(0xA0 | i);   /* never 0xFF */
        ssd_window(c, 0x01, 32, 63, 379, 376);
        ssd_write_plane(c, 0x24, win, sizeof(win));
        ssd_refresh(c, 0);
        ssd_window(c, 0x01, 32, 63, 379, 376);
        ssd_write_plane(c, 0x24, win, sizeof(win));
        ssd_write_plane(c, 0x26, win, sizeof(win));
        CHECK(count_ne(c->plane1, EPD_PLANE_BYTES, 0xFF) == 16, "4d: RED holds %d bytes (want 16)",
              count_ne(c->plane1, EPD_PLANE_BYTES, 0xFF));
        CHECK(memcmp(c->plane0, c->plane1, EPD_PLANE_BYTES) == 0, "4d: RED != BW inside the window");
        /* Stream order inside the window: row 379 columns 4..7 first (X up,
         * Y down), so win[0] is its top-left cell and win[15] its last row. */
        CHECK(c->plane1[379 * EPD_WB + 4] == 0xA0 && c->plane1[376 * EPD_WB + 7] == 0xAF,
              "4d: window corners %02x %02x", c->plane1[379 * EPD_WB + 4], c->plane1[376 * EPD_WB + 7]);
        CHECK(c->plane1[379 * EPD_WB + 8] == 0xFF && c->plane1[380 * EPD_WB + 4] == 0xFF,
              "4d: bytes next to the window untouched");
    }
    free(c);
}

/* ==========================================================================
 * (5) Writes outside a window leave the visible image alone
 * Datasheet, both families: RAM writes are confined to the window (SSD1677 wraps
 * inside it, UC81xx inside PTIN writes only the PTL rectangle), so a repaint of
 * a small window can never disturb the rest of the glass. This is what makes the
 * stock's 112-byte clock repaint safe.
 * ========================================================================== */
static void test_writes_outside_window(void)
{
    EpdCore *c = malloc(sizeof(*c));

    /* (5a) SSD1677: paint a frame, then send far more bytes than a small window
     * holds; only the window's glass rectangle may change. */
    epd_core_init(c, EPD_SSD1677);
    for (int i = 0; i < EPD_PLANE_BYTES; i++) fbbuf[i] = ssd_fb_byte(i);
    ssd_window(c, 0x01, 0, 799, 479, 0);
    ssd_write_plane(c, 0x24, fbbuf, EPD_PLANE_BYTES);
    ssd_write_plane(c, 0x26, fbbuf, EPD_PLANE_BYTES);
    ssd_refresh(c, 1);
    memcpy(snap, c->image, sizeof(snap));
    /* window: RAM rows 242..240 (glass rows 237..239), x 400..431 */
    ssd_window(c, 0x01, 400, 431, 242, 240);
    { uint8_t blob[200]; memset(blob, 0x00, sizeof(blob)); ssd_write_plane(c, 0x24, blob, sizeof(blob)); }
    ssd_refresh(c, 0);
    {
        int outside = 0, inside_black = 0;
        for (int r = 0; r < EPD_H; r++) {
            for (int x = 0; x < EPD_W; x++) {
                int in = (r >= 237 && r <= 239 && x >= 400 && x <= 431);
                if (in) { if (c->image[r * EPD_W + x] == 0) inside_black++; }
                else if (c->image[r * EPD_W + x] != snap[r * EPD_W + x]) outside++;
            }
        }
        CHECK(outside == 0, "5a: %d pixels outside the window changed", outside);
        CHECK(inside_black == 3 * 32, "5a: %d black pixels inside the window (want %d)", inside_black, 3 * 32);
    }

    /* (5b) UC8279: inside PTIN, a whole plane's worth of DTM2 bytes must still
     * only reach the PTL rectangle. */
    epd_core_init(c, EPD_UC8279);
    uc_init(c);
    uc_stream_fb(c, 0x13, 0);
    uc_fill(c, 0x10, 0xFF, EPD_UC_PLANE_BYTES);
    uc_refresh(c, 0);
    uc_stream_fb(c, 0x10, 0);
    memcpy(snap, c->image, sizeof(snap));
    cmd(c, 0x91); uc_ptl(c, 8, 23, 400, 401);
    uc_fill(c, 0x13, 0x00, EPD_UC_PLANE_BYTES);       /* 60,000 bytes into a 4-byte window */
    cmd(c, 0x92);
    cmd(c, 0x91); uc_refresh(c, 1); cmd(c, 0x92);
    {
        int outside = 0, inside_black = 0;
        for (int r = 0; r < EPD_H; r++) {
            for (int x = 0; x < EPD_W; x++) {
                int in = (r >= 400 - 120 && r <= 401 - 120 && x >= 8 && x <= 23);
                if (in) { if (c->image[r * EPD_W + x] == 0) inside_black++; }
                else if (c->image[r * EPD_W + x] != snap[r * EPD_W + x]) outside++;
            }
        }
        CHECK(outside == 0, "5b: %d pixels outside the PTL window changed", outside);
        CHECK(inside_black == 2 * 16, "5b: %d black pixels inside the window (want 32)", inside_black);
    }
    CHECK(c->unknown_cmds == 0, "5: unknown commands %llu (last %02x)",
          (unsigned long long)c->unknown_cmds, c->last_unknown_cmd);
    free(c);
}

/* ==========================================================================
 * (6) Findings: the SSD1677 gate-scan byte
 * The third byte of 0x01 (driver output control) carries the gate scan
 * direction. Two readings exist and the model honours both (epd_core.c keeps the
 * bit and epd_core_compose() reverses the RAM row -> glass row mapping for it):
 *   - the driver's: Ssd1677Driver writes cfg.driverOutputScan (0x02) and ORs
 *     SCAN_TB_FLIP (0x01) for a mirrorY mount, "flips the gate scan order (TB
 *     bit) for an upside-down mount";
 *   - the SSD16xx datasheet's: B[0] GD, B[1] SM, B[2] TB, TB = 1 scanning
 *     G(n-1) -> G0, i.e. 0x02 | 0x04 = 0x06.
 * Either way the picture must come out mirrored vertically, so both encodings
 * are checked. The X4-class profiles ship NO_FLIP, so nothing in CrossPoint or
 * the stock sends a flipped scan byte today.
 * ========================================================================== */
static void test_gate_scan_flip(void)
{
    EpdCore *c = malloc(sizeof(*c));
    uint8_t row[EPD_WB];
    memset(row, 0x00, sizeof(row));

    /* Unflipped (the X4-class NO_FLIP profile): RAM row 479 is the top of the glass. */
    epd_core_init(c, EPD_SSD1677);
    cmd(c, 0x01); dat(c, 0xDF); dat(c, 0x01); dat(c, 0x02);
    ssd_window(c, 0x01, 0, 799, 479, 479);
    ssd_write_plane(c, 0x24, row, EPD_WB);
    ssd_refresh(c, 1);
    CHECK(c->image[0] == 0 && c->image[479 * EPD_W] == 255, "6: unflipped scan puts RAM row 479 at the top");

    /* Flipped gate scan: the same RAM row must now appear at the bottom. */
    for (int k = 0; k < 2; k++) {
        uint8_t scan = k ? 0x06 : 0x03;         /* datasheet TB bit : the driver's mirrorY bit */
        epd_core_init(c, EPD_SSD1677);
        cmd(c, 0x01); dat(c, 0xDF); dat(c, 0x01); dat(c, scan);
        ssd_window(c, 0x01, 0, 799, 479, 479);
        ssd_write_plane(c, 0x24, row, EPD_WB);
        ssd_refresh(c, 1);
        CHECK(c->image[479 * EPD_W] == 0 && c->image[0] == 255,
              "6: a flipped gate scan (0x01 third byte %02x, %s) must move RAM row 479 to glass "
              "row 479, not row 0", scan, k ? "datasheet TB" : "the driver's mirrorY");
    }
    free(c);
}

int main(void)
{
    test_data_entry_modes();
    test_ssd_edge_windows();
    test_uc_edge_windows();
    test_old_plane_resync();
    test_writes_outside_window();
    test_gate_scan_flip();

    if (xfails) {
        printf("\nWARNING: %d datasheet/driver expectation(s) the model does not meet "
               "(not counted as failures):\n", xfails);
        for (int i = 0; i < nxmsg; i++) printf("  - %s\n", xmsg[i]);
        printf("\n");
    }
    if (fails) { printf("%d failure(s) in %d checks\n", fails, checks); return 1; }
    printf("window tests: OK (%d checks, %d known model gap(s), %d newly met)\n", checks, xfails, xpasses);
    return 0;
}
