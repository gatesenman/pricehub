"""把 docs 下的 Markdown（含 mermaid 图）导出为 PDF：Markdown → HTML（mermaid 用本地 mermaid.js 渲染为 SVG）→ Chromium 打印。
用法：python scripts/export_pdf.py docs/PRD.md docs/ARCHITECTURE.md"""
import sys, os, re, html, markdown
from playwright.sync_api import sync_playwright
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); MJS = os.path.join(ROOT, "scripts", "vendor", "mermaid.min.js")
CSS = """body{font-family:'Noto Sans CJK SC','Noto Serif CJK SC',sans-serif;font-size:10.5pt;line-height:1.6;color:#1f2328;margin:0 6mm}
h1{font-size:20pt;border-bottom:2px solid #0969da;padding-bottom:4px}h2{font-size:14pt;border-bottom:1px solid #d0d7de;padding-bottom:2px;margin-top:20px;page-break-after:avoid}
h3{font-size:12pt}table{border-collapse:collapse;width:100%;margin:8px 0;font-size:9pt;page-break-inside:auto}tr{page-break-inside:avoid}
th,td{border:1px solid #d0d7de;padding:4px 6px;vertical-align:top}th{background:#f6f8fa}code{font-family:'DejaVu Sans Mono',monospace;font-size:8.5pt;background:#f6f8fa;padding:1px 3px}
pre{background:#f6f8fa;padding:8px;overflow:hidden;white-space:pre-wrap;font-size:8.5pt}blockquote{color:#57606a;border-left:3px solid #d0d7de;margin:0;padding-left:10px}
.mermaid{text-align:center;page-break-inside:avoid;background:none}.mermaid svg{max-width:100%;max-height:200mm;height:auto;width:auto}"""
def to_html(md_text, title):
    blocks = []
    def keep(m): blocks.append(m.group(1)); return f"\n\nMERMAIDBLOCK{len(blocks) - 1}\n\n"
    md_text = re.sub(r"```mermaid\n(.*?)```", keep, md_text, flags=re.S)
    body = markdown.markdown(md_text, extensions=["tables", "fenced_code", "sane_lists"])
    for i, b in enumerate(blocks): body = body.replace(f"<p>MERMAIDBLOCK{i}</p>", f'<pre class="mermaid">{html.escape(b)}</pre>')
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>{CSS}</style><script>{open(MJS, encoding='utf-8').read()}</script></head>
<body>{body}<script>mermaid.initialize({{startOnLoad:false,theme:'neutral',fontFamily:'Noto Sans CJK SC',flowchart:{{useMaxWidth:true}}}});
mermaid.run({{querySelector:'.mermaid'}}).then(()=>document.body.dataset.done='1').catch(e=>{{document.body.dataset.done='err:'+e}});</script></body></html>"""
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox","--disable-dev-shm-usage"]); pg = b.new_page()
    for f in sys.argv[1:]:
        title = os.path.splitext(os.path.basename(f))[0]; h = os.path.join("/tmp", title + ".html")
        open(h, "w", encoding="utf-8").write(to_html(open(f, encoding="utf-8").read(), title))
        pg.goto("file://" + h); pg.wait_for_function("document.body.dataset.done", timeout=60000)
        st = pg.evaluate("document.body.dataset.done"); n = pg.locator(".mermaid svg").count()
        out = os.path.splitext(f)[0] + ".pdf"
        pg.pdf(path=out, format="A4", print_background=True, margin={"top": "14mm", "bottom": "14mm", "left": "10mm", "right": "10mm"},
               display_header_footer=True, header_template="<span></span>", footer_template='<div style="font-size:8px;width:100%;text-align:center;color:#888">PriceHub · <span class="pageNumber"></span> / <span class="totalPages"></span></div>')
        print(out, "mermaid:", n, "status:", st)
    b.close()
