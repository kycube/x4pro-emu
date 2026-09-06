#!/usr/bin/env python3
"""Write a minimal, valid EPUB 2 with enough text for several pages (test fixture)."""
import sys, zipfile

def make_epub(path, title='Emulator Test Book', chapters=3, paragraphs=40):
    body = []
    for c in range(1, chapters + 1):
        paras = ''.join(f'<p>Chapter {c}, paragraph {p}. The quick brown fox jumps over the lazy dog. '
                        f'Pack my box with five dozen liquor jugs. Sphinx of black quartz, judge my vow.</p>\n'
                        for p in range(1, paragraphs + 1))
        body.append(f'<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter {c}</title></head>'
                    f'<body><h1>Chapter {c}</h1>\n{paras}</body></html>')
    manifest = ''.join(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>' for i in range(1, chapters + 1))
    spine = ''.join(f'<itemref idref="c{i}"/>' for i in range(1, chapters + 1))
    navpoints = ''.join(f'<navPoint id="n{i}" playOrder="{i}"><navLabel><text>Chapter {i}</text></navLabel><content src="c{i}.xhtml"/></navPoint>' for i in range(1, chapters + 1))
    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid" version="2.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title><dc:creator>x4pro-emu</dc:creator>
<dc:language>en</dc:language><dc:identifier id="uid">urn:uuid:12345678-1234-1234-1234-123456789abc</dc:identifier></metadata>
<manifest>{manifest}<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>
<spine toc="ncx">{spine}</spine></package>'''
    ncx = f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="urn:uuid:12345678-1234-1234-1234-123456789abc"/></head>
<docTitle><text>{title}</text></docTitle><navMap>{navpoints}</navMap></ncx>'''
    container = '''<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'''
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', container, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/content.opf', opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/toc.ncx', ncx, compress_type=zipfile.ZIP_DEFLATED)
        for i, html in enumerate(body, 1):
            z.writestr(f'OEBPS/c{i}.xhtml', html, compress_type=zipfile.ZIP_DEFLATED)

if __name__ == '__main__':
    make_epub(sys.argv[1] if len(sys.argv) > 1 else 'test.epub')
    print('ok')
