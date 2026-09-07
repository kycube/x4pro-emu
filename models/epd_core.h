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

/* UC81xx external LUT bank (0x20 VCOM, 0x21 WW, 0x22 BW, 0x23 WB, 0x24 BB) as the
 * UC8279 X4 receives it: up to 49 data bytes = 7 groups of 7 bytes, group g at 7g:
 *   [0] group header (0x01 in every populated group; 0x00 = unused group, skipped)
 *   [1..4] four phases, each byte = level << 6 | frames (0..63)
 *   [5] repeat count of the group   [6] second repeat/tail byte (0x01 or 0x00, ignored)
 * Levels (source tables): 00 GND (hold), 01 VDH (drives black), 10 VDL (drives white),
 * 11 floating (hold). VCOM table: 00 VCOM_DC, 01 VCOMH, 10 VCOML, 11 floating.
 * Evidence and doubts: docs/grayscale.md. The 42-byte settle tables are the same
 * layout with six groups. */
#define EPD_UC_LUT_BYTES 49
#define EPD_UC_LUT_GROUP_BYTES 7
#define EPD_UC_LUT_GROUPS 7
#define EPD_UC_LUT_PHASES_PER_GROUP 4
#define EPD_LUT_MAX_PHASES (EPD_UC_LUT_GROUPS * EPD_UC_LUT_PHASES_PER_GROUP)
#define EPD_GRAY_K_DEFAULT 28           /* reflectance change per frame at VDH/VDL (0..255 scale) */
#define EPD_FRAME_US_DEFAULT 6667       /* 150 Hz: PLL 0x0E = FRS 1110 in the UC8179c table; duration estimates only */

typedef enum {
    EPD_LUT_GND = 0,    /* VCOM table: VCOM_DC */
    EPD_LUT_VDH = 1,    /* VCOM table: VCOMH */
    EPD_LUT_VDL = 2,    /* VCOM table: VCOML */
    EPD_LUT_FLOAT = 3,
} EpdLutLevel;

typedef struct {
    uint8_t level;      /* EpdLutLevel */
    uint8_t frames;     /* 0..63 */
    uint8_t group;      /* 0..6 */
    uint8_t repeat;     /* the group's repeat count, applied to all its phases */
} EpdLutPhase;

typedef struct {
    int nphases;        /* phases with frames > 0, in table order (repeats not expanded) */
    int groups;         /* populated groups */
    uint32_t frames;    /* total frames, repeats applied */
    EpdLutPhase phase[EPD_LUT_MAX_PHASES];
} EpdLutSeq;

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
    uint8_t uc_lut[5][EPD_UC_LUT_BYTES];   /* the bank as last written (a new write zeroes its table) */
    uint8_t uc_lut_len[5];
    uint8_t uc_cdi;                 /* 0x50 data byte; bit 4 = DDX[0] (1: RAM bit 1 = white). MTP default 0x97 */

    /* planes (SSD1677: bw + red, 480 rows; UC: old(DTM1) + new(DTM2), 600 rows) */
    uint8_t plane0[EPD_UC_PLANE_BYTES];
    uint8_t plane1[EPD_UC_PLANE_BYTES];

    /* visible image after the last refresh: one byte per pixel, 0 = black, 255 = white */
    uint8_t image[EPD_W * EPD_H];
    bool image_dirty;
    bool image_gray_approx;         /* last refresh composed 4 levels from two planes (fixed table) */

    /* Waveform-level grey model (docs/grayscale.md), off by default. When on, a
     * refresh that runs the external LUT bank moves each pixel's reflectance
     * (image[] itself, 0..255) by its transition class: every frame at VDH takes
     * gray_k off, every frame at VDL adds gray_k, GND/VCOM/floating frames hold,
     * saturating at 0 and 255. OTP refreshes (no LUT, or PSR REG=0) drive the
     * new bit fully, as the approximation does. */
    bool waveform_gray;
    int gray_k;                     /* EPD_GRAY_K_DEFAULT */
    uint32_t frame_us;              /* EPD_FRAME_US_DEFAULT; only used for last_lut_us */
    bool image_gray_waveform;       /* last refresh went through the waveform model */
    uint32_t last_lut_frames;       /* frames of the last LUT refresh (VCOM table, repeats applied) */
    uint32_t last_lut_us;           /* last_lut_frames * frame_us */
    uint8_t gray_map[4][256];       /* per (old<<1 | new) class: reflectance in -> out (rebuilt per LUT refresh) */

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

/* ---- UC81xx LUT interpreter and grey model (docs/grayscale.md) ---- */
/* Parse one 0x20..0x24 table of len <= EPD_UC_LUT_BYTES bytes (a shorter table
 * reads as zero-padded) into its phase sequence. */
void epd_core_lut_phases(const uint8_t *table, size_t len, EpdLutSeq *out);
/* Run a parsed source table over one reflectance value with the model (k per
 * driven frame), groups repeated as their counts say, saturating at 0/255. */
uint8_t epd_core_lut_apply(const EpdLutSeq *seq, uint8_t reflectance, int k);
/* Which table a pixel's (old, new) RAM bits select under the current CDI DDX:
 * 1 = WW (0x21), 2 = BW (0x22), 3 = WB (0x23), 4 = BB (0x24). */
int epd_core_lut_index(const EpdCore *c, int old_bit, int new_bit);
/* Rebuild gray_map[] from the loaded bank (called by compose; usable by tests). */
void epd_core_build_gray_map(EpdCore *c);
const char *epd_variant_name(EpdVariant v);
const char *epd_mode_name(EpdRefreshMode m);

#endif
