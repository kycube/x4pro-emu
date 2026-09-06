/*
 * E-paper controller cores for the Xteink X4 Pro panel (800x480):
 * SSD1677, UC8179 and UC8279 (X4 variant). Pure C, no QEMU headers.
 *
 * Feed the core what the wires carry: chip-select and data/command levels,
 * SPI bytes, a reset pulse, and the bit-banged probe clock. Read back the
 * visible image, the BUSY request, and statistics. The host decides what
 * "time" means: after epd_core_byte() the core may set busy_request_us > 0,
 * and the host is expected to drive BUSY for that long (guest time) and
 * then call epd_core_busy_done().
 *
 * x4pro-emu, GPL-2.0-or-later.
 */
#ifndef EPD_CORE_H
#define EPD_CORE_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define EPD_W 800
#define EPD_H 480
#define EPD_WB (EPD_W / 8)              /* 100 bytes per row */
#define EPD_PLANE_BYTES (EPD_WB * EPD_H)  /* 48000 */
#define EPD_UC_GATES 600                /* UC8279 X4: TRES height, visible 120..599 */
#define EPD_UC_PLANE_BYTES (EPD_WB * EPD_UC_GATES)

typedef enum {
    EPD_SSD1677 = 0,
    EPD_UC8179 = 1,
    EPD_UC8279 = 2,
} EpdVariant;

typedef enum {
    EPD_MODE_NONE = 0,
    EPD_MODE_FULL,
    EPD_MODE_FAST,
    EPD_MODE_HALF,
    EPD_MODE_GRAY,
    EPD_MODE_OTHER,
} EpdRefreshMode;

/* Refresh durations in microseconds; the host fills these (device defaults). */
typedef struct {
    uint32_t full_us;
    uint32_t fast_us;
    uint32_t half_us;
    uint32_t gray_us;
    uint32_t power_us;   /* power on/off, soft reset, auto-fill */
} EpdTiming;

typedef struct EpdCore EpdCore;

/* Optional trace callback: one panel command with its data. */
typedef void (*EpdTraceFn)(void *opaque, uint8_t cmd, const uint8_t *data, size_t len, bool truncated);

struct EpdCore {
    EpdVariant variant;
    EpdTiming timing;

    /* wires */
    bool cs;            /* true = selected (line LOW) */
    bool dc;            /* true = data (line HIGH) */
    bool in_reset;      /* RST line LOW */
    bool busy;          /* BUSY output as the SSD1677 drives it (busy = HIGH); UC: idle HIGH */
    uint32_t busy_request_us;   /* set by the core, consumed by the host */

    /* command state */
    int cmd;            /* current command or -1 */
    uint32_t cmd_len;   /* data bytes received for the current command */
    bool cmd_traced;    /* current command already emitted to the trace */
    uint8_t arg[256];   /* first bytes of the current command's data */

    /* SSD1677 RAM addressing */
    uint8_t data_entry;             /* 0x11 */
    uint16_t x_start, x_end, y_start, y_end;   /* 0x44 / 0x45 (pixel / row units) */
    uint16_t x_cnt, y_cnt;          /* 0x4E / 0x4F */
    uint8_t ctrl1, ctrl2;           /* 0x21 / 0x22 */
    uint8_t border;                 /* 0x3C */
    bool custom_lut;                /* 0x32 seen since the last OTP refresh */
    bool deep_sleep;

    /* UC8279 / UC8179 addressing */
    uint32_t uc_ptr;                /* byte pointer into the current plane */
    uint8_t psr[2];
    uint16_t tres_w, tres_h;
    bool partial_in;
    uint16_t ptl[4];                /* x0, x1, y0, y1 */
    bool power_on;
    bool uc_lut_loaded;             /* any 0x20..0x24 LUT written since power-on */

    /* planes (SSD1677: bw + red, 480 rows; UC: old(DTM1) + new(DTM2), 600 rows) */
    uint8_t plane0[EPD_UC_PLANE_BYTES];
    uint8_t plane1[EPD_UC_PLANE_BYTES];

    /* visible image after the last refresh: one byte per pixel, 0 = black, 255 = white */
    uint8_t image[EPD_W * EPD_H];
    bool image_dirty;
    bool image_gray_approx;         /* last refresh composed 4 levels from two planes */

    /* statistics */
    uint64_t refresh_count;
    EpdRefreshMode last_mode;
    uint8_t last_ctrl2;             /* SSD1677: 0x22 value; UC: 0xE5 TSSET value */
    uint64_t bytes_in;
    uint64_t cmds_in;
    uint64_t unknown_cmds;
    uint8_t last_unknown_cmd;
    uint64_t resets;
    uint64_t probe_reads;           /* 0x70/0x71/0xA2 seen (any variant) */

    /* bit-banged probe read-out (UC only) */
    bool probe_read_active;
    uint8_t probe_buf[64];
    int probe_len;
    int probe_bit;                  /* next bit index to present (0..len*8) */
    uint8_t bb_shift;               /* bit-bang input shift register */
    int bb_nbits;

    EpdTraceFn trace;
    void *trace_opaque;
};

void epd_core_init(EpdCore *c, EpdVariant v);
void epd_core_reset(EpdCore *c);                 /* hardware reset (RST low) */
void epd_core_set_cs(EpdCore *c, bool selected);
void epd_core_set_dc(EpdCore *c, bool data);
void epd_core_set_rst(EpdCore *c, bool line_high);
void epd_core_byte(EpdCore *c, uint8_t b);       /* one SPI byte from the master */
void epd_core_busy_done(EpdCore *c);             /* host: the BUSY interval elapsed */
void epd_core_flush_trace(EpdCore *c);           /* emit the pending command to the trace */

/* Bit-banged probe: call on each SCLK rising edge with the MOSI level, and on
 * each falling edge. Returns the level the controller drives on the shared
 * line (0/1) or -1 when it leaves the line floating. */
int epd_core_sclk_rise(EpdCore *c, int mosi);
int epd_core_sclk_fall(EpdCore *c);
int epd_core_line_level(const EpdCore *c);       /* current driven level or -1 */

/* Compose the visible image from the planes (called by refresh; also usable by tests). */
void epd_core_compose(EpdCore *c);
const char *epd_variant_name(EpdVariant v);
const char *epd_mode_name(EpdRefreshMode m);

#endif
