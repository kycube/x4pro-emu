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
#include "wire.h"
#include <stdlib.h>
#include <string.h>

static int parse_line(const char *line, int *op, unsigned long *len, uint8_t *head, int *nhead)
{
    const char *p;
    *nhead = 0;
    if ((p = strstr(line, "[EPD] c="))) {
        unsigned c;
        if (sscanf(p, "[EPD] c=%x len=%lu", &c, len) != 2) return 0;
        *op = c;
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
        *op = c;
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
    EpdVariant v;
    if (!strcmp(argv[1], "ssd1677")) v = EPD_SSD1677;
    else if (!strcmp(argv[1], "uc8179")) v = EPD_UC8179;
    else if (!strcmp(argv[1], "uc8279")) v = EPD_UC8279;
    else { fprintf(stderr, "replay: unknown variant '%s' (ssd1677|uc8179|uc8279)\n", argv[1]); return 2; }
    FILE *f = fopen(argv[2], "r");
    if (!f) { perror(argv[2]); return 2; }
    EpdCore *c = calloc(1, sizeof(*c));
    epd_core_init(c, v);
    uint8_t *buf = NULL;
    unsigned long cap = 0;
    char line[1024];
    int n = 0;
    while (fgets(line, sizeof(line), f)) {
        int command, nhead; unsigned long len; uint8_t head[8];
        if (!parse_line(line, &command, &len, head, &nhead)) continue;
        n++;
        cmd(c, (uint8_t)command);
        if (len) {
            if (len > cap) {
                uint8_t *nb = realloc(buf, len);
                if (!nb) { perror("realloc"); return 2; }
                buf = nb; cap = len;
            }
            memset(buf, 0xFF, len);
            memcpy(buf, head, (unsigned long)nhead < len ? (size_t)nhead : len);
            datn(c, buf, len);
        }
        if (c->busy_request_us) epd_core_busy_done(c);
    }
    fclose(f);
    free(buf);
    unsigned long black = 0;
    for (int i = 0; i < EPD_W * EPD_H; i++) if (c->image[i] < 128) black++;
    printf("commands=%d refreshes=%llu last_mode=%s unknown=%llu(last 0x%02x) probe_reads=%llu black_pixels=%lu\n",
           n, (unsigned long long)c->refresh_count, epd_mode_name(c->last_mode),
           (unsigned long long)c->unknown_cmds, c->last_unknown_cmd, (unsigned long long)c->probe_reads, black);
    int rc = c->unknown_cmds ? 1 : 0;
    free(c);
    return rc;
}
