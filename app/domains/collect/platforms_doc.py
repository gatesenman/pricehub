"""从《全球各国主要电商平台一览》PDF 解析出平台目录（国家/平台/网址/类型/本土或国际/说明）。"""
import re, json, os, subprocess
DOM = re.compile(r"^[\w.-]+\.[a-z]{2,}(/\S*)?$", re.I)
def parse(txt):
    out, region, country, glob = [], "", "", False
    for line in txt.splitlines():
        s = line.strip()
        if not s or s.startswith("第 ") or s.startswith("国家 ") or s.startswith("平台名称"): 
            if s.startswith("平台名称"): glob = True
            continue
        if re.match(r"^(亚洲|欧洲|非洲|美洲|北美|南美|大洋洲|中东|加勒比)[-\w]*$", s): region = s; continue
        parts = re.split(r"\s{2,}", s)
        di = next((i for i, p in enumerate(parts) if DOM.match(p)), None)
        if di is None or di == 0: continue
        if glob:
            name, rest = parts[0], parts[di + 1:]
            out.append(dict(region="全球/区域", country=(rest[2] if len(rest) > 2 else ""), name=name, url=parts[di],
                            type=rest[1] if len(rest) > 1 else "", scope="区域/全球", note=rest[-1] if rest else "")); continue
        if not line.startswith(" ") and di >= 2: country = parts[0]; name = " ".join(parts[1:di])
        else: name = " ".join(parts[:di])
        rest = parts[di + 1:]
        out.append(dict(region=region, country=country, name=name, url=parts[di], type=rest[0] if rest else "",
                        scope=rest[1] if len(rest) > 1 else "", note=rest[2] if len(rest) > 2 else ""))
    return out
if __name__ == "__main__":
    import sys
    txt = subprocess.run(["pdftotext", "-layout", sys.argv[1], "-"], capture_output=True, text=True).stdout
    data = parse(txt); json.dump(data, open(os.path.join(os.path.dirname(__import__('app').__file__), "platforms.json"), "w"), ensure_ascii=False, indent=0)
    print(len(data), "platforms")
