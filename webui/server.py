"""
HanFont Compress Web UI。

启动:
    python webui/server.py --host 127.0.0.1 --port 8765

浏览器访问:
    http://127.0.0.1:8765/

提供的功能:
    1. 上传 TTF/OTF 字体 → 自动跑流水线
    2. 展示部件库列表
    3. 展示匹配/未匹配的字 (分组)
    4. 原始字形 vs 压缩重建字形 的 SVG 对比图
    5. 一键下载 .hfc 文件
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 确保可以 import src/ 下的 hfc 包
_webui_dir = Path(__file__).parent.resolve()
_project_root = _webui_dir.parent
_src_path = _project_root / "src"
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
)
from werkzeug.utils import secure_filename

from hfc.component_library import Component, ComponentLibrary
from hfc.component_matcher import (
    ComponentMatcher,
    EncodedChar,
    MatchConfig,
)
from hfc.decomposer import (
    SharedComponent,
    SubContour,
    contour_decompose,
    find_shared_components,
)
from hfc.glyph_extractor import (
    GlyphContours,
    GlyphExtractor,
    GlyphExtractResult,
)
from hfc.hfc_encoder import EncodeOptions, HFCEncoder
from hfc.reconstructor import GlyphReconstructor


# ============================================================================
# 会话存储 (内存)
# ============================================================================

@dataclass
class PipelineSession:
    """一次上传+流水线的完整结果"""

    session_id: str
    font_name: str
    glyphs: list[GlyphContours]
    library: ComponentLibrary
    encoded_chars: list[EncodedChar]
    hfc_bytes: bytes
    stats: dict[str, Any]
    char_subs: dict[str, list[SubContour]] = field(default_factory=dict)
    shared: list[SharedComponent] = field(default_factory=list)
    created_at: float = 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "font_name": self.font_name,
            "created_at": self.created_at,
            "stats": self.stats,
            "component_count": len(self.library),
            "char_count": len(self.encoded_chars),
            "hfc_size": len(self.hfc_bytes),
            "shared_component_count": len(self.shared),
        }


_SESSIONS: dict[str, PipelineSession] = {}


# ============================================================================
# Flask 应用
# ============================================================================

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64MB 上限


def run_pipeline(font_bytes: bytes, font_name: str, max_chars: int = 200,
                 similarity_threshold: float = 0.60,
                 decomposer_tolerance: float = 0.05,
                 ) -> PipelineSession:
    """
    跑完整流水线:
      提取字形 → 构建部件库 → 匹配编码 → .hfc 编码
      额外: 几何连通域拆分 + 跨字共享部件检测
    """
    t0 = time.time()

    # ---------- 写入临时文件 (fontTools 需要 path) ----------
    fd, tmp_path = tempfile.mkstemp(suffix=Path(font_name).suffix or ".ttf")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(font_bytes)

        # ---------- 1. 提取字形 ----------
        extractor = GlyphExtractor()
        glyphs: list[GlyphContours] = []
        for r in extractor.extract_all_cjk(tmp_path):
            r: GlyphExtractResult = r
            if r.success and not r.glyph.is_empty():
                glyphs.append(r.glyph)
                if 0 < max_chars <= len(glyphs):
                    break

        if not glyphs:
            raise RuntimeError("字体中未提取到任何有效的 CJK 汉字字形")

        t_extract = time.time()

        # ---------- 2. 构建部件库 (取前 N 个字形作为"伪部件") ----------
        library = ComponentLibrary()
        N = min(20, len(glyphs))
        for i, g in enumerate(glyphs[:N]):
            c = chr(g.unicode) if 0 < g.unicode < 0x110000 else f"char{i:02d}"
            comp = Component(
                id=f"part_{i:02d}",
                name=f"部件_{c}",
                semantic=c,
            )
            comp.add_sample_from_glyph(g)
            library.add_component(comp)

        t_lib = time.time()

        # ---------- 3. 匹配编码 ----------
        matcher = ComponentMatcher(
            library,
            config=MatchConfig(similarity_threshold=similarity_threshold),
        )
        encoded: list[EncodedChar] = [matcher.match(g) for g in glyphs]
        t_match = time.time()

        # ---------- 3b. 几何连通域拆分 + 跨字共享部件检测 ----------
        char_subs, shared = find_shared_components(
            glyphs, tolerance=decomposer_tolerance,
        )
        t_decompose = time.time()

        # ---------- 4. .hfc 编码 ----------
        encoder = HFCEncoder()
        options = EncodeOptions(use_brotli=True, include_component_samples=True)
        hfc_bytes, _hfc_result = encoder.encode_to_bytes(
            library, encoded, options=options,
        )
        t_encode = time.time()

        # ---------- 5. 统计 ----------
        component_mode = sum(1 for e in encoded if e.mode == "COMPONENT")
        raw_mode = sum(1 for e in encoded if e.mode == "RAW")
        scores = [e.match_score for e in encoded if e.mode == "COMPONENT"]
        avg_score = sum(scores) / len(scores) if scores else 0.0

        total_subs = sum(len(v) for v in char_subs.values())
        shared_total = sum(s.appearance_count for s in shared)

        stats = {
            "total_chars": len(glyphs),
            "component_mode": component_mode,
            "raw_mode": raw_mode,
            "avg_match_score": avg_score,
            "hfc_size": len(hfc_bytes),
            "time_extract_ms": (t_extract - t0) * 1000,
            "time_library_ms": (t_lib - t_extract) * 1000,
            "time_match_ms": (t_match - t_lib) * 1000,
            "time_decompose_ms": (t_decompose - t_match) * 1000,
            "time_encode_ms": (t_encode - t_decompose) * 1000,
            "time_total_ms": (t_encode - t0) * 1000,
            "total_sub_contours": total_subs,
            "shared_components": len(shared),
            "shared_appearances": shared_total,
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return PipelineSession(
        session_id=uuid.uuid4().hex,
        font_name=font_name,
        glyphs=glyphs,
        library=library,
        encoded_chars=encoded,
        hfc_bytes=hfc_bytes,
        stats=stats,
        char_subs=char_subs,
        shared=shared,
        created_at=time.time(),
    )


# ============================================================================
# 路由
# ============================================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """上传字体文件并跑流水线。"""
    if "font" not in request.files:
        return jsonify({"ok": False, "error": "未找到 font 文件字段"}), 400

    file = request.files["font"]
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "未选择文件"}), 400

    filename = secure_filename(file.filename)
    allowed = (".ttf", ".otf", ".woff", ".woff2")
    if not filename.lower().endswith(allowed):
        return jsonify({
            "ok": False,
            "error": f"仅支持 {allowed}",
        }), 400

    max_chars = int(request.form.get("max_chars", 200))
    threshold = float(request.form.get("threshold", 0.60))

    font_bytes = file.read()
    if not font_bytes:
        return jsonify({"ok": False, "error": "文件为空"}), 400

    try:
        session = run_pipeline(
            font_bytes,
            filename,
            max_chars=max_chars,
            similarity_threshold=threshold,
        )
    except Exception as ex:
        return jsonify({"ok": False, "error": f"流水线失败: {ex}"}), 500

    _SESSIONS[session.session_id] = session

    return jsonify({
        "ok": True,
        "session": session.summary(),
    })


@app.route("/api/session/<sid>")
def api_session(sid: str):
    """获取一个 session 的详细数据。"""
    session = _SESSIONS.get(sid)
    if session is None:
        return jsonify({"ok": False, "error": "session 不存在"}), 404

    # 部件库列表
    components = []
    for comp in session.library:
        comp: Component = comp
        components.append({
            "id": comp.id,
            "name": comp.name,
            "semantic": comp.semantic,
            "sample_count": comp.num_samples(),
        })

    # 匹配成功的字 + 未匹配的字
    matched = []
    unmatched = []
    for enc in session.encoded_chars:
        char_obj = chr(enc.unicode) if 0 < enc.unicode < 0x110000 else "?"
        entry = {
            "unicode": enc.unicode,
            "unicode_hex": f"U+{enc.unicode:04X}",
            "char": char_obj,
            "mode": enc.mode,
            "match_score": enc.match_score,
            "parts": [p.component_id for p in enc.parts],
        }
        if enc.mode == "COMPONENT":
            matched.append(entry)
        else:
            unmatched.append(entry)

    matched.sort(key=lambda x: -x["match_score"])

    # 共享部件 (新功能): 每个 shared component 列出它出现的字和在该字中的子轮廓编号
    shared_list = []
    for idx, sc in enumerate(session.shared):
        shared_list.append({
            "index": idx,
            "tag": sc.tag,
            "chars": sc.chars,
            "appearance_count": sc.appearance_count,
            "mean_similarity": round(sc.mean_similarity, 4),
        })

    # 每个字的子轮廓统计
    char_decompose = {}
    for ch, subs in session.char_subs.items():
        char_decompose[ch] = [{
            "index": i,
            "size": int(max(
                s.bbox[2] - s.bbox[0],
                s.bbox[3] - s.bbox[1],
            )),
        } for i, s in enumerate(subs)]

    return jsonify({
        "ok": True,
        "session": session.summary(),
        "components": components,
        "matched": matched,
        "unmatched": unmatched,
        "shared": shared_list,
        "char_decompose": char_decompose,
    })


@app.route("/api/sub/svg", methods=["GET"])
def api_sub_svg():
    """
    渲染某个字中的第 i 个子轮廓 (部件)。

    query:
        sid=...   session id
        char=...  字 (如 "明")
        index=... 子轮廓索引
        size=...  像素大小
    """
    sid = request.args.get("sid", "")
    session = _SESSIONS.get(sid)
    if session is None:
        return "session not found", 404

    ch = request.args.get("char", "")
    idx = int(request.args.get("index", "0"))
    subs = session.char_subs.get(ch, [])
    if not subs or idx >= len(subs):
        return "sub not found", 404

    size = int(request.args.get("size", 120))
    sub = subs[idx]

    # 把 SubContour 转成临时 GlyphContours 再走 SVG 渲染
    glyph = GlyphContours(unicode=ord(ch))
    for c in sub.contours:
        new_c = glyph.add_contour()
        for p in c.points:
            new_c.add_point(p.x, p.y, p.is_on_curve)
    glyph.recompute_bbox()

    svg = glyph_to_svg(glyph, size=size)
    return svg, 200, {"Content-Type": "image/svg+xml; charset=utf-8"}


@app.route("/api/shared/svg", methods=["GET"])
def api_shared_svg():
    """渲染某个共享部件的代表轮廓。"""
    sid = request.args.get("sid", "")
    session = _SESSIONS.get(sid)
    if session is None:
        return "session not found", 404

    idx = int(request.args.get("index", "0"))
    if idx < 0 or idx >= len(session.shared):
        return "index out of range", 404

    size = int(request.args.get("size", 140))
    sc = session.shared[idx]

    glyph = GlyphContours(unicode=0x0000)
    for c in sc.representative.contours:
        new_c = glyph.add_contour()
        for p in c.points:
            new_c.add_point(p.x, p.y, p.is_on_curve)
    glyph.recompute_bbox()

    svg = glyph_to_svg(glyph, size=size)
    return svg, 200, {"Content-Type": "image/svg+xml; charset=utf-8"}


@app.route("/api/glyph/svg", methods=["GET"])
def api_glyph_svg():
    """
    生成某个字形的 SVG。

    query:
        sid=...   session id
        index=... 字形在 glyphs 列表中的下标
        mode=original | reconstructed  原始字形 / 部件重建后的字形

    也可以用 unicode= 来代替 index=。
    """
    sid = request.args.get("sid", "")
    session = _SESSIONS.get(sid)
    if session is None:
        return "session not found", 404

    mode = request.args.get("mode", "original")
    target: GlyphContours | None = None

    if request.args.get("unicode"):
        uv = int(request.args["unicode"], 0)
        for g in session.glyphs:
            if g.unicode == uv:
                target = g
                break
    elif request.args.get("index"):
        idx = int(request.args["index"])
        if 0 <= idx < len(session.glyphs):
            target = session.glyphs[idx]

    if target is None:
        return "glyph not found", 404

    glyph_to_render: GlyphContours

    if mode == "reconstructed":
        # 找到对应的 EncodedChar 并重建
        recon = GlyphReconstructor(session.library)
        enc = next(
            (e for e in session.encoded_chars if e.unicode == target.unicode),
            None,
        )
        if enc is None or enc.mode != "COMPONENT":
            # 无法重建 → 返回原始
            glyph_to_render = target
        else:
            glyph_to_render = recon.reconstruct(enc).glyph
    else:
        glyph_to_render = target

    size = int(request.args.get("size", 180))
    svg = glyph_to_svg(glyph_to_render, size=size)
    return svg, 200, {"Content-Type": "image/svg+xml; charset=utf-8"}


@app.route("/api/download/hfc/<sid>")
def api_download_hfc(sid: str):
    session = _SESSIONS.get(sid)
    if session is None:
        return "session not found", 404

    bio = io.BytesIO(session.hfc_bytes)
    filename = Path(session.font_name).stem + ".hfc"
    return send_file(
        bio,
        as_attachment=True,
        download_name=filename,
        mimetype="application/octet-stream",
    )


@app.route("/api/sessions")
def api_sessions():
    return jsonify({
        "ok": True,
        "sessions": [s.summary() for s in _SESSIONS.values()],
    })


# ============================================================================
# SVG 生成
# ============================================================================

def glyph_to_svg(glyph: GlyphContours, size: int = 200,
                 stroke: str = "#3b82f6", fill: str = "#dbeafe") -> str:
    """
    将 GlyphContours 转为 SVG 字符串。
    """
    if glyph is None or glyph.is_empty():
        # 返回一个占位的空方框
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{size}" height="{size}" '
            f'viewBox="0 0 {size} {size}"></svg>'
        )

    glyph.recompute_bbox()
    x_min, y_min, x_max, y_max = [float(v) for v in glyph.bbox]
    w = x_max - x_min
    h = y_max - y_min
    if w <= 0:
        w = 1
    if h <= 0:
        h = 1

    # 留出 margin
    margin = size * 0.08
    inner = size - 2 * margin

    # 计算缩放 (保持宽高比，以较大的维度为准)
    scale = inner / max(w, h)
    offset_x = margin - x_min * scale + (inner - w * scale) / 2
    # SVG 的 y 是向下的，字体坐标 y 是向上的，需要翻转
    offset_y = margin + y_max * scale + (inner - h * scale) / 2

    def fx(x: float) -> float:
        return offset_x + x * scale

    def fy(y: float) -> float:
        return offset_y - y * scale

    paths: list[str] = []
    for contour in glyph.contours:
        if not contour.points:
            continue
        pts = contour.points
        d = [f"M {fx(pts[0].x):.1f} {fy(pts[0].y):.1f}"]
        i = 1
        while i < len(pts):
            p = pts[i]
            if p.is_on_curve:
                d.append(f"L {fx(p.x):.1f} {fy(p.y):.1f}")
                i += 1
            else:
                # 二次贝塞尔曲线 (需要控制点 + 终点)
                ctrl = p
                if i + 1 < len(pts):
                    end = pts[i + 1]
                    d.append(
                        f"Q {fx(ctrl.x):.1f} {fy(ctrl.y):.1f} "
                        f"{fx(end.x):.1f} {fy(end.y):.1f}"
                    )
                    i += 2
                else:
                    # 没有终点则退化为直线
                    d.append(f"L {fx(ctrl.x):.1f} {fy(ctrl.y):.1f}")
                    i += 1
        d.append("Z")
        paths.append(" ".join(d))

    path_str = " ".join(paths)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}">\n'
        f'  <rect x="0" y="0" width="{size}" height="{size}" '
        f'fill="#fafafa" stroke="#e5e7eb" stroke-width="1"/>\n'
        f'  <path d="{path_str}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>\n'
        f'</svg>\n'
    )


# ============================================================================
# CLI 入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="HanFont Compress Web UI")
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    print(f"HanFont Compress Web UI 启动: http://{args.host}:{args.port}/")
    print("  Ctrl+C 停止。")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
