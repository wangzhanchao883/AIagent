# -*- coding: utf-8 -*-
"""Extract text from legacy .doc (Word 97-2003 OLE2) files via piece table parsing."""
import olefile
import struct
import sys


def extract_doc_text(path):
    ole = olefile.OleFileIO(path)
    stream = ole.openstream('WordDocument')
    word_doc = stream.read()

    # FibBase flags: bit 9 (0x0200) -> use 1Table, else 0Table
    use_1table = bool(struct.unpack('<H', word_doc[0x000A:0x000C])[0] & 0x0200)
    table_name = '1Table' if use_1table else '0Table'
    if not ole.exists(table_name):
        table_name = '1Table' if table_name == '0Table' else '0Table'
    table = ole.openstream(table_name).read()

    # fcClx / lcbClx at 0x01A2 (FibRgFcLcb97 layout)
    fc_clx, lcb_clx = struct.unpack('<II', word_doc[0x01A2:0x01AA])
    if lcb_clx == 0 or fc_clx >= len(table):
        raise ValueError('No CLX found')

    clx = table[fc_clx:fc_clx + lcb_clx]

    # Parse CLX: find the Pcdt (piece table), marker 0x02
    pos = 0
    lcb_pcdt = 0
    plc_pcd = b''
    while pos < len(clx):
        marker = clx[pos]
        if marker == 0x02:
            lcb_pcdt = struct.unpack('<I', clx[pos + 1:pos + 5])[0]
            plc_pcd = clx[pos + 5:pos + 5 + lcb_pcdt]
            break
        elif marker == 0x01:
            cb = struct.unpack('<H', clx[pos + 1:pos + 3])[0]
            pos += 3 + cb
        else:
            # Some writers put raw Pcdt without 0x02 marker
            plc_pcd = clx[pos:]
            lcb_pcdt = len(clx) - pos
            break

    if not plc_pcd:
        raise ValueError('No piece table found')

    n_pieces = (lcb_pcdt - 4) // 12
    cps = [struct.unpack('<I', plc_pcd[i * 4:i * 4 + 4])[0] for i in range(n_pieces + 1)]
    pcds_off = 4 * (n_pieces + 1)

    chunks = []
    for i in range(n_pieces):
        pcd = plc_pcd[pcds_off + i * 8: pcds_off + i * 8 + 8]
        fc_raw = struct.unpack('<I', pcd[2:6])[0]
        compressed = bool(fc_raw & 0x40000000)
        fc = fc_raw & 0x3FFFFFFF
        if compressed:
            cb = cps[i + 1] - cps[i]
            raw = word_doc[fc:fc + cb]
            # Ansi compressed: bytes; decode heuristic for Chinese
            try:
                txt = raw.decode('gb18030')
            except Exception:
                txt = raw.decode('cp1252', errors='replace')
        else:
            cb = (cps[i + 1] - cps[i]) * 2
            raw = word_doc[fc:fc + cb]
            txt = raw.decode('utf-16-le', errors='replace')
        chunks.append(txt)

    ole.close()
    return ''.join(chunks)


if __name__ == '__main__':
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    text = extract_doc_text(src)
    if out:
        with open(out, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f'OK: {len(text)} chars -> {out}')
    else:
        print(text[:2000])
