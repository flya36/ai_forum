# -*- coding: utf-8 -*-
"""
第四章_系统测试.docx -> 手机端友好自包含 HTML 预览
解析最终 docx（段落+表格+内嵌图片），图片转 base64 内嵌，无外部依赖。
"""
import os
import re
import base64
from docx import Document
from docx.oxml.ns import qn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "第四章_系统测试.docx")
OUT = os.path.join(ROOT, "第四章_系统测试_预览.html")

doc = Document(SRC)

image_by_rid = {}
for rid, rel in doc.part.rels.items():
    if "image" in rel.reltype:
        image_by_rid[rid] = rel.target_part.blob


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def datauri(blob):
    return "data:image/png;base64," + base64.b64encode(blob).decode("ascii")


def p_text(p):
    return "".join(n.text or "" for n in p.iter() if n.tag == qn("w:t"))


def p_blip(p):
    blips = p.findall(".//" + qn("a:blip"))
    for b in blips:
        rid = b.get(qn("r:embed"))
        if rid in image_by_rid:
            return datauri(image_by_rid[rid])
    return None


def tbl_html(tbl):
    out = ['<table>']
    for ri, tr in enumerate(tbl.findall(qn("w:tr"))):
        tag = "th" if ri == 0 else "td"
        out.append("<tr>")
        for tc in tr.findall(qn("w:tc")):
            txt = "".join(n.text or "" for n in tc.iter() if n.tag == qn("w:t"))
            out.append("<%s>%s</%s>" % (tag, esc(txt), tag))
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


H2 = re.compile(r"^4\.\d+\s")
H3 = re.compile(r"^4\.\d+\.\d+\s|^[1-5]\.\s|^修复计划与后续改进")
H4 = re.compile(r"^（[1-5]）|^表 F-")
CAP = re.compile(r"^图 4-\d+")
RESULT = re.compile(r"^测试结果")


def render_paragraph(txt):
    t = txt.strip()
    if not t:
        return ""
    if CAP.match(t):
        return '<div class="cap">%s</div>' % esc(t)
    if H4.match(t):
        return '<h4>%s</h4>' % esc(t)
    if H3.match(t):
        return '<h3>%s</h3>' % esc(t)
    if H2.match(t):
        return '<h2>%s</h2>' % esc(t)
    if RESULT.match(t):
        return '<p class="result">%s</p>' % esc(t)
    return '<p>%s</p>' % esc(t)


body = doc.element.body
html_parts = []
pending_cap = None
for child in body.iterchildren():
    if child.tag == qn("w:p"):
        txt = p_text(child)
        img = p_blip(child)
        if img:
            html_parts.append('<div class="img"><img src="%s" alt="截图"></div>' % img)
            continue
        if txt.strip() == "":
            continue
        html_parts.append(render_paragraph(txt))
    elif child.tag == qn("w:tbl"):
        html_parts.append(tbl_html(child))

css = """
body{margin:0;background:#f2f3f5;font-family:'Microsoft YaHei','PingFang SC',sans-serif;color:#222;}
.wrap{max-width:860px;margin:0 auto;background:#fff;padding:20px 18px 40px;}
h1{font-size:22px;text-align:center;margin:16px 0;}
h2{font-size:18px;border-left:4px solid #0b57d0;padding-left:10px;margin:26px 0 10px;}
h3{font-size:16px;color:#0b57d0;margin:20px 0 8px;}
h4{font-size:15px;margin:16px 0 6px;}
p{font-size:15px;line-height:1.8;margin:8px 0;text-align:justify;}
p.result{font-weight:bold;color:#0b57d0;}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13px;}
th,td{border:1px solid #c8ccd4;padding:6px 8px;text-align:left;vertical-align:top;word-break:break-word;}
th{background:#eef2f8;font-weight:bold;}
tr:nth-child(even) td{background:#fafbfc;}
.img{text-align:center;margin:14px 0;}
.img img{max-width:100%;height:auto;border:1px solid #ddd;border-radius:6px;}
.cap{font-size:12px;color:#666;text-align:center;margin:-10px 0 16px;}
"""

html_doc = (
    '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>第四章 系统测试（手机预览）</title><style>%s</style></head>'
    '<body><div class="wrap">%s</div></body></html>'
) % (css, "\n".join(html_parts))

with open(OUT, "w", encoding="utf-8") as f:
    f.write(html_doc)

print("SAVED:", OUT)
print("size:", os.path.getsize(OUT), "bytes")
