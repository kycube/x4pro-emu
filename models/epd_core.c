/*
 * E-paper controller cores: SSD1677 / UC8179 / UC8279 (X4 800x480 panel).
 * See epd_core.h. Command semantics from
 *   freeink-sdk/libs/display/FreeInkDisplay/src/driver/Ssd1677Driver.cpp
 *   freeink-sdk/libs/display/FreeInkDisplay/src/driver/Uc8279X4Driver.cpp
 *   freeink-sdk/libs/hardware/XteinkDetect/src/XteinkDetect.cpp (probe)
 * and the SSD1677 / UC8179 datasheets for register widths.
 *
 * x4pro-emu, GPL-2.0-or-later.
 */
#include "epd_core.h"
#include <string.h>

/* ---- SSD1677 commands ----------------------------------------------------- */
#define S_DRIVER_OUTPUT     0x01
#define S_GATE_VOLTAGE      0x03
#define S_SOURCE_VOLTAGE    0x04
#define S_BOOSTER           0x0C
#define S_DEEP_SLEEP        0x10
#define S_DATA_ENTRY        0x11
#define S_SW_RESET          0x12
#define S_TEMP_SENSOR       0x18
#define S_WRITE_TEMP        0x1A
#define S_MASTER_ACT        0x20
#define S_UPDATE_CTRL1      0x21
#define S_UPDATE_CTRL2      0x22
#define S_WRITE_RAM_BW      0x24
#define S_WRITE_RAM_RED     0x26
#define S_WRITE_VCOM        0x2C
#define S_WRITE_LUT         0x32
#define S_BORDER            0x3C
#define S_RAM_X_RANGE       0x44
#define S_RAM_Y_RANGE       0x45
#define S_AUTO_WRITE_BW     0x46
#define S_AUTO_WRITE_RED    0x47
#define S_RAM_X_CNT         0x4E
#define S_RAM_Y_CNT         0x4F

/* ---- UC81xx commands ------------------------------------------------------ */
#define U_PSR   0x00
#define U_PWR   0x01
#define U_BTST  0x06
#define U_TCON  0x60
#define U_PWS   0xE3
#define U_VDCS  0x82
#define U_TSC   0x40
#define U_TSE   0x41
#define U_LPD   0x51
#define U_AUTO  0x17
#define U_FLG2  0x71
#define U_POF   0x02
#define U_PFS   0x03
#define U_PON   0x04
#define U_DSLP  0x07
#define U_DTM1  0x10
#define U_DRF   0x12
#define U_DTM2  0x13
#define U_LUT0  0x20
#define U_LUT4  0x24
#define U_PLL   0x30
#define U_CDI   0x50
#define U_TRES  0x61
#define U_GSST  0x65
#define U_VER   0x70
#define U_FLG   0x71
#define U_PTL   0x90
#define U_PTIN  0x91
#define U_PTOUT 0x92
#define U_RMTP  0xA2
#define U_CCSET 0xE0
#define U_GSCAN 0xE1
#define U_TSSET 0xE5

static void trace_cmd(EpdCore *c);
static void request_busy(EpdCore *c, uint32_t us);

const char *epd_variant_name(EpdVariant v)
{
    switch (v) {
    case EPD_SSD1677: return "ssd1677";
    case EPD_UC8179: return "uc8179";
    case EPD_UC8279: return "uc8279";
    }
    return "?";
}

const char *epd_mode_name(EpdRefreshMode m)
{
    switch (m) {
    case EPD_MODE_NONE: return "none";
    case EPD_MODE_FULL: return "full";
    case EPD_MODE_FAST: return "fast";
    case EPD_MODE_HALF: return "half";
    case EPD_MODE_GRAY: return "gray";
    case EPD_MODE_OTHER: return "other";
    }
    return "?";
}

static void ssd_reset_regs(EpdCore *c)
{
    c->data_entry = 0x03;
    c->x_start = 0; c->x_end = EPD_W - 1;
    c->y_start = 0; c->y_end = EPD_H - 1;
    c->x_cnt = 0; c->y_cnt = 0;
    c->ctrl1 = 0; c->ctrl2 = 0;
    c->border = 0;
    c->custom_lut = false;
    c->deep_sleep = false;
}

static void uc_reset_regs(EpdCore *c)
{
    c->uc_ptr = 0;
    c->psr[0] = 0x0F; c->psr[1] = 0x00;
    c->tres_w = EPD_W; c->tres_h = EPD_UC_GATES;
    c->partial_in = false;
    memset(c->ptl, 0, sizeof(c->ptl));
    c->power_on = false;
    c->uc_lut_loaded = false;
    c->deep_sleep = false;
}

void epd_core_init(EpdCore *c, EpdVariant v)
{
    memset(c, 0, sizeof(*c));
    c->variant = v;
    c->cmd = -1;
    /* Desk unit (UC8279, CrossPoint 1.6.0): DRF full 1326 ms, DRF DU 483 ms, PON 40 ms.
     * SSD1677 units: support doc says full ~1800 ms, partial ~500 ms. */
    if (v == EPD_SSD1677) {
        c->timing.full_us = 1800000;
        c->timing.fast_us = 500000;
        c->timing.half_us = 1800000;
        c->timing.gray_us = 600000;
        c->timing.power_us = 20000;
    } else {
        c->timing.full_us = 1326000;
        c->timing.fast_us = 483000;
        c->timing.half_us = 1326000;
        c->timing.gray_us = 600000;
        c->timing.power_us = 40000;
    }
    memset(c->plane0, 0xFF, sizeof(c->plane0));
    memset(c->plane1, 0xFF, sizeof(c->plane1));
    memset(c->image, 0xFF, sizeof(c->image));
    ssd_reset_regs(c);
    uc_reset_regs(c);
    c->busy = false;
    c->image_dirty = true;
}

void epd_core_reset(EpdCore *c)
{
    trace_cmd(c);
    c->cmd = -1;
    c->cmd_len = 0;
    ssd_reset_regs(c);
    uc_reset_regs(c);
    c->probe_read_active = false;
    c->bb_nbits = 0;
    c->busy = false;
    c->busy_request_us = 0;
    c->resets++;
    /* RAM contents survive a reset on both controller families (not a DSLP) */
}

void epd_core_set_rst(EpdCore *c, bool line_high)
{
    bool was = c->in_reset;
    c->in_reset = !line_high;
    if (!was && c->in_reset) {
        epd_core_reset(c);
        c->busy = true;            /* BUSY asserted while in reset (SSD: HIGH, UC: BUSY_N LOW) */
    } else if (was && !c->in_reset) {
        /* after the reset pulse the controller stays busy for its power-up time,
         * which gives firmware that waits for the BUSY edge something to see */
        request_busy(c, c->timing.power_us);
    }
}

void epd_core_set_cs(EpdCore *c, bool selected)
{
    if (c->cs && !selected) {
        /* end of a transaction: a multi-byte command's data is complete */
        c->probe_read_active = false;
        c->bb_nbits = 0;
    }
    c->cs = selected;
}

void epd_core_set_dc(EpdCore *c, bool data)
{
    c->dc = data;
    if (data && c->cs && c->variant != EPD_SSD1677) {
        /* A UC part starts driving the read-out right after the command byte
         * when the master switches DC high and releases the line. */
        if (c->cmd == U_VER || c->cmd == U_FLG || c->cmd == U_RMTP) {
            c->probe_read_active = true;
            c->probe_bit = 0;
        }
    }
}

static void trace_cmd(EpdCore *c)
{
    if (c->trace && c->cmd >= 0 && !c->cmd_traced) {
        size_t n = c->cmd_len < sizeof(c->arg) ? c->cmd_len : sizeof(c->arg);
        c->trace(c->trace_opaque, (uint8_t)c->cmd, c->arg, n, c->cmd_len > sizeof(c->arg));
        c->cmd_traced = true;
    }
}

void epd_core_flush_trace(EpdCore *c)
{
    trace_cmd(c);
}

/* ---- SSD1677 RAM write ---------------------------------------------------- */
static void ssd_ram_write(EpdCore *c, uint8_t *plane, uint8_t b)
{
    if (c->x_cnt < EPD_W && c->y_cnt < EPD_H) {
        plane[c->y_cnt * EPD_WB + (c->x_cnt / 8)] = b;
    }
    /* advance per data entry mode: bit0 X dir (1 = inc), bit1 Y dir (1 = inc), bit2 = Y-first */
    bool x_inc = c->data_entry & 1;
    bool y_inc = c->data_entry & 2;
    bool at_x_end = x_inc ? (c->x_cnt / 8 >= c->x_end / 8) : (c->x_cnt / 8 <= c->x_start / 8);
    if (!at_x_end) {
        c->x_cnt = x_inc ? c->x_cnt + 8 : c->x_cnt - 8;
        return;
    }
    c->x_cnt = x_inc ? c->x_start : c->x_end;
    if (y_inc) {
        if (c->y_cnt < c->y_end) c->y_cnt++; else c->y_cnt = c->y_start;
    } else {
        if (c->y_cnt > c->y_end) c->y_cnt--; else c->y_cnt = c->y_start;
    }
}

static EpdRefreshMode ssd_mode_for_ctrl2(uint8_t v, bool custom_lut)
{
    if (custom_lut) return EPD_MODE_GRAY;
    switch (v) {
    case 0xF7: return EPD_MODE_FULL;
    case 0xFC: case 0xC7: case 0xFF: case 0x1C: case 0xDC: return EPD_MODE_FAST;
    case 0xD7: return EPD_MODE_HALF;
    case 0xCC: return EPD_MODE_GRAY;
    case 0xC0: case 0x03: return EPD_MODE_NONE;   /* power on / off only */
    default: return EPD_MODE_OTHER;
    }
}

static uint32_t mode_us(const EpdCore *c, EpdRefreshMode m)
{
    switch (m) {
    case EPD_MODE_FULL: return c->timing.full_us;
    case EPD_MODE_FAST: return c->timing.fast_us;
    case EPD_MODE_HALF: return c->timing.half_us;
    case EPD_MODE_GRAY: return c->timing.gray_us;
    case EPD_MODE_NONE: return c->timing.power_us;
    default: return c->timing.full_us;
    }
}

static void request_busy(EpdCore *c, uint32_t us)
{
    c->busy = true;
    c->busy_request_us = us;
}

static void ssd_master_activation(EpdCore *c)
{
    if (c->ctrl2 & 0x04) {   /* DISPLAY bit: run the waveform */
        EpdRefreshMode m = ssd_mode_for_ctrl2(c->ctrl2, c->custom_lut);
        if (m == EPD_MODE_NONE) m = EPD_MODE_OTHER;
        c->last_mode = m;
        c->last_ctrl2 = c->ctrl2;
        c->refresh_count++;
        epd_core_compose(c);
        request_busy(c, mode_us(c, m));
        if (c->ctrl2 & 0x20 || c->ctrl2 & 0x10) {
            /* load LUT from OTP (0x20) or load temperature (0x10): back to OTP waveform */
            if ((c->ctrl2 & 0x20)) c->custom_lut = false;
        }
    } else {
        /* power / clock only */
        request_busy(c, c->timing.power_us);
    }
}

static void ssd_byte(EpdCore *c, uint8_t b)
{
    if (!c->dc) {
        /* new command */
        trace_cmd(c);
        c->cmd = b;
        c->cmd_len = 0;
        c->cmd_traced = false;
        c->cmds_in++;
        switch (b) {
        case S_SW_RESET:
            ssd_reset_regs(c);
            request_busy(c, c->timing.power_us);
            break;
        case S_MASTER_ACT:
            ssd_master_activation(c);
            break;
        case S_WRITE_RAM_BW: case S_WRITE_RAM_RED:
            break;
        case U_VER: case U_FLG: case U_RMTP:
            c->probe_reads++;   /* not answered: line floats */
            break;
        case S_DRIVER_OUTPUT: case S_GATE_VOLTAGE: case S_SOURCE_VOLTAGE: case S_BOOSTER:
        case S_DEEP_SLEEP: case S_DATA_ENTRY: case S_TEMP_SENSOR: case S_WRITE_TEMP:
        case S_UPDATE_CTRL1: case S_UPDATE_CTRL2: case S_WRITE_VCOM: case S_WRITE_LUT:
        case S_BORDER: case S_RAM_X_RANGE: case S_RAM_Y_RANGE: case S_AUTO_WRITE_BW:
        case S_AUTO_WRITE_RED: case S_RAM_X_CNT: case S_RAM_Y_CNT:
            break;
        default:
            c->unknown_cmds++;
            c->last_unknown_cmd = b;
            break;
        }
        return;
    }
    /* data byte */
    if (c->cmd_len < sizeof(c->arg)) c->arg[c->cmd_len] = b;
    c->cmd_len++;
    switch (c->cmd) {
    case S_WRITE_RAM_BW: ssd_ram_write(c, c->plane0, b); break;
    case S_WRITE_RAM_RED: ssd_ram_write(c, c->plane1, b); break;
    case S_DATA_ENTRY: c->data_entry = b & 7; break;
    case S_RAM_X_RANGE:
        if (c->cmd_len == 2) c->x_start = (c->arg[0] | (c->arg[1] << 8)) & 0x3FF;
        if (c->cmd_len == 4) c->x_end = (c->arg[2] | (c->arg[3] << 8)) & 0x3FF;
        break;
    case S_RAM_Y_RANGE:
        if (c->cmd_len == 2) c->y_start = (c->arg[0] | (c->arg[1] << 8)) & 0x3FF;
        if (c->cmd_len == 4) c->y_end = (c->arg[2] | (c->arg[3] << 8)) & 0x3FF;
        break;
    case S_RAM_X_CNT: if (c->cmd_len == 2) c->x_cnt = (c->arg[0] | (c->arg[1] << 8)) & 0x3FF; break;
    case S_RAM_Y_CNT: if (c->cmd_len == 2) c->y_cnt = (c->arg[0] | (c->arg[1] << 8)) & 0x3FF; break;
    case S_UPDATE_CTRL1: c->ctrl1 = b; break;
    case S_UPDATE_CTRL2: c->ctrl2 = b; break;
    case S_BORDER: c->border = b; break;
    case S_WRITE_LUT: c->custom_lut = true; break;
    case S_AUTO_WRITE_BW:
        /* auto write: pattern in the byte, 0xF7 = fill 0xFF? datasheet: bits select the
         * pattern; the firmware uses 0xF7 to clear to white. Fill the plane white. */
        memset(c->plane0, (b & 0x80) ? 0xFF : 0x00, EPD_PLANE_BYTES);
        request_busy(c, c->timing.power_us);
        break;
    case S_AUTO_WRITE_RED:
        memset(c->plane1, (b & 0x80) ? 0xFF : 0x00, EPD_PLANE_BYTES);
        request_busy(c, c->timing.power_us);
        break;
    case S_DEEP_SLEEP:
        c->deep_sleep = (b & 3) != 0;
        break;
    default:
        break;
    }
}

/* ---- UC8179 / UC8279 ------------------------------------------------------ */
/* Desk unit, CrossPoint 1.6.0 on hardware (docs/device/boot-crosspoint-1.6.0.log):
 *   [XTDET] bus probe VER=00 0F 68 00 00 FLG=13 -> UltraChip
 *   [XTDET] MTP[0x000..0x02F]: A5 A5 1F 20 25 25 3C 00 97 02 02 03 20 02 58 00 ... 0F 00 00 68 ... 05 0A 0F 14 50 5F 64 7F
 *   [XTDET] promoted SSD1677 -> UC8279 800x480 (LUT_VER=68) */
static const uint8_t uc8279_ver[5] = {0x00, 0x0F, 0x68, 0x00, 0x00};
static const uint8_t uc8279_mtp[48] = {
    0xA5, 0xA5, 0x1F, 0x20, 0x25, 0x25, 0x3C, 0x00, 0x97, 0x02, 0x02, 0x03, 0x20, 0x02, 0x58, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0F, 0x00, 0x00, 0x68, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x05, 0x0A, 0x0F, 0x14, 0x50, 0x5F, 0x64, 0x7F,
};
/* A shipping UC8179 was observed returning VER 00 00 01 FF FF (XteinkDetect.cpp comment). */
static const uint8_t uc8179_ver[5] = {0x00, 0x00, 0x01, 0xFF, 0xFF};
static const uint8_t uc_flg = 0x13;

static void uc_prepare_probe(EpdCore *c, uint8_t cmd)
{
    c->probe_reads++;
    c->probe_len = 0;
    switch (cmd) {
    case U_FLG:
        c->probe_buf[0] = uc_flg | (c->busy ? 0x00 : 0x01);   /* BUSY_N bit0 = 1 when idle */
        c->probe_len = 1;
        break;
    case U_VER:
        memcpy(c->probe_buf, c->variant == EPD_UC8279 ? uc8279_ver : uc8179_ver, 5);
        c->probe_len = 5;
        break;
    case U_RMTP:
        /* dummy byte, then MTP[0..47] (the desk unit's dump for UC8279; a UC8179
         * gets the same block with LUT_VER 0x01 at 0x1A) */
        memset(c->probe_buf, 0, sizeof(c->probe_buf));
        c->probe_buf[0] = 0x00;
        memcpy(c->probe_buf + 1, uc8279_mtp, 48);
        if (c->variant == EPD_UC8179) c->probe_buf[1 + 0x1A] = 0x01;
        c->probe_len = 49;
        break;
    }
    c->probe_bit = 0;
}

static void uc_plane_write(EpdCore *c, uint8_t *plane, uint8_t b)
{
    uint32_t total = (uint32_t)c->tres_w / 8 * c->tres_h;
    if (c->partial_in) {
        /* partial window: x in bytes [ptl0/8 .. ptl1/8], rows ptl2..ptl3 */
        uint32_t x0 = c->ptl[0] / 8, x1 = c->ptl[1] / 8;
        uint32_t wbytes = (x1 >= x0) ? (x1 - x0 + 1) : 1;
        uint32_t rows = (c->ptl[3] >= c->ptl[2]) ? (c->ptl[3] - c->ptl[2] + 1) : 1;
        uint32_t row = c->uc_ptr / wbytes, col = c->uc_ptr % wbytes;
        if (row < rows) {
            uint32_t y = c->ptl[2] + row;
            if (y < EPD_UC_GATES && (x0 + col) < EPD_WB) {
                plane[y * EPD_WB + x0 + col] = b;
            }
        }
        c->uc_ptr++;
        return;
    }
    if (c->uc_ptr < total && c->uc_ptr < EPD_UC_PLANE_BYTES) {
        plane[c->uc_ptr] = b;
    }
    c->uc_ptr++;
}

static void uc_byte(EpdCore *c, uint8_t b)
{
    if (!c->dc) {
        trace_cmd(c);
        c->cmd = b;
        c->cmd_len = 0;
        c->cmd_traced = false;
        c->cmds_in++;
        switch (b) {
        case U_DTM1: case U_DTM2:
            c->uc_ptr = 0;
            break;
        case U_PON:
            c->power_on = true;
            request_busy(c, c->timing.power_us);
            break;
        case U_POF:
            c->power_on = false;
            request_busy(c, c->timing.power_us);
            break;
        case U_DRF: {
            EpdRefreshMode m;
            if (c->uc_lut_loaded && (c->psr[0] & 0x20)) {
                m = EPD_MODE_GRAY;
            } else if (c->partial_in) {
                m = EPD_MODE_FAST;
            } else {
                m = (c->last_ctrl2 == 0x5A) ? EPD_MODE_FAST : EPD_MODE_FULL;
            }
            c->last_mode = m;
            c->refresh_count++;
            epd_core_compose(c);
            request_busy(c, mode_us(c, m));
            break;
        }
        case U_PTIN: c->partial_in = true; break;
        case U_PTOUT: c->partial_in = false; break;
        case U_VER: case U_FLG: case U_RMTP:
            uc_prepare_probe(c, b);
            break;
        case U_PSR: case U_PWR: case U_PFS: case U_DSLP: case U_PLL: case U_CDI:
        case U_TRES: case U_GSST: case U_PTL: case U_CCSET: case U_GSCAN: case U_TSSET:
        case U_LUT0: case 0x21: case 0x22: case 0x23: case U_LUT4:
        case U_BTST: case U_TCON: case U_PWS: case U_VDCS: case U_TSC: case U_TSE: case U_LPD: case U_AUTO:
            break;
        default:
            c->unknown_cmds++;
            c->last_unknown_cmd = b;
            break;
        }
        return;
    }
    if (c->cmd_len < sizeof(c->arg)) c->arg[c->cmd_len] = b;
    c->cmd_len++;
    switch (c->cmd) {
    case U_DTM1: uc_plane_write(c, c->plane0, b); break;
    case U_DTM2: uc_plane_write(c, c->plane1, b); break;
    case U_PSR: if (c->cmd_len <= 2) c->psr[c->cmd_len - 1] = b; break;
    case U_TRES:
        if (c->cmd_len == 2) c->tres_w = ((c->arg[0] << 8) | c->arg[1]) & 0x3FF;
        if (c->cmd_len == 4) c->tres_h = ((c->arg[2] << 8) | c->arg[3]) & 0x3FF;
        break;
    case U_PTL:
        if (c->cmd_len == 2) c->ptl[0] = ((c->arg[0] << 8) | c->arg[1]) & 0x3FF & ~7;
        if (c->cmd_len == 4) c->ptl[1] = ((c->arg[2] << 8) | c->arg[3]) & 0x3FF;
        if (c->cmd_len == 6) c->ptl[2] = ((c->arg[4] << 8) | c->arg[5]) & 0x3FF;
        if (c->cmd_len == 8) c->ptl[3] = ((c->arg[6] << 8) | c->arg[7]) & 0x3FF;
        break;
    case U_TSSET: c->last_ctrl2 = b; break;
    case U_LUT0: case 0x21: case 0x22: case 0x23: case U_LUT4: c->uc_lut_loaded = true; break;
    case U_DSLP: if (b == 0xA5) { c->deep_sleep = true; c->power_on = false; } break;
    default: break;
    }
}

void epd_core_byte(EpdCore *c, uint8_t b)
{
    if (!c->cs || c->in_reset) {
        return;
    }
    c->bytes_in++;
    if (c->variant == EPD_SSD1677) {
        ssd_byte(c, b);
    } else {
        uc_byte(c, b);
    }
}

void epd_core_busy_done(EpdCore *c)
{
    trace_cmd(c);   /* the refresh command itself is complete now */
    c->busy = false;
    c->busy_request_us = 0;
}

/* ---- bit-banged probe ----------------------------------------------------- */
int epd_core_line_level(const EpdCore *c)
{
    if (!c->probe_read_active || c->variant == EPD_SSD1677 || !c->cs) {
        return -1;
    }
    int total = c->probe_len * 8;
    if (c->probe_bit >= total) {
        return -1;
    }
    uint8_t byte = c->probe_buf[c->probe_bit / 8];
    return (byte >> (7 - (c->probe_bit % 8))) & 1;
}

int epd_core_sclk_rise(EpdCore *c, int mosi)
{
    if (!c->cs) return -1;
    if (c->probe_read_active) {
        /* the master samples before the rising edge; nothing to shift in */
        return epd_core_line_level(c);
    }
    c->bb_shift = (uint8_t)((c->bb_shift << 1) | (mosi & 1));
    c->bb_nbits++;
    if (c->bb_nbits == 8) {
        uint8_t b = c->bb_shift;
        c->bb_nbits = 0;
        epd_core_byte(c, b);
    }
    return -1;
}

int epd_core_sclk_fall(EpdCore *c)
{
    if (!c->cs || !c->probe_read_active) return -1;
    /* the controller shifts the next bit out on the falling edge */
    c->probe_bit++;
    return epd_core_line_level(c);
}

/* ---- compose -------------------------------------------------------------- */
void epd_core_compose(EpdCore *c)
{
    if (c->variant == EPD_SSD1677) {
        bool gray = c->custom_lut && !(c->ctrl1 & 0x40);
        c->image_gray_approx = gray;
        for (int r = 0; r < EPD_H; r++) {
            /* gates are physically reversed: RAM row 479 is the top of the glass */
            const uint8_t *bw = &c->plane0[(EPD_H - 1 - r) * EPD_WB];
            const uint8_t *red = &c->plane1[(EPD_H - 1 - r) * EPD_WB];
            uint8_t *dst = &c->image[r * EPD_W];
            for (int x = 0; x < EPD_W; x++) {
                int bit = 7 - (x & 7);
                int b = (bw[x >> 3] >> bit) & 1;
                if (gray) {
                    int rr = (red[x >> 3] >> bit) & 1;
                    /* two planes -> four levels (approximation): bw=1,red=1 white ... bw=0,red=0 black */
                    static const uint8_t lv[4] = {0, 85, 170, 255};
                    dst[x] = lv[(b << 1) | rr];
                } else {
                    dst[x] = b ? 255 : 0;
                }
            }
        }
    } else {
        /* UC: the NEW plane (DTM2) is what the panel shows after DRF, 1 = white.
         * Visible gates are 120..599 of the 600-gate scan (Uc8279X4Driver gateOffset). */
        bool gray = c->uc_lut_loaded && (c->psr[0] & 0x20);
        c->image_gray_approx = gray;
        int off = (c->tres_h > EPD_H) ? (c->tres_h - EPD_H) : 0;   /* 120 for 600 gates */
        for (int r = 0; r < EPD_H; r++) {
            const uint8_t *nw = &c->plane1[(off + r) * EPD_WB];
            const uint8_t *od = &c->plane0[(off + r) * EPD_WB];
            uint8_t *dst = &c->image[r * EPD_W];
            for (int x = 0; x < EPD_W; x++) {
                int bit = 7 - (x & 7);
                int n = (nw[x >> 3] >> bit) & 1;
                if (gray) {
                    /* AA planes are streamed inverted: plane0 = ~(base|lsb), plane1 = ~(plane0 ^ msb).
                     * Approximate: both 0 -> white(ish)?? keep it simple: level from the two bits. */
                    int o = (od[x >> 3] >> bit) & 1;
                    static const uint8_t lv[4] = {255, 170, 85, 0};
                    dst[x] = lv[(o << 1) | n];
                } else {
                    dst[x] = n ? 255 : 0;
                }
            }
        }
    }
    c->image_dirty = true;
}
