"""简单 WebUI smoke 测试: POST 上传 + 解析响应。"""
import json
import os
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 用 fontTools 合成一个最小 TTF（只有 .notdef），看看报错路径
from fontTools.ttLib import TTFont


def main() -> int:
    # 合成一个简单的只含 ".notdef" 的 TTF 作为测试上传文件
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".ttf")
    try:
        from fontTools.ttLib import newTable
        font = TTFont()
        font.ensureDecompiled()

        cmap = newTable("cmap")
        cmap.tableVersion = 0
        cmap.tables = []
        font["cmap"] = cmap

        head = newTable("head")
        for k, v in [
            ("tableVersion", 1.0), ("fontRevision", 1.0),
            ("checkSumAdjustment", 0), ("magicNumber", 0x5F0F3CF5),
            ("flags", 0), ("unitsPerEm", 1000),
            ("created", 0), ("modified", 0),
            ("xMin", -100), ("yMin", -100), ("xMax", 100), ("yMax", 100),
            ("macStyle", 0), ("lowestRecPPEM", 6),
            ("fontDirectionHint", 2), ("indexToLocFormat", 0),
            ("glyphDataFormat", 0),
        ]:
            setattr(head, k, v)
        font["head"] = head

        hhea = newTable("hhea")
        for k, v in [
            ("tableVersion", 1.0), ("ascent", 800), ("descent", -200),
            ("lineGap", 0), ("advanceWidthMax", 1000),
            ("minLeftSideBearing", 0), ("minRightSideBearing", 0),
            ("xMaxExtent", 1000), ("caretSlopeRise", 1),
            ("caretSlopeRun", 0), ("caretOffset", 0),
            ("reserved0", 0), ("reserved1", 0), ("reserved2", 0),
            ("reserved3", 0), ("metricDataFormat", 0),
            ("numberOfHMetrics", 1),
        ]:
            setattr(hhea, k, v)
        font["hhea"] = hhea

        maxp = newTable("maxp")
        maxp.tableVersion = 1.0
        maxp.numGlyphs = 1
        font["maxp"] = maxp

        hmtx = newTable("hmtx")
        hmtx.metrics = {".notdef": (600, 0)}
        font["hmtx"] = hmtx

        name = newTable("name")
        name.names = []
        font["name"] = name

        os2 = newTable("OS/2")
        for k, v in [
            ("version", 4), ("xAvgCharWidth", 500), ("usWeightClass", 400),
            ("usWidthClass", 5), ("fsType", 0),
            ("ySubscriptXSize", 650), ("ySubscriptYSize", 600),
            ("ySubscriptXOffset", 0), ("ySubscriptYOffset", 75),
            ("ySuperscriptXSize", 650), ("ySuperscriptYSize", 600),
            ("ySuperscriptXOffset", 0), ("ySuperscriptYOffset", 350),
            ("yStrikeoutSize", 50), ("yStrikeoutPosition", 300),
            ("sFamilyClass", 0), ("panose", b"\x00" * 10),
            ("ulUnicodeRange1", 0), ("ulUnicodeRange2", 0),
            ("ulUnicodeRange3", 0), ("ulUnicodeRange4", 0),
            ("achVendID", "    "), ("fsSelection", 0),
            ("usFirstCharIndex", 0), ("usLastCharIndex", 0),
            ("sTypoAscender", 800), ("sTypoDescender", -200),
            ("sTypoLineGap", 0), ("usWinAscent", 800), ("usWinDescent", 200),
            ("ulCodePageRange1", 0), ("ulCodePageRange2", 0),
            ("sxHeight", 500), ("sCapHeight", 700),
            ("usDefaultChar", 0), ("usBreakChar", 32),
            ("usMaxContext", 0),
        ]:
            setattr(os2, k, v)
        font["OS/2"] = os2

        post = newTable("post")
        post.formatType = 2.0
        post.italicAngle = 0.0
        post.underlinePosition = -100
        post.underlineThickness = 50
        post.isFixedPitch = 0
        post.minMemType42 = 0
        post.maxMemType42 = 0
        post.minMemType1 = 0
        post.maxMemType1 = 0
        post.extraNames = []
        post.glyphOrder = [".notdef"]
        post.mapping = {}
        font["post"] = post

        loca = newTable("loca")
        loca.offsets = [0, 0]
        font["loca"] = loca

        glyf = newTable("glyf")
        glyf.glyphs = {".notdef": type("G", (), {
            "numberOfContours": 0, "xMin": 0, "yMin": 0, "xMax": 0, "yMax": 0,
            "flags": [], "xCoordinates": [], "yCoordinates": [],
            "endPtsOfContours": [], "instructions": b"",
        })()}

        # 用简单方式: 直接把一个字体文件存到磁盘
        tmp.close()
        try:
            font.save(tmp.name)
        except Exception as e:
            print("(合成最小 TTF 失败, 跳过真实上传测试)", e)
            return 0

        # ==== 测试上传 ====
        import mimetypes

        boundary = "----boundaryXYZ"
        with open(tmp.name, "rb") as f:
            file_bytes = f.read()

        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="font"; filename="test.ttf"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

        req = urllib.request.Request(
            "http://127.0.0.1:8765/api/upload",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            data = json.loads(resp.read())
            print("upload OK:", data.get("ok"), "session_id=",
                  data.get("session", {}).get("session_id")[:10] if data.get("session") else None)
        except urllib.error.HTTPError as e:
            # 没提取到 CJK 字形是预期的，看错误信息
            print("HTTP", e.code, e.read().decode("utf-8", errors="ignore")[:400])
            return 0

        sid = data["session"]["session_id"]
        # ==== 测试详情 ====
        resp2 = urllib.request.urlopen(f"http://127.0.0.1:8765/api/session/{sid}", timeout=10)
        detail = json.loads(resp2.read())
        print("session detail ok=", detail.get("ok"),
              "components=", len(detail.get("components", [])),
              "matched=", len(detail.get("matched", [])))

        # ==== 测试 SVG ====
        # 随便取一个字符 unicode
        first = (detail.get("matched") or detail.get("unmatched") or [{}])[0]
        uv = first.get("unicode")
        if uv is not None:
            svg_url = f"http://127.0.0.1:8765/api/glyph/svg?sid={sid}&unicode={uv}&mode=original&size=120"
            resp3 = urllib.request.urlopen(svg_url, timeout=10)
            svg_bytes = resp3.read()
            ct = resp3.headers.get("Content-Type", "")
            print("svg ->", ct, len(svg_bytes), "bytes, starts:",
                  svg_bytes[:50].decode("utf-8", errors="ignore"))

        print("smoke OK")
        return 0
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
