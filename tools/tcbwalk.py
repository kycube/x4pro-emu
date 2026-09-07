#!/usr/bin/env python3
"""FreeRTOS task walk over an ESP32-S3 DRAM dump (no symbols needed): what every task waits on.

  x4emu --name N qmp '{"execute":"pmemsave","arguments":{"val":1070170112,"size":491520,"filename":"/abs/dram.bin"}}'
  tools/tcbwalk.py dram.bin [--base 0x3FC88000] [--rom images/rom/esp32s3_rev0_rom.nm]
                   [--app images/device/stock-app0-7.2.4.bin] [--frames 24] [--region psram.bin:0x3C6C0000]

TCBs are found by their two list items owning themselves (pvOwner at TCB+16 and TCB+36 == TCB); the
layout is ESP-IDF's FreeRTOS: pxTopOfStack +0, xStateListItem +4, xEventListItem +24 (value, next,
prev, owner, container), uxPriority +44, pxStack +48, pcTaskName +52, xCoreID +68, pxEndOfStack +72.
A task whose event item sits in a list is blocked on a queue/semaphore: the list is the queue's
xTasksWaitingToSend (+16) or xTasksWaitingToReceive (+36); the queue's uxMessagesWaiting +56,
uxLength +60, uxItemSize +64, mutex holder +8 (mutex types) and ucQueueType (+80 with the trace
facility and queue sets on: 0 queue, 1 mutex, 2 counting, 3 binary, 4 recursive) are printed.
Ready lists are recognised from the IDLE tasks (pxReadyTasksLists[p] = base + 20 p).
The backtrace starts at the saved frame (solicited frame when word 0 is 0: PC +4, A0 +16, A1 +20;
interrupt frame otherwise: PC +4, A0 +12, A1 +16) and follows the register windows: A0 at SP-16,
caller SP at SP-12, return addresses carry the window size in bits 31:30. ROM PCs get names from the
nm table, app PCs the segment and the nearest preceding `entry a1` (function start heuristic).
"""
import argparse, bisect, struct, sys


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dump'); ap.add_argument('--base', type=lambda s: int(s, 0), default=0x3FC88000)
    ap.add_argument('--rom'); ap.add_argument('--app'); ap.add_argument('--frames', type=int, default=24)
    ap.add_argument('--task', help='only this task name')
    ap.add_argument('--frame', help='PC,A0,SP of a live CPU (info registers): backtrace that frame through the dump instead of the tasks')
    ap.add_argument('--region', action='append', default=[], help='FILE:BASE of another dump (e.g. PSRAM task stacks: pmemsave 0x3C6C0000 0x200000), repeatable')
    a = ap.parse_args()
    d = open(a.dump, 'rb').read(); base = a.base; end = base + len(d)
    regions = [(base, d)]
    for r in a.region:
        f, b = r.rsplit(':', 1)
        regions.append((int(b, 0), open(f, 'rb').read()))

    def u32(addr):
        for rb, rd in regions:
            if rb <= addr <= rb + len(rd) - 4:
                return struct.unpack_from('<I', rd, addr - rb)[0]
        return None

    # symbols
    rom = []
    if a.rom:
        for line in open(a.rom):
            p = line.split()
            if len(p) >= 3 and p[1] in 'TtWw':
                rom.append((int(p[0], 16), p[2]))
        rom.sort()
    rom_addrs = [x for x, _ in rom]
    segs = []
    if a.app:
        img = open(a.app, 'rb').read()
        if img[0] == 0xE9:
            n, off = img[1], 24
            for _ in range(n):
                la, ln = struct.unpack_from('<II', img, off); off += 8
                segs.append((la, ln, img[off:off + ln])); off += ln

    def sym(pc):
        if rom and 0x40000000 <= pc < 0x40060000:
            i = bisect.bisect_right(rom_addrs, pc) - 1
            if i >= 0:
                return f'{rom[i][1]}+0x{pc - rom[i][0]:x}'
        for la, ln, data in segs:
            if la <= pc < la + ln:
                o = pc - la
                # nearest preceding `entry a1, N` (bytes 36 x1 ..), functions are 4-aligned
                s = o & ~3
                while s > 0 and not (data[s] == 0x36 and (data[s + 1] & 0x0F) == 0x01):
                    s -= 4
                return f'app func@0x{la + s:08x}+0x{o - s:x} (seg 0x{la:08x})'
        if 0x3C000000 <= pc < 0x3E000000 or 0x3F000000 <= pc < 0x40000000:
            return 'data'
        return ''

    # find TCBs
    tcbs = []
    for off in range(0, len(d) - 80, 4):
        t = base + off
        if u32(t + 16) == t and u32(t + 36) == t:
            name = d[off + 52:off + 68].split(b'\0')[0]
            if name and all(32 <= c < 127 for c in name):
                tcbs.append(t)
    info = {}
    for t in tcbs:
        info[t] = dict(top=u32(t), prio=u32(t + 44), stack=u32(t + 48), name=d[t - base + 52:t - base + 68].split(b'\0')[0].decode(),
                       core=u32(t + 68), stack_end=u32(t + 72), st_list=u32(t + 20), st_val=u32(t + 4),
                       ev_list=u32(t + 40), ev_val=u32(t + 24))
    ready_base = None
    for t, i in info.items():
        if i['name'].startswith('IDLE') and i['prio'] == 0:
            ready_base = i['st_list']; break
    lists = {}
    for i in info.values():
        lists[i['st_list']] = lists.get(i['st_list'], 0) + 1

    def list_name(L, prio):
        if ready_base is not None and L >= ready_base and (L - ready_base) % 20 == 0 and (L - ready_base) // 20 < 32:
            p = (L - ready_base) // 20
            return f'ready[{p}]' + ('' if p == prio else f' (prio {prio}?)')
        return f'list 0x{L:08x} ({lists.get(L, 0)} tasks)'

    def queue_of(C):
        for q, kind in ((C - 16, 'send'), (C - 36, 'recv')):
            if u32(q + 16 + 8) == 0xFFFFFFFF and u32(q + 36 + 8) == 0xFFFFFFFF:
                typ = d[q - base + 80] if base <= q + 80 < end else -1
                tname = {0: 'queue', 1: 'mutex', 2: 'counting-sem', 3: 'binary-sem', 4: 'recursive-mutex'}.get(typ, f'type{typ}')
                holder = u32(q + 8)
                h = ''
                if typ in (1, 4) and holder in info:
                    h = f' holder={info[holder]["name"]}'
                elif typ in (1, 4):
                    h = f' holder=0x{holder:08x}'
                return (f'waits to {kind} on 0x{q:08x} {tname} msgs={u32(q + 56)} len={u32(q + 60)} item={u32(q + 64)}{h}'
                        f' send-waiters={u32(q + 16)} recv-waiters={u32(q + 36)}')
        return f'event list 0x{C:08x} (not a queue list)'

    if a.frame:
        pc, a0, sp = (int(x, 0) for x in a.frame.split(','))
        print(f'live frame pc 0x{pc:08x} a0 0x{a0:08x} sp 0x{sp:08x}')
        for n in range(a.frames):
            print(f'     #{n:2d} 0x{pc:08x}  sp 0x{sp:08x}  {sym(pc)}')
            if not a0 or u32(sp - 16) is None:
                break
            pc = (a0 & 0x3FFFFFFF) | 0x40000000
            a0, sp = u32(sp - 16), u32(sp - 12)
            if sp is None:
                break
        return
    print(f'{len(tcbs)} tasks (ready lists base {"0x%08x" % ready_base if ready_base else "?"})')
    for t in sorted(tcbs, key=lambda t: (info[t]['core'], -info[t]['prio'])):
        i = info[t]
        if a.task and i['name'] != a.task:
            continue
        state = list_name(i['st_list'], i['prio'])
        print(f"\n== {i['name']:16s} tcb 0x{t:08x} core {i['core']} prio {i['prio']:2d} stack 0x{i['stack']:08x}..0x{i['stack_end']:08x} top 0x{i['top']:08x}")
        print(f'   state: {state}, item value {i["st_val"]}' + (f' (delayed until tick {i["st_val"]})' if not state.startswith('ready') else ''))
        if i['ev_list']:
            print(f'   {queue_of(i["ev_list"])}')
        top = i['top']
        if top is None or u32(top) is None:
            print('   (stack frame not in dump)'); continue
        if u32(top) == 0:
            pc, a0, sp = u32(top + 4), u32(top + 16), u32(top + 20); kind = 'solicited'
        else:
            pc, a0, sp = u32(top + 4), u32(top + 12), u32(top + 16); kind = 'interrupt'
        print(f'   frame ({kind}) pc 0x{pc:08x} a0 0x{a0:08x} sp 0x{sp:08x}')
        seen = 0
        while seen < a.frames and pc:
            print(f'     #{seen:2d} 0x{pc:08x}  {sym(pc)}')
            seen += 1
            if not a0 or sp is None or not (i['stack'] <= sp <= i['stack_end'] + 64):
                break
            pc = (a0 & 0x3FFFFFFF) | 0x40000000
            a0n, spn = u32(sp - 16), u32(sp - 12)
            if a0n is None:
                break
            a0, sp = a0n, spn


if __name__ == '__main__':
    main()
