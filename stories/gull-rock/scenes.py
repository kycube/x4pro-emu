"""The Keeper of Gull Rock: one function per illustration, 160x100 logical pixels, shown at 3x.

`tools/gamebook.py plates stories/gull-rock OUT` renders them; `SCENES` maps the names story.txt
uses in `@image` to the functions.
"""
import math
import random
from pixart import Scene               # tools/pixart.py; tools/gamebook.py puts tools/ on sys.path


def storm():
    s = Scene()
    # sky: near black, a faint lift towards the horizon
    s.vgrad(0, 58, 0, 6)
    s.vgrad(58, 70, 6, 40)
    # the beam: bright at the lantern, falling off through the patterns
    s.wedge((104, 22), (-6, 0), (-6, 34), 255, 96, steps=70)
    s.wedge((104, 22), (-6, 8), (-6, 26), 255, 150, steps=70)
    s.wedge((104, 22), (170, 16), (170, 26), 255, 80, steps=30)
    # cloud edges caught by the beam
    # sea: black, with a lighter swell line at the horizon
    s.rect(0, 70, 159, 99, 0)
    s.vgrad(70, 74, 60, 0)
    s.quantise()
    # rock: solid black mass with a few lit edges
    s.black([(78, 82), (86, 72), (94, 66), (114, 65), (124, 69), (132, 76), (142, 84), (76, 84)])
    s.white([(95, 66), (100, 64)], 'line'); s.white([(115, 66), (122, 69)], 'line')
    s.white([(125, 71), (130, 75)], 'line')
    # tower: white body, left half in shadow (50% pattern), black outline and bands
    body = [(97, 66), (100, 27), (110, 27), (113, 66)]
    s.white(body)
    for y in range(28, 66):
        xl = 97 + (66 - y) * 3 / 39
        mid = 105
        for x in range(int(xl) + 1, mid - 1):
            if (x + y) % 2 == 0:
                s.black((x, y), 'point')
    s.black([(97, 66), (100, 27)], 'line'); s.black([(113, 66), (110, 27)], 'line')
    for y in (38, 50):
        xl = 97 + (66 - y) * 3 / 39
        s.black([(int(xl), y), (int(113 - (66 - y) * 3 / 39), y + 2)], 'rect')
    s.black([(104, 58), (106, 65)], 'rect')                    # door
    s.black([(104, 34), (105, 35)], 'rect')                    # window
    s.black([(98, 27), (112, 28)], 'rect')                     # gallery
    s.black([(98, 25), (98, 27)], 'line'); s.black([(112, 25), (112, 27)], 'line')
    s.black([(98, 25), (112, 25)], 'line')                     # rail
    s.black([(100, 24), (110, 18)], 'rect')                    # lantern frame
    s.white([(101, 19), (109, 23)], 'rect')                    # the light
    s.black([(105, 19), (105, 23)], 'line')                    # glazing bar
    s.black([(99, 17), (111, 17)], 'line')                     # roof
    s.black([(101, 16), (109, 16)], 'line'); s.black([(103, 15), (107, 15)], 'line')
    s.black([(105, 11), (105, 14)], 'line')                    # vane
    # glow around the lantern
    for (x, y) in [(99, 20), (111, 20), (99, 22), (111, 22), (104, 14), (106, 14)]:
        s.white((x, y), 'point')
    # rain: only where the light catches it
    rnd = random.Random(7)
    for _ in range(90):
        x, y = rnd.randrange(0, 160), rnd.randrange(2, 44)
        if 94 < x < 118:
            continue
        s.black([(x, y), (x + 1, y + 3)], 'line')
    # sea: long swell lines and breaking crests, structured rather than noisy
    for row, (y, step, ln) in enumerate([(76, 13, 6), (80, 11, 7), (85, 9, 8), (91, 8, 10), (97, 7, 11)]):
        off = (row * 5) % step
        for x in range(-ln + off, 160, step + ln):
            s.white([(x, y), (x + ln, y)], 'line')
            s.white([(x + ln, y), (x + ln + 2, y - 1)], 'line')
    # surf exploding against the rock: white mass with a patterned fringe
    s.white([(72, 84), (78, 76), (84, 78), (88, 72), (92, 77), (96, 74), (98, 80), (104, 78), (108, 84)])
    s.white([(118, 84), (124, 78), (128, 80), (134, 74), (140, 79), (146, 76), (150, 84)])
    for (x, y) in [(76, 73), (82, 74), (87, 69), (93, 71), (131, 71), (137, 72), (144, 73), (80, 71), (140, 70)]:
        s.white([(x - 1, y - 1), (x + 1, y + 1)], 'ellipse')
    s.black([(70, 86), (152, 88)], 'rect')
    # the boat, small, pulling away across a patch of sea lit by the beam
    s.pattern([(0, 74), (64, 73), (58, 79), (30, 84), (0, 86)], 120)
    s.pattern([(6, 76), (48, 75), (40, 79), (8, 81)], 190)
    s.black([(22, 77), (36, 77), (34, 80), (24, 80)])           # hull
    s.black([(26, 74), (27, 77)], 'rect')                         # Tam
    s.black([(26, 73), (27, 73)], 'line')
    s.black([(32, 75), (33, 77)], 'rect')
    s.black([(25, 78), (19, 81)], 'line'); s.black([(28, 78), (34, 82)], 'line')   # oars
    s.white([(36, 80), (44, 80)], 'line'); s.white([(14, 81), (21, 81)], 'line')   # wake
    return s


def cottage():
    """The keeper's cottage: cold hearth, the half-eaten meal, the log, footprints to the trapdoor."""
    s = Scene(tone=0)
    s.radial(78, 50, 58, 38, 240, 0)                 # one candle; the room falls away into black
    s.radial(140, 22, 14, 18, 90, 0, steps=8)        # grey storm light at the window
    s.quantise()
    # the window, back right: four panes, rain
    s.black([(130, 8), (150, 36)], 'rect')
    s.pattern([(131, 9), (149, 35)], 70, 'rect')
    s.black([(140, 8), (140, 36)], 'line'); s.black([(130, 22), (150, 22)], 'line')
    s.white([(130, 8), (150, 36)], 'line'); s.white([(129, 37), (151, 37)], 'line')
    for x in (133, 137, 144, 147):
        s.white([(x, 11), (x, 14)], 'line'); s.white([(x - 1, 26), (x - 1, 30)], 'line')
    # the hearth, left: a stone arch in faint light, black inside, a dead grate
    s.pattern([(4, 18), (36, 62)], 60, 'rect')
    s.black([(9, 28), (31, 62)], 'rect')
    s.black([(9, 28), (31, 22)], 'ellipse')
    s.white([(12, 54), (28, 54)], 'line')
    for x in range(13, 29, 4):
        s.white([(x, 50), (x, 54)], 'line')
    s.white([(2, 16), (38, 17)], 'rect')             # mantel catching the candle
    # the floor line
    s.white([(0, 62), (159, 62)], 'line')
    # the table, white top lit from the candle
    s.white([(48, 54), (108, 54), (114, 60), (42, 60)])
    s.black([(42, 60), (114, 61)], 'rect')
    for (x0, x1, y1) in [(46, 48, 84), (108, 110, 84), (56, 57, 76), (99, 100, 76)]:
        s.black([(x0, 61), (x1, y1)], 'rect')
    s.pattern([(48, 62), (108, 68)], 140, 'rect')    # the lit floor under the table
    s.black([(48, 62), (108, 62)], 'line')
    # plate, fish, bread, log on the table
    s.black([(52, 55), (68, 59)], 'ellipse'); s.white([(53, 56), (67, 58)], 'ellipse')
    s.black([(56, 57), (64, 57)], 'line'); s.black([(63, 56), (65, 58)], 'line')
    s.black([(70, 55), (75, 58)], 'ellipse')
    s.black([(84, 55), (104, 59)], 'rect'); s.white([(85, 55), (93, 58)], 'rect'); s.white([(95, 55), (103, 58)], 'rect')
    for y in (56, 57):
        s.black([(86, y), (91, y)], 'line'); s.black([(96, y), (102, y)], 'line')
    # the candle
    s.white([(77, 42), (79, 53)], 'rect'); s.black([(76, 42), (76, 53)], 'line'); s.black([(80, 42), (80, 53)], 'line')
    s.white([(77, 36), (79, 41)], 'ellipse'); s.white((78, 35), 'point'); s.black((78, 40), 'point')
    s.black([(74, 53), (82, 54)], 'rect')
    # the chair, pushed back, half in the light
    s.black([(120, 30), (123, 82)], 'rect'); s.black([(120, 30), (127, 33)], 'rect')
    s.white([(123, 34), (123, 80)], 'line')
    s.black([(118, 58), (140, 61)], 'rect'); s.white([(118, 57), (138, 57)], 'line')
    s.black([(136, 61), (138, 84)], 'rect'); s.black([(126, 61), (128, 80)], 'rect')
    # wet footprints, bare, from the table to the trapdoor: white glints on the dark boards
    for i, (x, y) in enumerate([(80, 72), (90, 75), (98, 80), (108, 83), (116, 88), (126, 90)]):
        s.white([(x, y), (x + 2, y + 2)], 'ellipse')
        s.white((x + (2 if i % 2 else 0), y - 2), 'point'); s.white((x + 1, y - 2), 'point')
    # the trapdoor, bottom right: outlined, its iron ring beaded with water
    s.white([(132, 88), (158, 99)], 'rect'); s.black([(133, 89), (157, 99)], 'rect')
    s.pattern([(134, 90), (156, 99)], 50, 'rect')
    s.white([(141, 92), (147, 97)], 'ellipse'); s.black([(142, 93), (146, 96)], 'ellipse')
    s.white((148, 93), 'point'); s.white((140, 96), 'point')
    return s


def log():
    """The logbook, close: two pages of failing handwriting by a guttering candle."""
    s = Scene(tone=0)
    s.radial(128, 18, 120, 70, 255, 0)
    s.quantise()
    # the book, open, slightly turned
    s.white([(14, 30), (78, 24), (80, 92), (18, 96)])            # left page
    s.white([(82, 24), (146, 28), (142, 94), (80, 92)])          # right page
    s.black([(14, 30), (78, 24), (80, 92), (18, 96)], 'line')
    s.pattern([(74, 24), (86, 93)], 110, 'rect')                 # the gutter's shadow
    s.white([(78, 24), (80, 92)], 'line'); s.white([(82, 24), (80, 92)], 'line')
    s.black([(14, 96), (80, 93), (144, 96), (144, 98), (80, 95), (14, 98)])   # page edges
    s.pattern([(18, 97), (142, 99)], 140, 'rect')
    rnd = random.Random(5)
    # handwriting: rows of word-dashes, steady on the left page, less steady on the right
    for page, (x0, x1, wob) in enumerate([(20, 72, 0), (88, 138, 1)]):
        for row, y in enumerate(range(32, 90, 6)):
            x = x0 + (4 if row == 0 else 0)
            end = x1 - (rnd.randrange(0, 14) if page == 1 and row > 6 else 0)
            if page == 1 and row == 9:
                end = x0 + 22                                    # the last line breaks off
            while x < end:
                ln = rnd.randrange(3, 9)
                dy = rnd.choice((0, 0, 1)) if wob and row > 4 else 0
                s.black([(x, y + dy), (min(x + ln, end), y)], 'line')
                if rnd.random() < 0.3:
                    s.black((x + 1, y - 1), 'point')
                x += ln + 2
    # on the right page, the keeper has drawn the light under the water
    for i in range(5):
        s.black([(118 + i, 70 - (i % 2)), (119 + i, 70 - ((i + 1) % 2))], 'line')
    s.black([(116, 66), (128, 74)], 'ellipse'); s.white([(117, 67), (127, 73)], 'ellipse')
    s.pattern([(119, 68), (125, 72)], 60, 'ellipse')
    # the candle, top right, nearly burnt down
    s.white([(124, 4), (131, 14)], 'rect'); s.black([(123, 4), (123, 14)], 'line'); s.black([(132, 4), (132, 14)], 'line')
    s.black([(120, 14), (135, 17)], 'rect')
    s.white([(126, 0), (129, 3)], 'ellipse'); s.black((127, 3), 'point')
    s.white([(124, 7), (124, 11)], 'line')                       # a drip of wax
    # the pen, laid across the left page
    s.black([(30, 88), (66, 70)], 'line', 2); s.white([(31, 87), (64, 70)], 'line')
    s.black([(26, 90), (30, 88)], 'line')
    return s


def stair():
    """Straight up the tower: the iron stair winding to the lamp room, a disc of light at the top."""
    s = Scene(tone=0)
    cx, cy = 80, 48
    s.radial(cx, cy, 22, 20, 255, 40, steps=14)
    s.quantise()
    # steps on a tightening spiral: each a wedge, lit nearer the top
    k = 0.11
    th, step = 0.0, 0.42
    while True:
        r0 = 70 * math.exp(-k * th)
        if r0 < 9:
            break
        r1 = r0 * 0.72
        a0, a1 = th, th + step * 0.82
        pts = [(cx + r0 * math.cos(a0), cy + r0 * math.sin(a0) * 0.82),
               (cx + r0 * math.cos(a1), cy + r0 * math.sin(a1) * 0.82),
               (cx + r1 * math.cos(a1), cy + r1 * math.sin(a1) * 0.82),
               (cx + r1 * math.cos(a0), cy + r1 * math.sin(a0) * 0.82)]
        light = 255 if r0 < 26 else (190 if r0 < 42 else 120)
        if light == 255:
            s.white(pts)
        else:
            s.pattern(pts, light)
        s.black([pts[1], pts[2]], 'line')
        th += step
    # the hand rail: a white spiral just inside the wall
    prev = None
    th = 0.0
    while True:
        r = 74 * math.exp(-k * th)
        if r < 11:
            break
        p = (cx + r * math.cos(th), cy + r * math.sin(th) * 0.82)
        if prev:
            s.white([prev, p], 'line')
        prev = p
        th += 0.08
    # the lamp room's light, a white disc with the lens's ribs
    s.white([(cx - 8, cy - 7), (cx + 8, cy + 7)], 'ellipse')
    for d in (-4, 0, 4):
        s.black([(cx + d, cy - 6), (cx + d, cy + 6)], 'line')
    return s


def lamp():
    """The lamp room: the great lens like a beehive of ice; a ship's lights through the rain."""
    s = Scene(tone=0)
    s.vgrad(0, 100, 10, 40)
    s.quantise()
    # the glazing: tall panes with brass mullions, rain on the glass
    for x in range(0, 161, 32):
        s.white([(x, 0), (x + 1, 76)], 'rect')
    rnd = random.Random(9)
    for _ in range(80):
        x, y = rnd.randrange(0, 160), rnd.randrange(0, 70)
        s.white([(x, y), (x, y + rnd.randrange(2, 6))], 'line')
    # the ship, far off to the south: three small lights low on the horizon
    s.white([(20, 60), (60, 60)], 'line')                        # a line of surf on the reef
    for (x, y) in [(132, 56), (138, 55), (136, 52)]:
        s.white([(x, y), (x + 1, y + 1)], 'rect')
    s.black([(0, 76), (159, 99)], 'rect')                        # the gallery wall below the glass
    s.white([(0, 76), (159, 77)], 'rect')
    # the lens: stacked prism rings, glinting where they catch your match
    cx = 80
    for i, y in enumerate(range(10, 84, 5)):
        hw = 26 - abs(y - 46) * 0.32
        s.black([(cx - hw - 1, y - 1), (cx + hw + 1, y + 4)], 'ellipse')
        s.pattern([(cx - hw, y), (cx + hw, y + 3)], 150 if i % 2 else 200, 'ellipse')
        s.white([(cx - hw * 0.6, y + 1), (cx - hw * 0.2, y + 1)], 'line')
    s.white([(cx - 6, 40), (cx + 6, 52)], 'ellipse')             # the bullseye
    s.black([(cx - 3, 43), (cx + 3, 49)], 'ellipse')
    s.white([(cx - 1, 45), (cx + 1, 47)], 'rect')
    s.black([(cx - 30, 84), (cx + 30, 90)], 'rect')              # the pedestal
    s.pattern([(cx - 28, 85), (cx + 28, 89)], 120, 'rect')
    s.black([(cx - 10, 90), (cx + 10, 99)], 'rect')
    # your hand and the match, bottom right
    s.black([(118, 86), (134, 99)], 'rect'); s.pattern([(119, 87), (133, 98)], 90, 'rect')
    s.black([(128, 80), (130, 86)], 'line'); s.white([(127, 76), (131, 80)], 'ellipse')
    return s


def beam():
    """From the gallery: the beam laid across the sea, the ship turning hard away from the reef."""
    s = Scene(tone=0)
    s.vgrad(0, 56, 0, 14)
    s.rect(0, 56, 159, 99, 0)
    # the beam falls from the lantern above you down onto the sea around the ship
    s.wedge((-10, -10), (176, 30), (146, 76), 255, 110, steps=80)
    s.wedge((-10, -10), (176, 36), (152, 70), 255, 200, steps=80)
    s.poly([(88, 12), (160, 10), (160, 64), (90, 64)], 255)   # solid light behind the ship
    s.radial(124, 38, 44, 32, 255, 200, steps=10)
    s.quantise()
    rnd = random.Random(21)
    for _ in range(110):                              # rain, black inside the light
        x, y = rnd.randrange(0, 160), rnd.randrange(0, 70)
        if 86 < x < 160 and y < 66:
            continue
        s.black([(x, y), (x + 1, y + 3)], 'line')
    s.white([(0, 56), (159, 56)], 'line')            # the horizon
    # the ship, in the light: hull heeled, three masts, sails reefed hard
    s.black([(96, 54), (142, 51), (137, 59), (101, 60)])
    for (x, top) in [(106, 22), (118, 18), (130, 26)]:
        s.black([(x, 54), (x + 3, top)], 'line', 2)
        s.black([(x - 5, top + 6), (x + 8, top + 4), (x + 7, top + 12), (x - 4, top + 14)])
        s.black([(x - 4, top + 18), (x + 8, top + 16), (x + 7, top + 22), (x - 3, top + 24)])
    s.black([(142, 51), (154, 44)], 'line')          # bowsprit and stays
    s.black([(154, 44), (121, 18)], 'line'); s.black([(96, 54), (106, 22)], 'line')
    s.white([(102, 58), (136, 57)], 'line')          # waterline in the beam
    # the wake curling white as she swings away
    s.white([(98, 61), (86, 64), (72, 66), (58, 66)], 'line', 2)
    # sea: swell lines, brighter where the beam lies
    for row, (y, step, ln) in enumerate([(64, 12, 6), (70, 10, 8), (77, 9, 9), (85, 8, 11)]):
        for x in range((row * 4) % step - ln, 160, step + ln):
            s.white([(x, y), (x + ln, y)], 'line')
    # the reef, foreground left: black teeth and surf
    s.black([(18, 99), (24, 84), (30, 88), (36, 79), (44, 90), (50, 85), (60, 99)])
    for (x, y) in [(22, 83), (29, 86), (35, 77), (42, 86), (50, 83), (57, 92)]:
        s.white([(x - 2, y - 2), (x + 2, y + 1)], 'ellipse')
    # the gallery rail you stand at
    s.black([(0, 92), (159, 93)], 'rect'); s.white([(0, 91), (159, 91)], 'line')
    for x in range(4, 160, 12):
        s.black([(x, 93), (x + 1, 99)], 'rect')
    return s


def cave():
    """Under the rock: steps down to the tide pools, and far back, a light under the water."""
    s = Scene(tone=0)
    s.radial(108, 68, 64, 34, 255, 0)                # the glow lights the narrows
    s.radial(20, 0, 36, 30, 150, 0, steps=12)        # grey light from the trapdoor above
    s.quantise()
    rnd = random.Random(13)
    # rock walls: black masses whose edges the glow picks out in broken patterns
    left = [(0, 0), (58, 0), (70, 26), (84, 50), (90, 62), (86, 99), (0, 99)]
    right = [(160, 0), (152, 0), (138, 30), (130, 52), (126, 64), (132, 99), (160, 99)]
    roof = [(56, 0), (152, 0), (136, 16), (104, 22), (74, 18)]
    for poly in (left, right, roof):
        s.black(poly)
    for poly in ([(58, 0), (70, 26), (84, 50), (90, 62)], [(152, 0), (138, 30), (130, 52), (126, 64)],
                 [(74, 18), (104, 22), (136, 16)]):
        for a, b in zip(poly, poly[1:]):
            for t in range(14):
                f = t / 14
                x = a[0] + (b[0] - a[0]) * f
                y = a[1] + (b[1] - a[1]) * f
                n = rnd.randrange(2, 6)
                s.pattern([(x - n, y - 1), (x, y + 1)], 120, 'rect')
                s.white((int(x), int(y)), 'point')
    # dripping teeth from the roof against the glow
    for x in (92, 100, 112, 120):
        ln = rnd.randrange(4, 9)
        s.black([(x - 1, 20), (x + 1, 20), (x, 20 + ln)])
    # rough-cut steps down the left wall
    for i in range(7):
        x0, y = 4 + i * 6, 12 + i * 9
        s.white([(x0, y), (x0 + 14, y)], 'line')
        s.pattern([(x0, y + 1), (x0 + 14, y + 3)], 80, 'rect')
    # the water: the glow's reflection broken into lines that widen towards you
    for i, y in enumerate(range(72, 99, 3)):
        hw = 6 + i * 4
        for x in range(108 - hw, 108 + hw, 5):
            if rnd.random() < 0.75:
                s.white([(x, y), (x + 2 + i // 2, y)], 'line')
    # tide pools on the near floor, black mirrors with a bright rim
    for (x0, y0, x1, y1) in [(18, 86, 46, 94), (52, 90, 72, 96)]:
        s.white([(x0, y0), (x1, y1)], 'ellipse'); s.black([(x0 + 1, y0 + 1), (x1 - 1, y1 - 1)], 'ellipse')
        s.white([(x0 + 6, y0 + 3), (x0 + 12, y0 + 3)], 'line')
    # the light itself, under the surface
    s.white([(101, 64), (115, 70)], 'ellipse')
    s.pattern([(96, 62), (120, 72)], 200, 'ellipse')
    s.white([(104, 65), (112, 69)], 'ellipse')
    return s


def ledge():
    """Elias on the ledge above the light, a dead lantern in his lap, singing to stay awake."""
    s = Scene(tone=0)
    s.vgrad(0, 56, 0, 200, 34, 150)                  # light rising up the wall behind him
    s.radial(84, 100, 74, 26, 255, 0)                # the glow in the water below
    s.quantise()
    rnd = random.Random(17)
    for _ in range(18):                              # cracks in the lit wall
        x, y = rnd.randrange(38, 146), rnd.randrange(20, 52)
        s.black([(x, y), (x + rnd.randrange(-3, 4), y + rnd.randrange(3, 7))], 'line')
    # side walls, black, edges picked out
    s.black([(0, 0), (44, 0), (36, 30), (30, 56), (34, 99), (0, 99)])
    s.black([(160, 0), (142, 0), (148, 30), (152, 60), (160, 99)])
    s.white([(44, 0), (36, 30), (30, 56)], 'line'); s.white([(142, 0), (148, 30), (152, 60)], 'line')
    # the ledge: a black slab, its lip lit from the water
    s.black([(36, 56), (152, 54), (152, 64), (36, 68)])
    s.white([(36, 68), (152, 64)], 'line')
    # Elias: sitting against the wall, one leg straight out (the broken one), one knee up
    s.black([(103, 25), (111, 33)], 'ellipse')                   # head, bowed
    s.black([(101, 32), (113, 55)], 'rect')                      # coat
    s.black([(98, 36), (101, 50)], 'rect'); s.black([(113, 37), (117, 48)], 'rect')    # arms
    s.black([(113, 51), (140, 55)], 'rect'); s.black([(137, 46), (141, 55)], 'rect')   # straight leg, boot up
    s.black([(90, 55), (96, 42), (101, 44), (98, 55)])           # the bent knee
    # the dead lantern in his lap, its glass catching the glow
    s.white([(104, 43), (110, 50)], 'rect'); s.black([(105, 44), (109, 49)], 'rect')
    s.white([(106, 40), (108, 42)], 'line')
    # you, climbing: head and an arm over the near edge
    s.black([(60, 50), (68, 58)], 'ellipse'); s.black([(56, 56), (76, 62)], 'rect')
    s.black([(68, 52), (80, 55)], 'rect')                        # reaching arm
    # the water below: the glow broken into lines
    for i, y in enumerate(range(74, 99, 3)):
        hw = 10 + i * 6
        for x in range(84 - hw, 84 + hw, 6):
            if rnd.random() < 0.7:
                s.white([(x, y), (x + 3, y)], 'line')
    return s


def ornament():
    """The head of a text-only page: a small lighthouse between two lines of swell (160 x 10)."""
    s = Scene(160, 10, tone=255)
    s.quantise()
    for x0, x1 in ((26, 70), (90, 134)):
        for x in range(x0, x1, 6):
            s.black([(x, 6), (x + 3, 6)], 'line'); s.black([(x + 3, 6), (x + 4, 5)], 'line')
    s.black([(78, 9), (82, 9)], 'line')                          # rock
    s.black([(79, 3), (81, 8)], 'rect')                          # tower
    s.black([(78, 2), (82, 2)], 'line'); s.black([(80, 0), (80, 1)], 'line')
    s.white((80, 4), 'point'); s.white((80, 6), 'point')
    return s


SCENES = {'storm': storm, 'cottage': cottage, 'log': log, 'stair': stair, 'lamp': lamp,
          'beam': beam, 'cave': cave, 'ledge': ledge, 'ornament': ornament}
