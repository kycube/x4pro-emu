/* Replay a recorded panel command stream into the core and summarise.
 *
 *   replay <variant> <fixture>
 *
 * Fixture lines (from the device, CrossPoint built with FREEINK_EPD_TRACE):
 *   [t] [EPD] c=13 len=60000 FF FF FF FF FF FF FF FF
 * or from the emulator (x4emu --trace-epd, JSON lines):
 *   {"t_us":..,"cmd":"0x13","len":60000,"data":"ff ff ..","more":true}
 * Only the opcode, the data length and the first bytes are known, so the
 * plane data is replayed as the first bytes followed by 0xFF fill (white).
 * Exit status 1 if the core saw an unknown command.
 */
#include "epd_core.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int parse_line(const char *line, int *cmd, unsigned long *len, uint8_t *head, int *nhead)
{
    const char *p;
    *nhead = 0;
    if ((p = strstr(line, "[EPD] c="))) {
        unsigned c;
        if (sscanf(p, "[EPD] c=%x len=%lu", &c, len) != 2) return 0;
        *cmd = c;
        p = strstr(p, "len=");
        while (*p && *p != ' ') p++;
        while (*p == ' ' && *nhead < 8) {
            unsigned b;
            if (sscanf(p, " %x", &b) != 1) break;
            head[(*nhead)++] = b;
            p++;
            while (*p && *p != ' ') p++;
        }
        return 1;
    }
    if ((p = strstr(line, "\"cmd\":\"0x"))) {
        unsigned c;
        if (sscanf(p, "\"cmd\":\"0x%x\"", &c) != 1) return 0;
        *cmd = c;
        const char *l = strstr(line, "\"len\":");
        *len = l ? strtoul(l + 6, NULL, 10) : 0;
        const char *d = strstr(line, "\"data\":\"");
        if (d) {
            d += 8;
            while (*d && *d != '"' && *nhead < 8) {
                unsigned b;
                if (sscanf(d, "%x", &b) != 1) break;
                head[(*nhead)++] = b;
                while (*d && *d != ' ' && *d != '"') d++;
                if (*d == ' ') d++;
            }
        }
        return 1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    if (argc < 3) { fprintf(stderr, "usage: replay ssd1677|uc8179|uc8279 fixture\n"); return 2; }
    EpdVariant v = EPD_UC8279;
    if (!strcmp(argv[1], "ssd1677")) v = EPD_SSD1677;
    else if (!strcmp(argv[1], "uc8179")) v = EPD_UC8179;
    FILE *f = fopen(argv[2], "r");
    if (!f) { perror(argv[2]); return 2; }
    EpdCore *c = calloc(1, sizeof(*c));
    epd_core_init(c, v);
    char line[1024];
    int n = 0;
    while (fgets(line, sizeof(line), f)) {
        int cmd, nhead; unsigned long len; uint8_t head[8];
        if (!parse_line(line, &cmd, &len, head, &nhead)) continue;
        n++;
        epd_core_set_dc(c, false); epd_core_set_cs(c, true);
        epd_core_byte(c, (uint8_t)cmd);
        epd_core_set_cs(c, false);
        if (len) {
            epd_core_set_dc(c, true); epd_core_set_cs(c, true);
            for (unsigned long i = 0; i < len; i++) {
                epd_core_byte(c, i < (unsigned long)nhead ? head[i] : 0xFF);
            }
            epd_core_set_cs(c, false);
        }
        if (c->busy_request_us) epd_core_busy_done(c);
    }
    fclose(f);
    unsigned long black = 0;
    for (int i = 0; i < EPD_W * EPD_H; i++) if (c->image[i] < 128) black++;
    printf("commands=%d refreshes=%llu last_mode=%s unknown=%llu(last 0x%02x) probe_reads=%llu black_pixels=%lu\n",
           n, (unsigned long long)c->refresh_count, epd_mode_name(c->last_mode),
           (unsigned long long)c->unknown_cmds, c->last_unknown_cmd, (unsigned long long)c->probe_reads, black);
    int rc = c->unknown_cmds ? 1 : 0;
    free(c);
    return rc;
}
