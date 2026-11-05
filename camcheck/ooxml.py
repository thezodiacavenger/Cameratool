"""Minimal, dependency-free writers for Excel (.xlsx) and Word (.docx).

Only what Camera Check needs: styled header rows, filters and frozen panes for
spreadsheets; headings, paragraphs, tables, shading and an optional PNG logo for
documents. All text is XML-escaped; formula-like cells are written as strings.
"""
import datetime as _dt
import io
import re
import struct
import zipfile
from xml.sax.saxutils import escape as _esc

_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def esc(v):
    return _esc(_BAD_XML.sub("", "" if v is None else str(v)), {'"': "&quot;"})


def _col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _core(title, creator):
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{esc(title)}</dc:title><dc:creator>{esc(creator)}</dc:creator>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
            '</cp:coreProperties>')


# ================================================================= XLSX ======

def xlsx(sheets, title="Camera Check export", creator="Camera Check"):
    """sheets: list of (name, header_list, rows, col_widths_or_None). Returns bytes."""
    buf = io.BytesIO()
    z = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    n = len(sheets)
    z.writestr("[Content_Types].xml",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
               '<Default Extension="xml" ContentType="application/xml"/>'
               '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
               '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
               + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(n))
               + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
               '</Types>')
    z.writestr("_rels/.rels",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
               '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
               '</Relationships>')
    z.writestr("docProps/core.xml", _core(title, creator))
    names = [re.sub(r"[\[\]:*?/\\]", " ", s[0])[:31] or f"Sheet{i + 1}" for i, s in enumerate(sheets)]
    z.writestr("xl/workbook.xml",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
               + "".join(f'<sheet name="{esc(names[i])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                         for i in range(n))
               + '</sheets>'
               + "".join(f'<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'
                         f"'{esc(names[0])}'!$A$1:${_col(max(len(sh[1]) - 1, 0))}${len(sh[2]) + 1}</definedName></definedNames>"
                         for sh in sheets[:1] if sh[2])
               + '</workbook>')
    z.writestr("xl/_rels/workbook.xml.rels",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(n))
               + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
               '</Relationships>')
    # styles: 0 default, 1 header (bold white on dark), 2 wrap text, 3 number with thousands, 4 percent-ish 0.0
    z.writestr("xl/styles.xml",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               '<numFmts count="1"><numFmt numFmtId="164" formatCode="0.0"/></numFmts>'
               '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
               '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
               '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
               '<fill><patternFill patternType="solid"><fgColor rgb="FF1F2937"/><bgColor indexed="64"/></patternFill></fill></fills>'
               '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
               '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
               '<cellXfs count="5"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
               '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment vertical="center"/></xf>'
               '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>'
               '<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"><alignment vertical="top"/></xf>'
               '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"><alignment vertical="top"/></xf>'
               '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
               '</styleSheet>')
    for i, sheet in enumerate(sheets):
        name, header, rows = sheet[:3]
        widths = sheet[3] if len(sheet) > 3 else None
        out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
               '</sheetView></sheetViews><sheetFormatPr defaultRowHeight="15"/>']
        widths = widths or [max(10, min(60, max([len(str(h))] + [len(str(r[c] or "")) for r in rows[:300]]) + 2))
                            for c, h in enumerate(header)]
        out.append("<cols>" + "".join(f'<col min="{c + 1}" max="{c + 1}" width="{w}" customWidth="1"/>'
                                      for c, w in enumerate(widths)) + "</cols><sheetData>")
        for r_i, row in enumerate([header] + list(rows)):
            cells = []
            for c_i, v in enumerate(row):
                ref = f"{_col(c_i)}{r_i + 1}"
                if r_i == 0:
                    cells.append(f'<c r="{ref}" s="1" t="inlineStr"><is><t>{esc(v)}</t></is></c>')
                elif isinstance(v, bool) or v is None or v == "":
                    if v not in (None, ""):
                        cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{esc(v)}</t></is></c>')
                elif isinstance(v, int):
                    cells.append(f'<c r="{ref}" s="3"><v>{v}</v></c>')
                elif isinstance(v, float):
                    cells.append(f'<c r="{ref}" s="4"><v>{v}</v></c>')
                else:
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{esc(v)}</t></is></c>')
            out.append(f'<row r="{r_i + 1}">' + "".join(cells) + "</row>")
        out.append("</sheetData>")
        if rows and i == 0:
            out.append(f'<autoFilter ref="A1:{_col(len(header) - 1)}{len(rows) + 1}"/>')
        out.append('<pageMargins left="0.5" right="0.5" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
                   '<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>')
        z.writestr(f"xl/worksheets/sheet{i + 1}.xml", "".join(out))
    z.close()
    return buf.getvalue()


# ================================================================= DOCX ======

def png_size(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return struct.unpack(">II", data[16:24])


class Docx:
    """Tiny document builder. Units: twips for tables, EMU for images."""
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    def __init__(self, title="Document", creator="Camera Check", accent="C8102E"):
        self.body = []
        self.title = title
        self.creator = creator
        self.accent = accent
        self.images = []
        self.footer_text = ""

    @staticmethod
    def _run(text, bold=False, size=None, color=None, italic=False):
        props = ""
        if bold:
            props += "<w:b/>"
        if italic:
            props += "<w:i/>"
        if color:
            props += f'<w:color w:val="{color}"/>'
        if size:
            props += f'<w:sz w:val="{int(size * 2)}"/><w:szCs w:val="{int(size * 2)}"/>'
        parts = str("" if text is None else text).split("\n")
        runs = []
        for i, part in enumerate(parts):
            br = "<w:br/>" if i else ""
            runs.append(f'<w:r>{"<w:rPr>" + props + "</w:rPr>" if props else ""}{br}'
                        f'<w:t xml:space="preserve">{esc(part)}</w:t></w:r>')
        return "".join(runs)

    def para(self, text="", bold=False, size=None, color=None, align=None, style=None, space_after=None,
             italic=False, runs=None):
        ppr = ""
        if style:
            ppr += f'<w:pStyle w:val="{style}"/>'
        if space_after is not None:
            ppr += f'<w:spacing w:after="{space_after}"/>'
        if align:
            ppr += f'<w:jc w:val="{align}"/>'
        content = runs if runs is not None else self._run(text, bold, size, color, italic)
        self.body.append(f'<w:p>{"<w:pPr>" + ppr + "</w:pPr>" if ppr else ""}{content}</w:p>')

    def heading(self, text, level=1):
        self.para(text, style=f"Heading{level}")

    def title_block(self, title, subtitle=None):
        self.para(title, style="Title")
        if subtitle:
            self.para(subtitle, color="555555", size=11)

    def table(self, rows, widths=None, header=False, shade_first_col=False, font_size=10, borders=True):
        """rows: list of lists of str (or (str, dict(bold,color,fill)))"""
        cols = max(len(r) for r in rows)
        widths = widths or [int(9360 / cols)] * cols
        b = ('<w:tblBorders>' + "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="D9DCE1"/>'
                                         for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
             + '</w:tblBorders>') if borders else ""
        out = [f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/>{b}'
               '<w:tblLayout w:type="fixed"/><w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
               '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr>'
               '<w:tblGrid>' + "".join(f'<w:gridCol w:w="{w}"/>' for w in widths) + '</w:tblGrid>']
        for r_i, row in enumerate(rows):
            out.append("<w:tr>" + ('<w:trPr><w:tblHeader/></w:trPr>' if header and r_i == 0 else ""))
            for c_i in range(cols):
                cell = row[c_i] if c_i < len(row) else ""
                opts = {}
                if isinstance(cell, tuple):
                    cell, opts = cell
                is_head = header and r_i == 0
                fill = opts.get("fill") or ("1F2937" if is_head else ("F3F4F6" if shade_first_col and c_i == 0 else None))
                color = opts.get("color") or ("FFFFFF" if is_head else ("4B5563" if shade_first_col and c_i == 0 else None))
                bold = opts.get("bold", is_head or (shade_first_col and c_i == 0))
                tcpr = f'<w:tcW w:w="{widths[c_i] if c_i < len(widths) else widths[-1]}" w:type="dxa"/>'
                if fill:
                    tcpr += f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>'
                out.append(f'<w:tc><w:tcPr>{tcpr}</w:tcPr><w:p><w:pPr><w:spacing w:after="0"/></w:pPr>'
                           f'{self._run(cell, bold=bold, size=opts.get("size", font_size), color=color)}</w:p></w:tc>')
            out.append("</w:tr>")
        out.append("</w:tbl>")
        self.body.append("".join(out))
        self.para("", space_after=60)

    def image(self, png_bytes, width_in=1.6):
        w, h = png_size(png_bytes)
        cx = int(width_in * 914400)
        cy = int(cx * h / max(w, 1))
        rid = f"rIdImg{len(self.images) + 1}"
        self.images.append((rid, png_bytes))
        n = len(self.images)
        self.body.append(
            '<w:p><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0" '
            'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
            f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{n}" name="Logo {n}"/>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="{n}" name="logo{n}.png"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
            '<a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
            '</wp:inline></w:drawing></w:r></w:p>')

    def bar(self, pct, width_twips=9360, fill="16A34A", empty="E5E7EB"):
        """A horizontal meter drawn as a two-cell table."""
        pct = max(0.0, min(100.0, float(pct)))
        a = max(1, int(width_twips * pct / 100))
        b = max(1, width_twips - a)
        self.body.append(
            f'<w:tbl><w:tblPr><w:tblW w:w="{width_twips}" w:type="dxa"/><w:tblLayout w:type="fixed"/></w:tblPr>'
            f'<w:tblGrid><w:gridCol w:w="{a}"/><w:gridCol w:w="{b}"/></w:tblGrid><w:tr><w:trPr><w:trHeight w:val="220" w:hRule="exact"/></w:trPr>'
            f'<w:tc><w:tcPr><w:tcW w:w="{a}" w:type="dxa"/><w:shd w:val="clear" w:color="auto" w:fill="{fill}"/></w:tcPr><w:p/></w:tc>'
            f'<w:tc><w:tcPr><w:tcW w:w="{b}" w:type="dxa"/><w:shd w:val="clear" w:color="auto" w:fill="{empty}"/></w:tcPr><w:p/></w:tc>'
            '</w:tr></w:tbl>')
        self.para("", space_after=80)

    def footer(self, text):
        self.footer_text = text

    def build(self):
        buf = io.BytesIO()
        z = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                   '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
                   '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                   '</Relationships>')
        z.writestr("docProps/core.xml", _core(self.title, self.creator))
        rels = ['<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>',
                '<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>']
        for i, (rid, data) in enumerate(self.images):
            z.writestr(f"word/media/image{i + 1}.png", data)
            rels.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image{i + 1}.png"/>')
        z.writestr("word/_rels/document.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(rels) + '</Relationships>')
        a = self.accent
        z.writestr("word/styles.xml",
                   f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{self.W}">'
                   '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
                   '<w:sz w:val="21"/><w:szCs w:val="21"/><w:color w:val="1F2937"/></w:rPr></w:rPrDefault>'
                   '<w:pPrDefault><w:pPr><w:spacing w:after="100" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
                   '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
                   '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
                   '<w:pPr><w:spacing w:after="60"/></w:pPr><w:rPr><w:b/><w:sz w:val="44"/><w:color w:val="111827"/></w:rPr></w:style>'
                   '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
                   f'<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="80"/><w:pBdr><w:bottom w:val="single" w:sz="8" w:space="2" w:color="{a}"/></w:pBdr>'
                   '<w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="28"/><w:color w:val="111827"/></w:rPr></w:style>'
                   '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>'
                   '<w:pPr><w:keepNext/><w:spacing w:before="160" w:after="60"/><w:outlineLvl w:val="1"/></w:pPr>'
                   '<w:rPr><w:b/><w:sz w:val="23"/><w:color w:val="374151"/></w:rPr></w:style>'
                   '</w:styles>')
        z.writestr("word/footer1.xml",
                   f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr xmlns:w="{self.W}">'
                   f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr>{self._run(self.footer_text, size=8, color="6B7280")}</w:p></w:ftr>')
        z.writestr("word/document.xml",
                   f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   f'<w:document xmlns:w="{self.W}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   '<w:body>' + "".join(self.body)
                   + '<w:sectPr><w:footerReference w:type="default" r:id="rIdFooter"/>'
                   '<w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" '
                   'w:header="600" w:footer="600" w:gutter="0"/></w:sectPr></w:body></w:document>')
        z.close()
        return buf.getvalue()
