# Typefaces for the X4 Pro — what to bake in

A decision document for the system font (`.xtf`, `tools/xtfont.py`) and for the reader's font list.
Research session 2026-09-07. Everything below is either **measured here** (the numbers came out of
this repo's own rasteriser), **consensus** (several independent sources agree), or **opinion** (one
forum poster, one blog) — each claim says which. Nothing was downloaded into the repo; the TTFs I
measured live in the session scratch dir
`/private/tmp/claude-501/-Users-mini-xteink-x4/a746b33c-5073-40dc-bf8c-628f1fad9f27/scratchpad/fonts/`
and are gone when it is cleaned. No `xtfont.py build` was run.

> **Seeing them:** `tools/fontcompare.py OUT.png --current "Name=font.ttf:17,20" ...` builds each
> candidate into the two cells with the same code a real system font goes through and draws it 1 bpp
> at 3x, with the device's current font on top as the baseline to beat. Judge from that sheet, never
> from a specimen rendered on the Mac — anti-aliased at 40 px, everything looks good.

## 0. What the device actually constrains

Two different problems, and they must not be confused:

| | UI font (`system_small.xtf` / `system_medium.xtf`) | Reader font |
|---|---|---|
| rendering | **1 bpp, no anti-aliasing** — `_rasterise` sets `fontmode='1'` and thresholds at 128 | anti-aliased through the reader's own path; three levels on this panel (`CLAUDE.md`, grayscale note) |
| size | fixed cells **20x20** and **24x24**, baseline row 17 / 20 | user-chosen, typically 30 px+ |
| what wins | hinting, big x-height at 14–22 ppem, sturdy stems, tight vertical metrics | shape, colour, texture — the usual book-typography criteria |

Measured consequences of `fit_size()` (tools/xtfont.py:361) that shape every recommendation:

* It picks the ppem from the font's **declared** `hhea` ascent/descent (`asc+desc <= 21/26`,
  `asc <= 17/20`) and from `M`'s advance fitting the cell. Declared metrics are usually far larger
  than the ink, so **fonts with roomy vertical metrics get badly under-sized**: Literata is rendered
  at **16 px in a 24 px cell**, Charis SIL at **15**, Gentium at 17, Noto Serif/Sans at 18 — while
  Newsreader gets 25 and Merriweather 20. The ranking `fit_size` produces is a ranking of vertical
  metrics, not of legibility.
* `--body-size N` / `--small-size N` override it. I measured the real ink extents (including
  `À Ö É Ž Å`, the tallest things in `BASE_RANGES`) to find how far each face can safely be pushed:
  the cell gives **20 rows above the baseline and 4 below** at cell 24. Charis SIL forced to 20 px
  measures exactly 20/4 — it fits, and gains 5 px of size over the automatic choice. Literata at 20
  px measures 20/5, i.e. one row of descender past the cell.
* Ink outside the cell is not silently lost — `x_off`/`y_off` are signed — but at `advance_y` 22 in
  a 26-px line box it will touch the line above. Treat "*" in the table below as *check it by eye*,
  not as *broken*. **Untested:** what the stock renderer does with a negative `y_off`.

## 1. Measured: how the candidates behave in the cells

`ppem` = what `fit_size` picks for cell 24; `x` = x-height in **pixels** at that ppem (the number
that decides how big the text looks); `hinted` = carries a real TrueType hinting program (`fpgm`
over 300 bytes) rather than the 7-byte stub `prep` that Google Fonts' build writes; `push` = largest
size whose ink still fits 20/4 with accented capitals. Contact sheets rendered through the real
`_rasterise`: `sheet20.png`, `sheet24.png` in the scratch dir.

| face | ppem24 | x-px | hinted | push | BASE_RANGES cov. |
|---|---|---|---|---|---|
| Merriweather | 20 | **12** | no | overflows already at 20 (22 rows above baseline) | 44% |
| Bitter | 21 | 11 | no | overflows at 21 | 36% |
| Inter | 20 | 11 | no | **20** (exactly 20/4) | 56% |
| Roboto | 21 | 11 | **yes** | 20 | 36% |
| Libre Franklin / Public Sans | 20/21 | 11 | no | overflow at fitted size | 33/20% |
| Newsreader | 25 | 11 | no | overflows at 25 and at 20 | 21% |
| **PT Serif** | 19 | 10 | **yes** | **21** | 27% |
| **PT Sans** | 19 | 10 | **yes** | **22** | 27% |
| **IBM Plex Serif** | 19 | 10 | **yes** | **20** | 28% |
| IBM Plex Sans | 19 | 10 | **yes** | 20 | 35% |
| **Atkinson Hyperlegible** | 20 | 10 | **yes** | 20 | 16% |
| Faustina | 19 | 10 | no | **22** (roomiest of all) | 24% |
| Lato | 20 | 10 | **yes** | 20 | 54% |
| Crimson Pro | 22 | 10 | no | overflows | 23% |
| Lora | 19 | 10 | no | overflows | 30% |
| Noto Serif / Noto Sans (GF copy) | 18 | 10 | no | 18 | 58% |
| Open Sans | 18 | 10 | **yes** | 18 | 36% |
| Source Serif 4 / Vollkorn | 18 | 9 | no | 18 | 34/46% |
| Source Sans 3 | 17 | 8 | no | 17 | 41% |
| **Charis SIL** | 15 | 8 | **yes** | **20** (+5 over automatic) | 49% |
| Gentium (Plus / Book Plus) | 17 | 8 | **yes** | 17 | 54% |
| Literata | 16 | 8 | no | 17 | 34% |
| EB Garamond | 19 | 8 | no | overflows | 51% |
| Fanwood | 19 | 8 | no | overflows; glyphs already break up at 19 px | 18% |

Two things fall out of this that no blog post will tell you:

* **The Google Fonts copies of Literata, Noto, Source Serif, Bitter, Merriweather, Inter and
  Newsreader carry no hinting program.** Only Atkinson Hyperlegible, Charis SIL, Gentium, IBM Plex,
  Lato, Open Sans, PT Sans/Serif and Roboto do. At 1 bpp and 17–21 ppem this is the single biggest
  predictor of whether stems come out even. FreeType's mono target uses the bytecode when it is
  there. (Measured: sfnt table directory of each file.)
* **Nothing covers the symbols the stock UI may need.** No candidate has box drawing
  (U+2500), most lack ✓ (U+2713) and ▶ (U+25B6); MiSans, as a CJK-scale font, has them. If the
  stock draws a tick or an arrow from the system font, a replacement will leave a hole.
  `xtfont.py build` intersects the TTF's cmap with `BASE_RANGES`, so the codepoint simply will not
  be in the package. **Not established:** which symbol codepoints the stock UI actually draws.

## 2. Consumer / community consensus

What e-reader owners keep naming, and why. This is soft evidence — self-selected forum users
judging by eye on their own hardware — so it is reported as such.

* **Low stroke contrast beats high contrast on e-ink.** The clearest recurring *reason*, not just a
  preference: "low contrast font has less difference between thick and thin parts", offered on
  MobileRead in support of Bitter, Alegreya and Literata
  ([MobileRead 366520](https://www.mobileread.com/forums/showthread.php?t=366520)). Consistent with
  the same thread's complaint that thin strokes disappear. **Consensus, with a stated mechanism.**
* **Charis SIL** is named repeatedly, usually with the rider that the poster added weight to it
  themselves ([MobileRead 366520](https://www.mobileread.com/forums/showthread.php?t=366520)). Its
  lineage is the point: it is SIL's OFL extension of Matthew Carter's **Bitstream Charter**, a face
  designed in 1987 for 300 dpi laser printers with a deliberately simplified, straight-segment
  structure and few curves, to survive coarse rasterisation
  ([Wikipedia: Bitstream Charter](https://en.wikipedia.org/wiki/Bitstream_Charter),
  [Wikipedia: Charis SIL](https://en.wikipedia.org/wiki/Charis_SIL)). That is exactly our problem
  restated — a low-resolution binary raster. **This is the strongest single finding in the report.**
* **Bitter** — "designed for e-ink" on MobileRead
  ([242951](https://www.mobileread.com/forums/showthread.php?t=242951)) is an overstatement; the
  foundry's own framing is that it was built "with a pixel grid, based on rational rather than
  emotional principles" for screen reading ([Huerta
  Tipográfica](https://www.huertatipografica.com/en/fonts/bitter-ht)). Screen-first, yes; e-ink
  specifically, unverified. **Opinion, corrected against the primary source.**
* **Georgia** is the Kobo default and a perennial favourite, praised for holding up "at any size",
  with a recurring complaint about its old-style numerals
  ([242951](https://www.mobileread.com/forums/showthread.php?t=242951), [366520](https://www.mobileread.com/forums/showthread.php?t=366520)).
  Proprietary (Microsoft) — excluded, but the trait to copy is obvious: enormous x-height, low
  contrast, heavy hinting.
* **Bookerly and Literata are the two purpose-built e-reading faces** and both are treated as safe
  defaults. A 15-font side-by-side shot on a Kindle Paperwhite with KOReader reaches the same
  conclusion and commends Noto Serif as KOReader's default
  ([simonh.uk, 2025](https://simonh.uk/2025/11/02/best-fonts-for-ereading-part-1/)) — though that
  author explicitly declines to rank, so it is a "these all work" result, not a winner.
* **Kobo's shipped list** — Amasis, Avenir Next, Caecilia, Georgia, Gill Sans, Kobo Nickel, Malabar,
  OpenDyslexic, STIX Two Text ([The eBook
  Reader](https://blog.the-ebook-reader.com/2021/07/10/kobo-ereaders-software-features-list/)) — is
  almost entirely licensed commercial type. It tells us what the category buys, not what we can
  ship.
* **Hinting is contested.** One MobileRead poster argues hinting "became useless with higher
  resolution screens"; another counters that Kindles use it and that stripping hints out of Bookerly
  demonstrated the need ([366520](https://www.mobileread.com/forums/showthread.php?t=366520)). For
  a 300 dpi anti-aliased reader view the first may be right; for **our 1-bpp 20 px UI cells it is
  certainly wrong**, and the measurements above are the reason to side with the second camp here.

## 3. Developer / typographer view

* **Literata** was commissioned by Google from TypeTogether for Play Books, explicitly to read well
  "on a whole range of devices and high resolution screens running different rendering
  technologies", solved as a hybrid Scotch/oldstyle roman whose upright italic "accounts for the
  inherent limitations of the square pixel grid" ([TypeTogether](https://www.type-together.com/literata-book)).
  Primary source, and the most credible "designed for e-reading" claim of any OFL face.
* **Charter / Charis SIL**: see above — the only widely-available free face whose *original design
  brief was coarse rasterisation*.
* **The one developer actually shipping fonts to e-readers** — the `nicoverbruggen/ebook-fonts`
  collection for Kobo/Kindle/Boox — is instructive because of what he had to *change*: his flagship
  faces are a **thickened** Source Serif 4 ("Sourcerer"), an extended Charter ("Cartisse"), a
  narrower-line-height Charter ("NV Charis"), an optically-enlarged EB Garamond, and two Newsreader
  derivatives; he also ships "anti-wobble hinting" variants for Kobo's rasteriser
  ([github.com/nicoverbruggen/ebook-fonts](https://github.com/nicoverbruggen/ebook-fonts)). Read
  that as a practitioner's verdict that stock OFL faces are **too light and too small** for e-ink as
  shipped — which is exactly what the 1-bpp sheets show.
* **Weight.** The device ships MiSans **Demibold**, not Regular. Every measurement above used the
  Regular/default instance. On a 1-bit panel a Regular at 19 px will read markedly lighter than the
  font it replaces. For any variable candidate, instance the `wght` axis to ~500–600 before
  building; for static-only candidates prefer the Medium/SemiBold file. **This is a stronger lever
  than the choice of face.**

## 4. Accessibility — what the evidence actually shows

* **OpenDyslexic: the claim is not supported.** Rello and Baeza-Yates' eye-tracking study of 48
  diagnosed-dyslexic readers across 12 fonts found no reading-time advantage for OpenDyslexic, and
  participants preferred Verdana and Helvetica to it; a separate randomised study of the commercial
  Dyslexie font found no benefit for children with or without dyslexia
  ([Kuster et al., *Annals of Dyslexia*](https://link.springer.com/article/10.1007/s11881-017-0154-6),
  [Wery & Diliberto on OpenDyslexic](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5629233/)). Ship
  it if users ask for it — Kobo does — but do not present it as an accessibility improvement.
* **Luciole: real study, modest result.** 145 French readers (73 low-vision), Luciole against Arial,
  Verdana, Frutiger, Eido and OpenDyslexic. About half the low-vision participants preferred Luciole;
  measured readability was significantly better than Eido and OpenDyslexic but **not** better than
  Arial or Verdana ([*Applied Ergonomics*, 2023](https://www.sciencedirect.com/science/article/pii/S0001691823001026)).
  Honest summary: a well-made, freely licensed low-vision face that is not measurably better than a
  good ordinary sans. Licence is **CC BY 4.0** ([luciole-vision.com](https://www.luciole-vision.com/)) —
  redistributable with attribution, but not one of the licences on our approved list; a decision to
  ship it is a licence decision, not just a font one.
* **Atkinson Hyperlegible** was made by Applied Design Works with the Braille Institute and
  deliberately trades visual coherence for letterform distinction (b/d, I/l/1, 0/O)
  ([Google Fonts](https://fonts.google.com/specimen/Atkinson+Hyperlegible)). I found **no
  independent controlled study** of it — the legibility claim is the designer's, not a measured one.
  What *is* measurable and matters here: it is genuinely hinted, its ink fits the cell at 20 px, and
  disambiguated letterforms are worth more at 20 px on a binary grid than they are at 300 dpi.
  Recommended for the UI on the engineering evidence, not on the accessibility claim.

## 5. Shortlists

### (a) UI face — 20/24 px, 1 bpp

1. **IBM Plex Serif Medium** (`--small-size 17 --body-size 20`). Hinted (`fpgm` 371 B, `prep` 430 B),
   ships as a **static Medium and SemiBold**, and is the only face measured whose ink fills both
   cells exactly with nothing over the edge. OFL. *Caveat:* x-height 10 px — it will read a little
   smaller than MiSans Demibold does now; missing ▶ and box drawing.
2. **Charis SIL Bold** (`--small-size 15 --body-size 20`). Charter's low-resolution design brief, a
   real hinting program, the widest glyph coverage of any candidate (3609 glyphs, 49% of
   `BASE_RANGES`), OFL. *Caveat:* `fit_size` will pick 12/15 px unless overridden — you **must** pass
   the sizes; the small cell caps it at 15 px, so small and body text will look mismatched. A serif
   UI is also an unusual look next to the stock's sans. Only Regular and Bold exist — no SemiBold.
3. **PT Sans Bold** (`--small-size 16 --body-size 22`). The most headroom of any hinted sans (ink
   20/4 at 22 px, two sizes above what `fit_size` picks), ParaType hinting is thorough, OFL.
   *Caveat:* narrow coverage (720 glyphs, no Greek, no ✓); a slightly dated 2009 look.
4. **Atkinson Hyperlegible** (`--body-size 20`, Regular). Hinted, comfortable in the body cell (18/4
   at 20 px), disambiguated letterforms. *Caveat:* the **Bold never fits the 20-px cell** — 4 rows of
   descender at every size from 15 px up; and only 369 glyphs, Latin-1 and little else, so no
   Cyrillic, Greek or arrows.
5. **Roboto** (`--body-size 20`). Hinted, large x-height (11 px), the safest "looks like a phone UI"
   choice. *Caveat:* overflows at its own fitted 21 px, so pin 20; utterly generic.

Not recommended for the UI, despite their reputations: **Literata** (unhinted, and the fitter gives
it 16 px — it will look small and mushy at 1 bpp; it is a *reading* face), **Merriweather** (ink
already overflows the cell at its fitted size), **Newsreader**, **EB Garamond**, **Fanwood**,
**Crimson Pro**, **Vollkorn** (all unhinted and all overflow), **Noto Serif/Sans** as shipped by
Google Fonts (unhinted copies; the hinted builds from notofonts.github.io are a different file).

### (b) Reading serif (anti-aliased path — different rules)

1. **Literata** — the only OFL face commissioned specifically for an e-reading app, with a published
   design rationale ([TypeTogether](https://www.type-together.com/literata-book)); OFL;
   `https://github.com/googlefonts/literata`. *Caveat:* variable-only in the Google Fonts repo;
   large (932 KB).
2. **Charis SIL** — Charter lineage, sturdy, huge language coverage; OFL; `https://github.com/silnrsi/font-charis`.
   *Caveat:* small on the page for its nominal size; readers typically bump a step.
3. **Bitter** — low contrast and slab serifs, the trait the forums single out for e-ink; OFL;
   `https://github.com/solmatas/BitterPro`. *Caveat:* the "designed for e-ink" claim is folklore.
4. **Source Serif 4** — Adobe, screen-first, well regarded; OFL; `https://github.com/adobe-fonts/source-serif`.
   *Caveat:* the practitioner above found it needed thickening for e-ink.
5. **Gentium Book Plus** — SIL, book weight, exceptional coverage (4307 glyphs), hinted; OFL;
   `https://github.com/silnrsi/font-gentium`. *Caveat:* light and small on the page.

Praised but **excluded on licence**: **Bookerly** (Amazon proprietary, the most-praised e-reading
face there is — its traits are a large x-height, low contrast and heavy hinting), **Amazon Ember**,
**Georgia** (Microsoft), Kobo's Amasis/Caecilia/Malabar/Kobo Nickel (licensed commercial type),
**Dyslexie** (commercial). **MiSans** — the font the device ships — is explicitly non-redistributable:
its licence grants a "non-transferable… revocable" copyright licence and states that the fonts may
not be rented, sublicensed, loaned, further distributed or sold, nor "adapted or redeveloped"
([MiSans Font IP Licence Agreement, hyperos.mi.com](https://hyperos.mi.com/font-download/MiSans%E5%AD%97%E4%BD%93%E7%9F%A5%E8%AF%86%E4%BA%A7%E6%9D%83%E8%AE%B8%E5%8F%AF%E5%8D%8F%E8%AE%AE.pdf)).
Rasterising it into an `.xtf` we distribute is exactly what that forbids. **The device's own font
cannot be shipped in our packages.**

### (c) Reading sans

1. **Inter** — the best-covered candidate (56% of `BASE_RANGES`), large x-height, made for UI/screen;
   OFL; `https://github.com/rsms/inter`. *Caveat:* unhinted; slightly cold for long-form prose.
2. **IBM Plex Sans** — hinted, warmer, wide family; OFL; `https://github.com/googlefonts/plex`.
3. **Lato** — hinted, 3023 glyphs, humanist; OFL. *Caveat:* fashionable-2010s look.
4. **Atkinson Hyperlegible** — as an accessibility *option* in the list, framed honestly (see §4).
   OFL; `https://github.com/googlefonts/atkinson-hyperlegible`.
5. **Luciole** — a genuine low-vision option with a real study behind it; **CC BY 4.0**, not OFL —
   a separate licence call; `https://www.luciole-vision.com/`.

## 6. Install mapping

Every entry below is `.venv/bin/python tools/xtfont.py build FONT.ttf --id ID --name "NAME" OUT_DIR`
followed by `... install SD.img OUT_DIR/ID.xtfont`. Download URLs are the `google/fonts` raw paths
I fetched (all returned 200); the upstream repos in §5 are the canonical sources.

| face | file to build from | size flags needed | notes |
|---|---|---|---|
| **IBM Plex Serif** | `ofl/ibmplexserif/IBMPlexSerif-Medium.ttf` (**static**; Regular…SemiBold all present) | `--small-size 17 --body-size 20` | fits both cells exactly; 159 KB, 836 glyphs |
| **Charis SIL** | `ofl/charissil/CharisSIL-Bold.ttf` (static; **only** Regular/Bold + italics exist) | **yes** — `--small-size 15 --body-size 20`, else you get 12/15 px | 735 KB, 3609 glyphs — slowest build here |
| **PT Sans / PT Serif** | `ofl/ptsans/PT_Sans-Web-Bold.ttf`, `ofl/ptserif/PT_Serif-Web-Regular.ttf` (static) | `--small-size 16 --body-size 22` / `21` | no Greek, no ✓; 720 glyphs |
| IBM Plex Sans | `ofl/ibmplexsans/IBMPlexSans[wdth,wght].ttf` | `--body-size 20` | **variable-only in google/fonts** — instance `wght` 500, or take statics from `github.com/IBM/plex` releases |
| Atkinson Hyperlegible | `ofl/atkinsonhyperlegible/AtkinsonHyperlegible-Regular.ttf` (static) | `--body-size 20`; Bold cannot be used | 369 glyphs — Latin-1 only; expect blanks outside it |
| Roboto | `ofl/roboto/Roboto[wdth,wght].ttf` | `--body-size 20` (pin it; 21 overflows) | **variable** — instance first |
| Inter | `ofl/inter/Inter[opsz,wght].ttf` | fitted 20 is already the maximum | **variable**; unhinted |
| Literata | `ofl/literata/Literata[opsz,wght].ttf` | reading only — do not build for the UI | **variable**, 932 KB |
| Bitter | `ofl/bitter/Bitter[wght].ttf` | reading only | **variable**; overflows the UI cell |
| Merriweather | `ofl/merriweather/Merriweather[opsz,wdth,wght].ttf` | reading only | **variable, 4.5 MB** — largest of the set |
| Gentium Book Plus | `ofl/gentiumbookplus/GentiumBookPlus-Regular.ttf` (static) | `--body-size 17` (its own fit) | 4307 glyphs — huge |
| Noto Serif / Sans | `ofl/notoserif/...`, `ofl/notosans/...` | — | GF copies are **unhinted**; use notofonts.github.io hinted builds instead |

Base path for all of the above: `https://raw.githubusercontent.com/google/fonts/main/`.
`Libre Baskerville` and `Tinos` 404'd at their expected paths and were not evaluated.

`build` writes both cells from one TTF, so a face has to work at 20 **and** 24; the `push` column in
§1 is the 24-px number. **The 20-px cell is the binding one and it is much tighter**: baseline 17
leaves only **3 rows below the baseline**, and almost every face descends 4 rows once it is large
enough to be legible. Measured, for the three faces recommended below (ink above/below baseline,
against 17/3 for cell 20 and 20/4 for cell 24):

| face + weight | cell 20 | cell 24 |
|---|---|---|
| IBM Plex Serif **Medium** | 17 px → 17/3 — fits exactly | 20 px → 20/4 — fits exactly |
| IBM Plex Serif SemiBold | 17 px → 17/3 | 20 px → 20/4 |
| PT Sans **Bold** | 16 px → 14/3 (17 px already descends 4) | 22 px → 20/4, the roomiest here |
| Charis SIL **Bold** | 15 px → 16/3; 16 px descends 4 | 20 px → 20/4 |
| Atkinson Hyperlegible Bold | **never** — 4 rows of descender at every size from 15 px | 20 px → 18/4 |

So the small cell, not the body cell, is what disqualifies a face. Charis SIL Bold at 15/20 means a
noticeably smaller small-font than body-font; IBM Plex Serif Medium at 17/20 is the most even pair
measured.

## 7. What to install first

Three faces, in this order:

1. **IBM Plex Serif Medium, `--small-size 17 --body-size 20`** — the only face measured that fills
   *both* cells exactly (17/3 and 20/4) with no overflow, is genuinely hinted, and comes as a static
   Medium so the weight matches MiSans Demibold's darkness without instancing anything. Lowest-risk
   first build.
2. **Charis SIL Bold, `--small-size 15 --body-size 20`** — the highest-information experiment: the
   one face whose original design brief *is* coarse rasterisation (Charter, 1987, 300 dpi laser
   printers), hinted, best glyph coverage of the shortlist. If a Charter derivative does not hold at
   1 bpp, nothing unhinted will. *Note the 15/20 split* — its small font will look distinctly
   smaller than its body font, which may itself be the reason to reject it.
3. **PT Sans Bold, `--small-size 16 --body-size 22`** — the sans control, and the roomiest face
   measured (it can be built two sizes above what `fit_size` picks). Same category as the stock
   font, so it isolates "is the problem the face or the weight?". Its 720 glyphs are the cost.

Atkinson Hyperlegible is deliberately *not* in this three: measured, its Bold descends 4 rows at
every usable size and so never fits the 20-px cell. Try the Regular there if the accessibility angle
matters, but check the small cell first.

Then judge the three by eye on the emulator's Home screen against
`tests/data/stock-home-panel.png`, which is what MiSans Demibold looks like at cell 24.

## 8. What could not be established

* **Which codepoints the stock UI draws.** Every candidate is missing box drawing, most are missing
  ✓ and ▶. If the UI uses them the replacement will show holes and I cannot predict where.
* **What the stock renderer does with a negative `y_off`** — whether ink above the cell is clipped,
  drawn, or collides with the line above. The "*overflows*" verdicts in §1 are therefore risks, not
  proven failures.
* **Whether the reader view's font list can be extended at all**, and in what format. This document
  assumes it can; §5(b) and §5(c) are useless if it cannot.
* **Any controlled study of Atkinson Hyperlegible.** Only the designers' claims were found.
* **Bookerly's actual metrics** — it is the most-praised e-reading face and unavailable for
  measurement, so "large x-height, low contrast, heavily hinted" is inference from commentary.
* **Whether MiSans Demibold's own x-height at cell 24 is 11 or 12 px.** No `.xtf` exists outside
  `images/` (out of bounds for this session), so the candidates were not compared against the
  incumbent numerically — only against each other.
* **The weight question was not measured.** Every number here is the Regular/default instance. The
  claim that a Medium/SemiBold instance is the bigger lever than the choice of face is reasoning
  from the 1-bpp sheets, not a measurement.
