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

int main(void)
{
    test_ssd1677_basic();
    test_ssd1677_window();
    test_uc8279_basic();
    test_probe();
    if (fails) { printf("%d failure(s)\n", fails); return 1; }
    printf("epd core tests: OK\n");
    return 0;
}
