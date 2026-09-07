/* Host unit tests for the panel cores (no framework: asserts + counters). */
#include "epd_core.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int fails = 0;
#define CHECK(cond, ...) do { if (!(cond)) { fails++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static void cmd(EpdCore *c, uint8_t b) { epd_core_set_dc(c, false); epd_core_set_cs(c, true); epd_core_byte(c, b); epd_core_set_cs(c, false); }
static void dat(EpdCore *c, uint8_t b) { epd_core_set_dc(c, true); epd_core_set_cs(c, true); epd_core_byte(c, b); epd_core_set_cs(c, false); }
static void datn(EpdCore *c, const uint8_t *d, size_t n) { epd_core_set_dc(c, true); epd_core_set_cs(c, true); for (size_t i = 0; i < n; i++) epd_core_byte(c, d[i]); epd_core_set_cs(c, false); }

/* The SSD1677 driver's setRamArea(0,0,800,480): X inc, Y dec, Y range 479..0, counters (0,479). */
static void ssd_full_window(EpdCore *c)
{
    cmd(c, 0x11); dat(c, 0x01);
    cmd(c, 0x44); dat(c, 0); dat(c, 0); dat(c, 0x1F); dat(c, 0x03);
    cmd(c, 0x45); dat(c, 0xDF); dat(c, 0x01); dat(c, 0); dat(c, 0);
    cmd(c, 0x4E); dat(c, 0); dat(c, 0);
    cmd(c, 0x4F); dat(c, 0xDF); dat(c, 0x01);
}

static void test_ssd1677_basic(void)
{
    EpdCore *c = malloc(sizeof(*c));
    epd_core_init(c, EPD_SSD1677);
    /* init sequence */
    cmd(c, 0x12); CHECK(c->busy && c->busy_request_us > 0, "swreset busy"); epd_core_busy_done(c);
    cmd(c, 0x18); dat(c, 0x80);
    cmd(c, 0x0C); { uint8_t b[5] = {0xAE, 0xC7, 0xC3, 0xC0, 0x80}; datn(c, b, 5); }
    cmd(c, 0x01); dat(c, 0xDF); dat(c, 0x01); dat(c, 0x02);
    cmd(c, 0x3C); dat(c, 0x80);
    ssd_full_window(c);
    CHECK(c->x_cnt == 0 && c->y_cnt == 479, "counters %u %u", c->x_cnt, c->y_cnt);
    cmd(c, 0x46); dat(c, 0xF7); epd_core_busy_done(c);
    cmd(c, 0x47); dat(c, 0xF7); epd_core_busy_done(c);
    CHECK(c->plane0[0] == 0xFF && c->plane1[47999] == 0xFF, "auto write white");

    /* frame: top row black, rest white */
    static uint8_t fb[EPD_PLANE_BYTES];
    memset(fb, 0xFF, sizeof(fb));
    memset(fb, 0x00, EPD_WB);           /* firmware row 0 = top of the picture */
    fb[EPD_WB * 479] = 0x7F;            /* bottom row, leftmost pixel black */
    ssd_full_window(c);
    cmd(c, 0x24); datn(c, fb, sizeof(fb));
    cmd(c, 0x26); datn(c, fb, sizeof(fb));
    CHECK(c->y_cnt == 479 && c->x_cnt == 0, "wrapped counters %u %u", c->x_cnt, c->y_cnt);
    /* HALF refresh */
    cmd(c, 0x21); dat(c, 0x40);
    cmd(c, 0x3C); dat(c, 0xC0);
    cmd(c, 0x1A); dat(c, 0x5A);
    cmd(c, 0x22); dat(c, 0xD7);
    cmd(c, 0x20);
    CHECK(c->refresh_count == 1, "refresh count %llu", (unsigned long long)c->refresh_count);
    CHECK(c->last_mode == EPD_MODE_HALF, "mode %s", epd_mode_name(c->last_mode));
    CHECK(c->busy && c->busy_request_us == c->timing.half_us, "busy for half");
    CHECK(c->image[0] == 0 && c->image[799] == 0, "top row black (%u %u)", c->image[0], c->image[799]);
    CHECK(c->image[EPD_W * 1] == 255, "second row white");
    CHECK(c->image[EPD_W * 479 + 0] == 0 && c->image[EPD_W * 479 + 1] == 255, "bottom-left pixel black");
    CHECK(!c->image_gray_approx, "not gray");
    epd_core_busy_done(c);
    CHECK(!c->busy, "busy cleared");
    /* probe reads are ignored on an SSD part */
    cmd(c, 0x71); CHECK(epd_core_line_level(c) == -1, "ssd leaves the line floating");
    CHECK(c->unknown_cmds == 0, "unknown %llu (last %02x)", (unsigned long long)c->unknown_cmds, c->last_unknown_cmd);
    free(c);
}

static void test_ssd1677_window(void)
{
    EpdCore *c = malloc(sizeof(*c));
    epd_core_init(c, EPD_SSD1677);
    /* setRamArea(x=16, y=8, w=16, h=2): y' = 480-8-2 = 470; X range 16..31, Y range 471..470 */
    cmd(c, 0x11); dat(c, 0x01);
    cmd(c, 0x44); dat(c, 16); dat(c, 0); dat(c, 31); dat(c, 0);
    cmd(c, 0x45); dat(c, 471 & 0xFF); dat(c, 471 >> 8); dat(c, 470 & 0xFF); dat(c, 470 >> 8);
    cmd(c, 0x4E); dat(c, 16); dat(c, 0);
    cmd(c, 0x4F); dat(c, 471 & 0xFF); dat(c, 471 >> 8);
    uint8_t win[4] = {0x00, 0xFF, 0xFF, 0x00};
    cmd(c, 0x24); datn(c, win, 4);
    cmd(c, 0x21); dat(c, 0x00); cmd(c, 0x22); dat(c, 0xFC); cmd(c, 0x20);
    /* picture row 8 = RAM row 471: bytes 2,3 = 00 FF -> pixels 16..23 black, 24..31 white */
    CHECK(c->image[8 * EPD_W + 16] == 0 && c->image[8 * EPD_W + 24] == 255, "window row 8");
    CHECK(c->image[9 * EPD_W + 16] == 255 && c->image[9 * EPD_W + 24] == 0, "window row 9");
    CHECK(c->last_mode == EPD_MODE_FAST, "fast");
    free(c);
}

static void test_uc8279_basic(void)
{
    EpdCore *c = malloc(sizeof(*c));
    epd_core_init(c, EPD_UC8279);
    cmd(c, 0x00); dat(c, 0x37); dat(c, 0x4D);
    cmd(c, 0x61); dat(c, 0x03); dat(c, 0x20); dat(c, 0x02); dat(c, 0x58);
    CHECK(c->tres_w == 800 && c->tres_h == 600, "tres %u %u", c->tres_w, c->tres_h);
    /* stream NEW plane: 120 white rows, 480 rows with top row black, rest white */
    static uint8_t row[EPD_WB];
    cmd(c, 0x13);
    memset(row, 0xFF, sizeof(row));
    for (int y = 0; y < 120; y++) datn(c, row, EPD_WB);
    memset(row, 0x00, sizeof(row)); datn(c, row, EPD_WB);
    memset(row, 0xFF, sizeof(row));
    for (int y = 1; y < 480; y++) datn(c, row, EPD_WB);
    cmd(c, 0x10);
    for (int y = 0; y < 600; y++) datn(c, row, EPD_WB);
    cmd(c, 0x50); dat(c, 0x97); cmd(c, 0xE0); dat(c, 0x02); cmd(c, 0xE5); dat(c, 0x1E);
    cmd(c, 0x04); epd_core_busy_done(c);
    cmd(c, 0x00); dat(c, 0x17); dat(c, 0x4D);
    cmd(c, 0x12);
    CHECK(c->refresh_count == 1 && c->last_mode == EPD_MODE_FULL, "uc full refresh (%s)", epd_mode_name(c->last_mode));
    CHECK(c->image[0] == 0 && c->image[EPD_W] == 255, "uc top row black");
    CHECK(c->unknown_cmds == 0, "unknown %llu (last %02x)", (unsigned long long)c->unknown_cmds, c->last_unknown_cmd);
    free(c);
}

/* Bit-banged probe as XteinkDetect does it: command with DC low, then DC high and read. */
static uint8_t bb_read_byte(EpdCore *c)
{
    uint8_t b = 0;
    for (int i = 0; i < 8; i++) {
        int lvl = epd_core_line_level(c);
        b = (uint8_t)((b << 1) | (lvl < 0 ? 1 : lvl));   /* floating reads as pull-up 1 */
        epd_core_sclk_rise(c, 1);
        epd_core_sclk_fall(c);
    }
    return b;
}
static void bb_cmd_read(EpdCore *c, uint8_t command, uint8_t *out, int len)
{
    epd_core_set_dc(c, false);
    epd_core_set_cs(c, true);
    for (int i = 7; i >= 0; i--) {
        epd_core_sclk_rise(c, (command >> i) & 1);
        epd_core_sclk_fall(c);
    }
    epd_core_set_dc(c, true);
    for (int i = 0; i < len; i++) out[i] = bb_read_byte(c);
    epd_core_set_cs(c, false);
}

static void test_probe(void)
{
    EpdCore *c = malloc(sizeof(*c));
    uint8_t flg = 0, ver[5] = {0}, mtp[49] = {0};
    epd_core_init(c, EPD_UC8279);
    bb_cmd_read(c, 0x71, &flg, 1);
    bb_cmd_read(c, 0x70, ver, 5);
    CHECK(flg == 0x13, "flg %02x", flg);
    CHECK(ver[0] == 0 && ver[1] == 0x0F && ver[2] == 0x68 && ver[3] == 0 && ver[4] == 0, "ver %02x %02x %02x %02x %02x", ver[0], ver[1], ver[2], ver[3], ver[4]);
    bb_cmd_read(c, 0xA2, mtp, 49);
    CHECK(mtp[1] == 0xA5 && mtp[2] == 0xA5 && mtp[1 + 0x1A] == 0x68 && mtp[48] == 0x7F, "mtp %02x %02x .. %02x .. %02x", mtp[1], mtp[2], mtp[1 + 0x1A], mtp[48]);
    epd_core_init(c, EPD_UC8179);
    bb_cmd_read(c, 0x70, ver, 5);
    CHECK(ver[2] == 0x01, "uc8179 lut_ver %02x", ver[2]);
    epd_core_init(c, EPD_SSD1677);
    bb_cmd_read(c, 0x71, &flg, 1);
    bb_cmd_read(c, 0x70, ver, 5);
    CHECK(flg == 0xFF && ver[0] == 0xFF && ver[4] == 0xFF, "ssd floats: %02x %02x", flg, ver[0]);
    free(c);
}

/* ---- UC8279 external LUT interpreter and the waveform grey model ---------- */
/* CrossPoint's AA bank for LUT_VER 0x68 (Uc8279X4Driver.cpp kXtfAa68): 49 bytes each,
 * only the first 14 non-zero. 0x20 VCOM, 0x21 WW, 0x22 BW, 0x23 WB, 0x24 BB. */
static const uint8_t kXtfAa68[5][14] = {
    {0x01, 0x02, 0x03, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01},
    {0x01, 0x02, 0x03, 0x41, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01},
    {0x01, 0x02, 0x83, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01},
    {0x01, 0x02, 0x83, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01},
    {0x01, 0x02, 0x03, 0x81, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01},
};
/* The settle bank (kXtfPreBwMid, 42 data bytes each, command prefix stripped). */
static const uint8_t kXtfPreBwMid[5][14] = {
    {0x01, 0x06, 0x01, 0x06, 0x06, 0x01, 0x01, 0x01, 0x02, 0x04, 0x00, 0x00, 0x01, 0x01},
    {0x01, 0x06, 0x81, 0x06, 0x06, 0x01, 0x01, 0x01, 0x02, 0x04, 0x00, 0x00, 0x01, 0x01},
    {0x01, 0x86, 0x81, 0x86, 0x86, 0x01, 0x01, 0x01, 0x82, 0x84, 0x00, 0x00, 0x01, 0x01},
    {0x01, 0x46, 0x41, 0x46, 0x46, 0x01, 0x01, 0x01, 0x42, 0x44, 0x00, 0x00, 0x01, 0x01},
    {0x01, 0x06, 0x01, 0x06, 0x06, 0x01, 0x01, 0x01, 0x02, 0x44, 0x00, 0x00, 0x01, 0x01},
};
/* The X3's AA bank (Uc8279X3Luts.h kUc8279X3_XtfAa): BW and WB differ (5 and 2 VDL frames). */
static const uint8_t kX3XtfAa[5][14] = {
    {0x01, 0x03, 0x02, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01},
    {0x01, 0x03, 0x02, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01},
    {0x01, 0x83, 0x82, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01},
    {0x01, 0x03, 0x82, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01},
    {0x01, 0x03, 0x02, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01},
};

static void lut_fill(uint8_t *out, const uint8_t *head14)
{
    memset(out, 0, 49);
    memcpy(out, head14, 14);
}

static int phase_is(const EpdLutPhase *p, int level, int frames, int group, int repeat)
{
    return p->level == level && p->frames == frames && p->group == group && p->repeat == repeat;
}

static void test_lut_parse(void)
{
    uint8_t t[49];
    EpdLutSeq q;
    /* AA68 WW: one populated group, phases GND 2, GND 3, VDH 1, GND 1; group 1 has header 0 -> skipped */
    lut_fill(t, kXtfAa68[1]);
    epd_core_lut_phases(t, 49, &q);
    CHECK(q.groups == 1 && q.nphases == 4 && q.frames == 7, "aa ww groups %d phases %d frames %u", q.groups, q.nphases, q.frames);
    CHECK(phase_is(&q.phase[0], EPD_LUT_GND, 2, 0, 1), "aa ww p0");
    CHECK(phase_is(&q.phase[1], EPD_LUT_GND, 3, 0, 1), "aa ww p1");
    CHECK(phase_is(&q.phase[2], EPD_LUT_VDH, 1, 0, 1), "aa ww p2 (%u,%u)", q.phase[2].level, q.phase[2].frames);
    CHECK(phase_is(&q.phase[3], EPD_LUT_GND, 1, 0, 1), "aa ww p3");
    /* BW and WB: GND 2, VDL 3, GND 1, GND 1 -- byte-identical tables */
    for (int i = 2; i <= 3; i++) {
        lut_fill(t, kXtfAa68[i]);
        epd_core_lut_phases(t, 49, &q);
        CHECK(q.nphases == 4 && q.frames == 7, "aa %d phases", i);
        CHECK(phase_is(&q.phase[1], EPD_LUT_VDL, 3, 0, 1), "aa %d p1 = VDL 3 (%u,%u)", i, q.phase[1].level, q.phase[1].frames);
        CHECK(q.phase[0].level == EPD_LUT_GND && q.phase[2].level == EPD_LUT_GND, "aa %d others hold", i);
    }
    /* BB: GND 2, GND 3, VDL 1, GND 1 */
    lut_fill(t, kXtfAa68[4]);
    epd_core_lut_phases(t, 49, &q);
    CHECK(q.frames == 7 && phase_is(&q.phase[2], EPD_LUT_VDL, 1, 0, 1), "aa bb p2 = VDL 1");
    /* VCOM: all at VCOM_DC, 7 frames -- the frame totals of the five tables agree */
    lut_fill(t, kXtfAa68[0]);
    epd_core_lut_phases(t, 49, &q);
    CHECK(q.frames == 7 && q.nphases == 4, "aa vcom frames %u", q.frames);
    for (int i = 0; i < 4; i++) CHECK(q.phase[i].level == EPD_LUT_GND, "aa vcom level %d", i);

    /* the settle bank: two groups, 25 frames in every table */
    for (int i = 0; i < 5; i++) {
        lut_fill(t, kXtfPreBwMid[i]);
        epd_core_lut_phases(t, 42, &q);
        CHECK(q.groups == 2 && q.frames == 25, "prebw %d groups %d frames %u", i, q.groups, q.frames);
    }
    lut_fill(t, kXtfPreBwMid[2]);   /* BW: VDL 6,1,6,6 then VDL 2,4 */
    epd_core_lut_phases(t, 42, &q);
    CHECK(q.nphases == 6, "prebw bw phases %d", q.nphases);
    { int vdl = 0; for (int i = 0; i < q.nphases; i++) if (q.phase[i].level == EPD_LUT_VDL) vdl += q.phase[i].frames;
      CHECK(vdl == 25, "prebw bw vdl frames %d", vdl); }
    lut_fill(t, kXtfPreBwMid[3]);   /* WB: the same at VDH */
    epd_core_lut_phases(t, 42, &q);
    { int vdh = 0; for (int i = 0; i < q.nphases; i++) if (q.phase[i].level == EPD_LUT_VDH) vdh += q.phase[i].frames;
      CHECK(vdh == 25, "prebw wb vdh frames %d", vdh); }
    lut_fill(t, kXtfPreBwMid[4]);   /* BB: one VDH 4 kick in group 1 */
    epd_core_lut_phases(t, 42, &q);
    CHECK(q.nphases == 6 && phase_is(&q.phase[5], EPD_LUT_VDH, 4, 1, 1), "prebw bb tail (%u,%u,g%u)", q.phase[5].level, q.phase[5].frames, q.phase[5].group);

    /* repeat counts multiply the group; a 0 count runs it 0 times; a short table is zero-padded */
    uint8_t syn[49] = {0x01, 0x85, 0x42, 0x00, 0x00, 0x03, 0x01,   0x01, 0x81, 0x00, 0x00, 0x00, 0x00, 0x01};
    epd_core_lut_phases(syn, 49, &q);
    CHECK(q.groups == 2 && q.nphases == 3 && q.frames == (5 + 2) * 3, "syn frames %u", q.frames);
    {
        /* from 0: group 0 x3: +50 -20 = +30 each -> 90; group 1 never runs (count 0) */
        uint8_t v = epd_core_lut_apply(&q, 0, 10);
        CHECK(v == 90, "syn apply %u", v);
        epd_core_lut_phases(syn, 7, &q);   /* only the first group sent */
        CHECK(q.groups == 1 && q.frames == 21, "syn short frames %u", q.frames);
    }
}

static void test_lut_apply(void)
{
    uint8_t t[49];
    EpdLutSeq q;
    const int k = EPD_GRAY_K_DEFAULT;
    lut_fill(t, kXtfAa68[1]); epd_core_lut_phases(t, 49, &q);      /* WW: VDH 1 */
    CHECK(epd_core_lut_apply(&q, 0, k) == 0, "ww from black stays black");
    CHECK(epd_core_lut_apply(&q, 255, k) == 255 - k, "ww from white: one frame darker (%u)", epd_core_lut_apply(&q, 255, k));
    lut_fill(t, kXtfAa68[2]); epd_core_lut_phases(t, 49, &q);      /* BW: VDL 3 */
    CHECK(epd_core_lut_apply(&q, 0, k) == 3 * k, "bw from black = 3k (%u)", epd_core_lut_apply(&q, 0, k));
    CHECK(epd_core_lut_apply(&q, 255, k) == 255, "bw from white saturates");
    lut_fill(t, kXtfAa68[4]); epd_core_lut_phases(t, 49, &q);      /* BB: VDL 1 */
    CHECK(epd_core_lut_apply(&q, 255, k) == 255, "bb from white stays white");
    lut_fill(t, kXtfPreBwMid[2]); epd_core_lut_phases(t, 42, &q);  /* BW settle: 25 VDL frames */
    CHECK(epd_core_lut_apply(&q, 0, k) == 255, "prebw bw reaches white");
    lut_fill(t, kXtfPreBwMid[3]); epd_core_lut_phases(t, 42, &q);
    CHECK(epd_core_lut_apply(&q, 255, k) == 0, "prebw wb reaches black");
    lut_fill(t, kXtfPreBwMid[4]); epd_core_lut_phases(t, 42, &q);
    CHECK(epd_core_lut_apply(&q, 3 * k, k) == 0, "prebw bb clears a grey");
    lut_fill(t, kXtfPreBwMid[1]); epd_core_lut_phases(t, 42, &q);
    CHECK(epd_core_lut_apply(&q, 255, k) == 255, "prebw ww keeps white");
}

/* Stream a 600-gate plane whose visible row 0 (gate 120) is `row`; the other visible
 * rows are `fill` (0xFF = white in a base plane, 0x00 = white in an inverted AA plane);
 * the padding gates are 0xFF as the driver sends them. */
static void uc_plane_row0_fill(EpdCore *c, uint8_t plane_cmd, const uint8_t *row, uint8_t fill)
{
    static uint8_t pad[EPD_WB], rest[EPD_WB];
    memset(pad, 0xFF, sizeof(pad));
    memset(rest, fill, sizeof(rest));
    cmd(c, plane_cmd);
    for (int y = 0; y < 120; y++) datn(c, pad, EPD_WB);
    datn(c, row, EPD_WB);
    for (int y = 1; y < 480; y++) datn(c, rest, EPD_WB);
}
static void uc_plane_row0(EpdCore *c, uint8_t plane_cmd, const uint8_t *row)
{
    uc_plane_row0_fill(c, plane_cmd, row, 0xFF);
}

static void set_px(uint8_t *row, int x, int bit)
{
    if (bit) row[x >> 3] |= (uint8_t)(0x80 >> (x & 7)); else row[x >> 3] &= (uint8_t)~(0x80 >> (x & 7));
}

static void uc_load_bank(EpdCore *c, const uint8_t (*bank)[14], size_t len)
{
    uint8_t t[49];
    for (int i = 0; i < 5; i++) {
        lut_fill(t, bank[i]);
        cmd(c, (uint8_t)(0x20 + i));
        datn(c, t, len);
    }
}

/* CrossPoint's reader on the UC8279 X4 (Uc8279X4Driver.cpp): pixels x=0 black, 1 dark grey,
 * 2 light grey, 3 white. Base = white for white only; plane0 = base|lsb, plane1 = plane0^msb,
 * both streamed inverted: wire (DTM1, DTM2) = black (1,1) dark (0,1) light (1,0) white (0,0). */
static void reader_base(EpdCore *c, const uint8_t *base_row, uint8_t psr0)
{
    uc_plane_row0(c, 0x13, base_row);
    static uint8_t white[EPD_WB];
    memset(white, 0xFF, sizeof(white));
    cmd(c, 0x10); for (int y = 0; y < 600; y++) datn(c, white, EPD_WB);
    cmd(c, 0x50); dat(c, 0x97); cmd(c, 0xE0); dat(c, 0x02); cmd(c, 0xE5); dat(c, 0x1E);
    cmd(c, 0x04); epd_core_busy_done(c);
    cmd(c, 0x00); dat(c, psr0); dat(c, 0x4D);
    cmd(c, 0x12); epd_core_busy_done(c);
}

static void reader_aa(EpdCore *c, const uint8_t (*bank)[14], const uint8_t *p0_wire, const uint8_t *p1_wire)
{
    uc_plane_row0_fill(c, 0x10, p0_wire, 0x00);
    uc_plane_row0_fill(c, 0x13, p1_wire, 0x00);
    cmd(c, 0x00); dat(c, 0x37); dat(c, 0x4D);
    uc_load_bank(c, bank, 49);
    cmd(c, 0x50); dat(c, 0x97);
    cmd(c, 0x04); epd_core_busy_done(c);
    cmd(c, 0x00); dat(c, 0x37); dat(c, 0x4D);
    cmd(c, 0x12); epd_core_busy_done(c);
}

static void test_waveform_reader_page(void)
{
    EpdCore *c = malloc(sizeof(*c));
    epd_core_init(c, EPD_UC8279);
    c->waveform_gray = true;
    cmd(c, 0x00); dat(c, 0x37); dat(c, 0x4D);
    cmd(c, 0x61); dat(c, 0x03); dat(c, 0x20); dat(c, 0x02); dat(c, 0x58);
    uint8_t base[EPD_WB], p0[EPD_WB], p1[EPD_WB];
    memset(base, 0xFF, sizeof(base));
    set_px(base, 0, 0); set_px(base, 1, 0); set_px(base, 2, 0); set_px(base, 3, 1);
    reader_base(c, base, 0x17);   /* OTP GC: full drive */
    CHECK(c->last_mode == EPD_MODE_FULL && !c->image_gray_waveform && !c->image_gray_approx, "base is mono otp");
    CHECK(c->image[0] == 0 && c->image[1] == 0 && c->image[2] == 0 && c->image[3] == 255, "base levels %u %u %u %u", c->image[0], c->image[1], c->image[2], c->image[3]);
    memset(p0, 0x00, sizeof(p0)); memset(p1, 0x00, sizeof(p1));   /* white background: BB */
    set_px(p0, 0, 1); set_px(p1, 0, 1);   /* black: WW */
    set_px(p0, 1, 0); set_px(p1, 1, 1);   /* dark: BW */
    set_px(p0, 2, 1); set_px(p1, 2, 0);   /* light: WB */
    set_px(p0, 3, 0); set_px(p1, 3, 0);   /* white: BB */
    reader_aa(c, kXtfAa68, p0, p1);
    CHECK(c->last_mode == EPD_MODE_GRAY && c->image_gray_waveform && !c->image_gray_approx, "aa is a waveform refresh");
    CHECK(c->last_lut_frames == 7 && c->last_lut_us == 7 * EPD_FRAME_US_DEFAULT, "aa frames %u us %u", c->last_lut_frames, c->last_lut_us);
    const int k = EPD_GRAY_K_DEFAULT;
    CHECK(c->image[0] == 0, "black stays black (%u)", c->image[0]);
    CHECK(c->image[1] == 3 * k, "dark = 3 VDL frames from black (%u)", c->image[1]);
    CHECK(c->image[2] == 3 * k, "light = the same table as dark on this bank (%u)", c->image[2]);
    CHECK(c->image[3] == 255, "white stays white (%u)", c->image[3]);
    CHECK(c->image[4] == 255 && c->image[EPD_W] == 255, "untouched pixels white");
    /* the driver restores the base to both planes without a refresh: nothing changes */
    uc_plane_row0(c, 0x10, base); uc_plane_row0(c, 0x13, base);
    CHECK(c->image[1] == 3 * k, "planes alone do not move pixels");
    /* next page: settle transition (prebw, REG=1). Pixel 1 becomes white, pixel 2 black. */
    uint8_t base2[EPD_WB];
    memset(base2, 0xFF, sizeof(base2));
    set_px(base2, 0, 0); set_px(base2, 1, 1); set_px(base2, 2, 0); set_px(base2, 3, 1);
    uc_plane_row0(c, 0x13, base2);
    cmd(c, 0x91);
    cmd(c, 0x90); { uint8_t w[9] = {0, 0, 0x03, 0x1F, 0, 0x78, 0x02, 0x57, 0x01}; datn(c, w, 9); }
    cmd(c, 0x00); dat(c, 0x37); dat(c, 0x4D);
    cmd(c, 0x50); dat(c, 0xD7); cmd(c, 0xE0); dat(c, 0x02); cmd(c, 0xE5); dat(c, 0x5A);
    uc_load_bank(c, kXtfPreBwMid, 42);
    cmd(c, 0x12); epd_core_busy_done(c); cmd(c, 0x92);
    CHECK(c->image_gray_waveform && c->last_lut_frames == 25, "prebw ran through the model (%u frames)", c->last_lut_frames);
    CHECK(c->image[0] == 0 && c->image[1] == 255 && c->image[2] == 0 && c->image[3] == 255,
          "settle levels %u %u %u %u", c->image[0], c->image[1], c->image[2], c->image[3]);
    /* the new page's AA pass: pixel 2 (black in base2) becomes dark grey, pixel 1 stays white */
    uint8_t q0[EPD_WB], q1[EPD_WB];
    memset(q0, 0x00, sizeof(q0)); memset(q1, 0x00, sizeof(q1));
    set_px(q0, 0, 1); set_px(q1, 0, 1);
    set_px(q0, 2, 0); set_px(q1, 2, 1);
    reader_aa(c, kXtfAa68, q0, q1);
    CHECK(c->image[2] == 3 * k && c->image[1] == 255 && c->image[0] == 0, "page 2 levels %u %u %u", c->image[0], c->image[1], c->image[2]);
    /* an OTP DU afterwards drives fully: grey 3k -> the new bit */
    uc_plane_row0(c, 0x10, base2);
    uc_plane_row0(c, 0x13, base2);
    cmd(c, 0x00); dat(c, 0x17); dat(c, 0x4D);
    cmd(c, 0x12); epd_core_busy_done(c);
    CHECK(!c->image_gray_waveform && !c->image_gray_approx, "otp again");
    CHECK(c->image[1] == 255 && c->image[2] == 0, "otp drives fully (%u %u)", c->image[1], c->image[2]);
    CHECK(c->unknown_cmds == 0, "unknown %llu", (unsigned long long)c->unknown_cmds);
    free(c);
}

static void test_waveform_four_levels(void)
{
    /* A bank whose BW and WB differ (the X3's): four distinct, ordered levels. */
    EpdCore *c = malloc(sizeof(*c));
    epd_core_init(c, EPD_UC8279);
    c->waveform_gray = true;
    cmd(c, 0x00); dat(c, 0x37); dat(c, 0x4D);
    cmd(c, 0x61); dat(c, 0x03); dat(c, 0x20); dat(c, 0x02); dat(c, 0x58);
    uint8_t base[EPD_WB], p0[EPD_WB], p1[EPD_WB];
    memset(base, 0xFF, sizeof(base));
    set_px(base, 0, 0); set_px(base, 1, 0); set_px(base, 2, 0); set_px(base, 3, 1);
    reader_base(c, base, 0x17);
    memset(p0, 0x00, sizeof(p0)); memset(p1, 0x00, sizeof(p1));
    set_px(p0, 0, 1); set_px(p1, 0, 1);
    set_px(p0, 1, 0); set_px(p1, 1, 1);   /* BW: VDL 5 */
    set_px(p0, 2, 1); set_px(p1, 2, 0);   /* WB: VDL 2 */
    set_px(p0, 3, 0); set_px(p1, 3, 0);
    reader_aa(c, kX3XtfAa, p0, p1);
    const int k = EPD_GRAY_K_DEFAULT;
    CHECK(c->image[0] == 0 && c->image[2] == 2 * k && c->image[1] == 5 * k && c->image[3] == 255,
          "four levels %u %u %u %u", c->image[0], c->image[2], c->image[1], c->image[3]);
    CHECK(c->image[0] < c->image[2] && c->image[2] < c->image[1] && c->image[1] < c->image[3], "ordered");
    /* the tunable: a larger k spreads the greys */
    c->gray_k = 40;
    reader_base(c, base, 0x17);
    reader_aa(c, kX3XtfAa, p0, p1);
    CHECK(c->image[2] == 80 && c->image[1] == 200, "k=40 levels %u %u", c->image[2], c->image[1]);
    /* DDX[0] = 0 flips the polarity: the same wire bits select the mirrored tables */
    reader_base(c, base, 0x17);
    uc_plane_row0(c, 0x10, p0); uc_plane_row0(c, 0x13, p1);
    cmd(c, 0x00); dat(c, 0x37); dat(c, 0x4D);
    uc_load_bank(c, kX3XtfAa, 49);
    cmd(c, 0x50); dat(c, 0x87);
    CHECK(epd_core_lut_index(c, 1, 1) == 4 && epd_core_lut_index(c, 0, 1) == 3 && epd_core_lut_index(c, 1, 0) == 2 && epd_core_lut_index(c, 0, 0) == 1, "ddx0 mapping");
    cmd(c, 0x50); dat(c, 0x97);
    CHECK(epd_core_lut_index(c, 1, 1) == 1 && epd_core_lut_index(c, 0, 1) == 2 && epd_core_lut_index(c, 1, 0) == 3 && epd_core_lut_index(c, 0, 0) == 4, "ddx1 mapping");
    free(c);
}

static void test_waveform_off_and_otp_unchanged(void)
{
    /* The same stream into a core with the flag off gives today's approximation; OTP
     * refreshes are identical with the flag on or off. */
    EpdCore *a = malloc(sizeof(*a)), *b = malloc(sizeof(*b));
    epd_core_init(a, EPD_UC8279); epd_core_init(b, EPD_UC8279);
    a->waveform_gray = false;       /* the fixed two-plane table (the default until 2026-09-07) */
    b->waveform_gray = true;
    uint8_t base[EPD_WB], p0[EPD_WB], p1[EPD_WB];
    memset(base, 0xFF, sizeof(base));
    set_px(base, 0, 0); set_px(base, 1, 0); set_px(base, 2, 0); set_px(base, 3, 1);
    memset(p0, 0x00, sizeof(p0)); memset(p1, 0x00, sizeof(p1));
    set_px(p0, 0, 1); set_px(p1, 0, 1); set_px(p0, 1, 0); set_px(p1, 1, 1);
    set_px(p0, 2, 1); set_px(p1, 2, 0); set_px(p0, 3, 0); set_px(p1, 3, 0);
    EpdCore *cs[2] = {a, b};
    for (int i = 0; i < 2; i++) {
        EpdCore *c = cs[i];
        cmd(c, 0x00); dat(c, 0x37); dat(c, 0x4D);
        cmd(c, 0x61); dat(c, 0x03); dat(c, 0x20); dat(c, 0x02); dat(c, 0x58);
        reader_base(c, base, 0x17);
    }
    CHECK(memcmp(a->image, b->image, sizeof(a->image)) == 0, "otp base identical");
    /* a LUT loaded but PSR REG=0: still OTP, still identical */
    uc_load_bank(b, kXtfAa68, 49);
    reader_base(b, base, 0x17);
    CHECK(b->last_mode == EPD_MODE_FULL && memcmp(a->image, b->image, sizeof(a->image)) == 0, "lut + reg=0 is otp");
    reader_aa(a, kXtfAa68, p0, p1);
    reader_aa(b, kXtfAa68, p0, p1);
    CHECK(a->image_gray_approx && !a->image_gray_waveform, "approx flags");
    CHECK(a->image[0] == 0 && a->image[1] == 170 && a->image[2] == 85 && a->image[3] == 255,
          "approximation table unchanged: %u %u %u %u", a->image[0], a->image[1], a->image[2], a->image[3]);
    CHECK(b->image[1] == 3 * EPD_GRAY_K_DEFAULT, "waveform core differs by design");
    free(a); free(b);
}

static void test_ssd1677_gray_unchanged_by_flag(void)
{
    EpdCore *c = malloc(sizeof(*c));
    epd_core_init(c, EPD_SSD1677);
    c->waveform_gray = true;
    ssd_full_window(c);
    static uint8_t bw[EPD_PLANE_BYTES], red[EPD_PLANE_BYTES];
    memset(bw, 0xFF, sizeof(bw)); memset(red, 0xFF, sizeof(red));
    bw[0] = 0x3F;    /* stream byte 0 lands on RAM row 479 = picture row 0: pixels 0,1 -> bw 0 */
    red[0] = 0x7F;   /* pixel 0 -> red 0 */
    cmd(c, 0x24); datn(c, bw, sizeof(bw));
    ssd_full_window(c);
    cmd(c, 0x26); datn(c, red, sizeof(red));
    cmd(c, 0x32); { uint8_t lut[105]; memset(lut, 0x11, sizeof(lut)); datn(c, lut, sizeof(lut)); }
    cmd(c, 0x21); dat(c, 0x00); cmd(c, 0x22); dat(c, 0xCC); cmd(c, 0x20);
    CHECK(c->image_gray_approx && !c->image_gray_waveform, "ssd keeps the approximation");
    CHECK(c->image[0] == 0 && c->image[1] == 85 && c->image[2] == 255, "ssd levels %u %u %u", c->image[0], c->image[1], c->image[2]);
    free(c);
}

int main(void)
{
    test_ssd1677_basic();
    test_ssd1677_window();
    test_uc8279_basic();
    test_probe();
    test_lut_parse();
    test_lut_apply();
    test_waveform_reader_page();
    test_waveform_four_levels();
    test_waveform_off_and_otp_unchanged();
    test_ssd1677_gray_unchanged_by_flag();
    if (fails) { printf("%d failure(s)\n", fails); return 1; }
    printf("epd core tests: OK\n");
    return 0;
}
