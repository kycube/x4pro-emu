/* What the host tests and the replay tool share: the wire helpers (a command
 * byte with DC low, data bytes with DC high, CS asserted around each) and a
 * CHECK that counts (no framework: asserts + counters). */
#ifndef EPD_TESTS_WIRE_H
#define EPD_TESTS_WIRE_H

#include "epd_core.h"
#include <stdio.h>

static inline void cmd(EpdCore *c, uint8_t b)
{
    epd_core_set_dc(c, false); epd_core_set_cs(c, true); epd_core_byte(c, b); epd_core_set_cs(c, false);
}

static inline void datn(EpdCore *c, const uint8_t *d, size_t n)
{
    epd_core_set_dc(c, true); epd_core_set_cs(c, true);
    for (size_t i = 0; i < n; i++) epd_core_byte(c, d[i]);
    epd_core_set_cs(c, false);
}

static inline void dat(EpdCore *c, uint8_t b) { datn(c, &b, 1); }

/* SSD1677 0x44/0x45/0x4E/0x4F values are 16-bit, low byte first (Ssd1677Driver::setRamArea). */
static inline void w16(EpdCore *c, int v) { dat(c, (uint8_t)(v & 0xFF)); dat(c, (uint8_t)((v >> 8) & 0xFF)); }

/* setRamArea() as the driver issues it: data entry, X window (start, end),
 * Y window (start, end), then both counters at the window's start. */
static inline void ssd_window(EpdCore *c, int mode, int xs, int xe, int ys, int ye)
{
    cmd(c, 0x11); dat(c, (uint8_t)mode);
    cmd(c, 0x44); w16(c, xs); w16(c, xe);
    cmd(c, 0x45); w16(c, ys); w16(c, ye);
    cmd(c, 0x4E); w16(c, xs);
    cmd(c, 0x4F); w16(c, ys);
}

static int checks __attribute__((unused)), fails __attribute__((unused));
#define CHECK(cond, ...) do { checks++; if (!(cond)) { fails++; printf("FAIL %s:%d: ", __FILE__, __LINE__); \
                              printf(__VA_ARGS__); printf("\n"); } } while (0)

#endif
