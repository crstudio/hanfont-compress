"""
测试模块4: 人工审核工具 (HumanReviewer)

Usage:
    pytest tests/test_human_reviewer.py -v
"""

import sys
import tempfile
from pathlib import Path

import pytest

# 添加 src 目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hfc.component_matcher import EncodedChar, PartInstance, Transform
from hfc.glyph_extractor import Contour, ContourPoint, GlyphContours
from hfc.human_reviewer import (
    HumanReviewer,
    MANUAL_REVIEW_LIMIT,
    ReviewDecision,
    ReviewItem,
    ReviewReport,
    ReviewSession,
    review_with_simple_threshold,
)


# 辅助函数
def _make_glyph(unicode_val: int) -> GlyphContours:
    glyph = GlyphContours(unicode=unicode_val, bbox=(0, 0, 6400, 6400))
    contour = glyph.add_contour()
    contour.add_point(0, 0, True)
    contour.add_point(6400, 0, True)
    contour.add_point(6400, 6400, True)
    contour.add_point(0, 6400, True)
    return glyph


def _make_encoded(
    unicode_val: int,
    mode: str = "COMPONENT",
    score: float = 0.9,
    needs_review: bool = False,
) -> EncodedChar:
    enc = EncodedChar(unicode=unicode_val, mode=mode, match_score=score)
    enc.manual_review_needed = needs_review
    if mode == "COMPONENT":
        enc.parts = [
            PartInstance(
                component_id=f"part_{unicode_val}",
                transform=Transform.identity(),
                similarity=score,
            )
        ]
    return enc


# ============================================================================
# ReviewDecision 测试
# ============================================================================

class TestReviewDecision:
    """测试审核决策枚举"""

    def test_values(self):
        """测试枚举值存在"""
        assert ReviewDecision.ACCEPT_AS_IS.value == "accept_as_is"
        assert ReviewDecision.FORCE_RAW.value == "force_raw"
        assert ReviewDecision.SKIP.value == "skip"


# ============================================================================
# ReviewItem 测试
# ============================================================================

class TestReviewItem:
    """测试单个审核条目"""

    def test_creation(self):
        """测试创建"""
        glyph = _make_glyph(0x6C49)
        enc = _make_encoded(0x6C49, needs_review=True)
        item = ReviewItem(unicode=0x6C49, glyph=glyph, encoded_char=enc)

        assert item.decision == ReviewDecision.SKIP
        assert item.reviewer_notes == ""

    def test_char_repr(self):
        """测试字符表示"""
        glyph = _make_glyph(0x6C49)
        enc = _make_encoded(0x6C49)
        item = ReviewItem(unicode=0x6C49, glyph=glyph, encoded_char=enc)

        repr_str = item.char_repr()
        assert "U+6C49" in repr_str

    def test_to_dict(self):
        """测试字典序列化"""
        glyph = _make_glyph(0x6C49)
        enc = _make_encoded(0x6C49, score=0.95, needs_review=True)
        item = ReviewItem(
            unicode=0x6C49,
            glyph=glyph,
            encoded_char=enc,
            decision=ReviewDecision.ACCEPT_AS_IS,
            reviewer_notes="looks good",
        )
        d = item.to_dict()

        assert d["unicode"] == 0x6C49
        assert d["decision"] == "accept_as_is"
        assert d["notes"] == "looks good"
        assert abs(d["match_score"] - 0.95) < 1e-6


# ============================================================================
# ReviewReport 测试
# ============================================================================

class TestReviewReport:
    """测试审核报告"""

    def test_summary(self):
        """测试报告摘要"""
        report = ReviewReport(
            total_reviewable=100,
            reviewed=50,
            accepted_as_is=30,
            accepted_component=10,
            forced_raw=8,
            new_parts=2,
            skipped=0,
            over_limit_ignored=0,
        )
        summary = report.summary()

        assert "100" in summary
        assert "50" in summary
        assert "30" in summary

    def test_to_dict(self):
        report = ReviewReport(total_reviewable=50, reviewed=20, accepted_as_is=20)
        d = report.to_dict()
        assert d["total_reviewable"] == 50
        assert d["reviewed"] == 20


# ============================================================================
# ReviewSession 测试
# ============================================================================

class TestReviewSession:
    """测试审核会话"""

    def test_empty_session(self):
        """测试空会话"""
        session = ReviewSession()
        assert len(session) == 0
        assert session.reviewed_count() == 0

    def test_add_item(self):
        """测试添加条目"""
        session = ReviewSession()
        glyph = _make_glyph(0x6C49)
        enc = _make_encoded(0x6C49, needs_review=True)
        session.add_item(
            ReviewItem(unicode=0x6C49, glyph=glyph, encoded_char=enc)
        )
        assert len(session) == 1
        assert session.needs_review()

    def test_needs_review_filtering(self):
        """测试待审核过滤"""
        session = ReviewSession()

        for uv in range(0x6C40, 0x6C45):
            glyph = _make_glyph(uv)
            enc = _make_encoded(uv, needs_review=True)
            session.add_item(ReviewItem(unicode=uv, glyph=glyph, encoded_char=enc))

        # 标记前2个为已处理
        session.items[0].decision = ReviewDecision.ACCEPT_AS_IS
        session.items[1].decision = ReviewDecision.FORCE_RAW

        assert session.reviewed_count() == 2
        assert len(session.needs_review()) == 3

    def test_save_and_load_report(self):
        """测试会话保存"""
        session = ReviewSession()
        glyph = _make_glyph(0x6C49)
        enc = _make_encoded(0x6C49, needs_review=True)
        item = ReviewItem(
            unicode=0x6C49,
            glyph=glyph,
            encoded_char=enc,
            decision=ReviewDecision.ACCEPT_COMPONENT,
        )
        session.add_item(item)
        session.report.total_reviewable = 1
        session.report.reviewed = 1
        session.report.accepted_component = 1
        session.completed = True

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            session.save(str(path))
            assert path.exists()

            # 验证 JSON 内容可解析
            import json
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["completed"] is True
            assert len(data["items"]) == 1


# ============================================================================
# HumanReviewer 测试
# ============================================================================

class TestHumanReviewer:
    """测试 HumanReviewer 主类"""

    def _make_chars_with_glyphs(
        self,
        count: int = 10,
        all_need_review: bool = True,
    ) -> list[tuple[EncodedChar, GlyphContours]]:
        """构造测试数据"""
        results = []
        for i in range(count):
            uv = 0x6C40 + i
            score = 0.9 - (i * 0.02)  # 递减分数
            enc = _make_encoded(uv, score=score, needs_review=all_need_review)
            glyph = _make_glyph(uv)
            results.append((enc, glyph))
        return results

    def test_collect_reviewable(self):
        """测试收集需审核条目"""
        reviewer = HumanReviewer()
        chars = self._make_chars_with_glyphs(count=10)
        session = reviewer.collect_reviewable(chars)

        assert len(session) == 10

    def test_collect_reviewable_filters_nonreview(self):
        """测试过滤掉不需审核的条目"""
        reviewer = HumanReviewer()
        chars = self._make_chars_with_glyphs(count=10)
        # 标记一半为不需要审核
        for i in range(5):
            chars[i][0].manual_review_needed = False

        session = reviewer.collect_reviewable(chars)
        assert len(session) == 5

    def test_accept_all(self):
        """测试全部接受"""
        reviewer = HumanReviewer()
        chars = self._make_chars_with_glyphs(count=10)
        session = reviewer.collect_reviewable(chars)

        report = reviewer.accept_all(session)

        assert report.reviewed == 10
        assert report.accepted_as_is == 10
        assert report.forced_raw == 0

        # 验证状态已清除
        for item in session.items:
            assert item.encoded_char.manual_review_needed is False

    def test_force_raw_all(self):
        """测试全部降级"""
        reviewer = HumanReviewer()
        chars = self._make_chars_with_glyphs(count=10)
        session = reviewer.collect_reviewable(chars)

        report = reviewer.force_raw_all(session)

        assert report.reviewed == 10
        assert report.forced_raw == 10

        # 验证编码结果已被修改
        for item in session.items:
            assert item.encoded_char.mode == "RAW"
            assert item.encoded_char.parts == []

    def test_review_programmatic(self):
        """测试程序化审核"""
        reviewer = HumanReviewer()
        chars = self._make_chars_with_glyphs(count=10)
        session = reviewer.collect_reviewable(chars)

        # 决策: 前5个接受，后5个降级
        counter = [0]

        def decision_fn(item: ReviewItem) -> ReviewDecision:
            counter[0] += 1
            if counter[0] <= 5:
                return ReviewDecision.ACCEPT_AS_IS
            return ReviewDecision.FORCE_RAW

        report = reviewer.review_programmatic(session, decision_fn)

        assert report.accepted_as_is == 5
        assert report.forced_raw == 5
        assert report.reviewed == 10

    def test_auto_accept_threshold(self):
        """测试自动接受阈值"""
        reviewer = HumanReviewer(auto_accept_threshold=0.85)
        chars = self._make_chars_with_glyphs(count=10)
        # 分数 0.90, 0.88, 0.86, 0.84, 0.82, 0.80, 0.78, 0.76, 0.74, 0.72
        session = reviewer.collect_reviewable(chars)

        # 用保守策略: 不确定的降级为 RAW
        def decision_fn(item: ReviewItem) -> ReviewDecision:
            return ReviewDecision.FORCE_RAW

        report = reviewer.review_programmatic(session, decision_fn)

        # 前3个(分数 0.90, 0.88, 0.86) 应该被自动接受
        # 后7个走决策函数 -> FORCE_RAW
        assert report.accepted_as_is >= 3
        assert report.forced_raw >= 7

    def test_hard_limit_enforcement(self):
        """测试硬约束: 超过1000条自动降级"""
        reviewer = HumanReviewer(limit=5)
        chars = self._make_chars_with_glyphs(count=15)
        session = reviewer.collect_reviewable(chars)

        def decision_fn(item: ReviewItem) -> ReviewDecision:
            return ReviewDecision.ACCEPT_AS_IS

        report = reviewer.review_programmatic(session, decision_fn)

        # 前5个被接受，后10个因为超限被降级
        assert report.accepted_as_is == 5
        assert report.forced_raw == 10
        assert report.over_limit_ignored == 10

    def test_review_summary_output(self):
        """测试报告摘要字符串不为空"""
        reviewer = HumanReviewer()
        chars = self._make_chars_with_glyphs(count=5)
        session = reviewer.collect_reviewable(chars)
        report = reviewer.accept_all(session)

        summary = report.summary()
        assert len(summary) > 0
        print(f"\n{summary}")

    def test_limit_constant(self):
        """测试硬约束常量"""
        assert MANUAL_REVIEW_LIMIT == 1000

    def test_custom_limit(self):
        """测试自定义审核上限"""
        reviewer = HumanReviewer(limit=3)
        chars = self._make_chars_with_glyphs(count=10)
        session = reviewer.collect_reviewable(chars)

        report = reviewer.accept_all(session)
        assert report.reviewed == 3
        assert report.over_limit_ignored == 7


# ============================================================================
# 便捷函数测试
# ============================================================================

class TestConvenienceFunctions:
    """测试便捷函数"""

    def _make_mixed_chars(
        self,
    ) -> list[tuple[EncodedChar, GlyphContours]]:
        """构造混合分数的测试数据"""
        chars = []

        # 高分 (>= 0.85)
        for i in range(3):
            uv = 0x6C40 + i
            enc = _make_encoded(uv, score=0.92, needs_review=True)
            glyph = _make_glyph(uv)
            chars.append((enc, glyph))

        # 中等 (0.60-0.85)
        for i in range(4):
            uv = 0x6C50 + i
            enc = _make_encoded(uv, score=0.75, needs_review=True)
            glyph = _make_glyph(uv)
            chars.append((enc, glyph))

        # 低分 (<= 0.60)
        for i in range(3):
            uv = 0x6C60 + i
            enc = _make_encoded(uv, score=0.55, needs_review=True)
            glyph = _make_glyph(uv)
            chars.append((enc, glyph))

        return chars

    def test_review_with_simple_threshold(self):
        """测试基于阈值的审核"""
        chars = self._make_mixed_chars()
        results, report = review_with_simple_threshold(
            chars,
            accept_threshold=0.85,
            raw_threshold=0.60,
            limit=1000,
        )

        # 应该处理了所有 10 个
        assert len(results) == 10
        # 低分的应该被降级
        assert report.forced_raw == 3
        # 高分和中等的应该被接受
        assert report.accepted_as_is == 7


# ============================================================================
# 端到端测试
# ============================================================================

class TestEndToEnd:
    """端到端测试"""

    def test_full_workflow(self):
        """完整审核流程"""
        # 1. 准备数据
        chars = []
        for i in range(5):
            uv = 0x6C40 + i
            enc = _make_encoded(uv, score=0.9 - i * 0.03, needs_review=True)
            glyph = _make_glyph(uv)
            chars.append((enc, glyph))

        # 2. 收集待审核
        reviewer = HumanReviewer()
        session = reviewer.collect_reviewable(chars)
        assert len(session) == 5
        assert session.report.total_reviewable == 5

        # 3. 程序化审核: 分数 > 0.80 接受，否则降级
        def decision_fn(item: ReviewItem) -> ReviewDecision:
            if item.encoded_char.match_score > 0.80:
                return ReviewDecision.ACCEPT_AS_IS
            return ReviewDecision.FORCE_RAW

        report = reviewer.review_programmatic(session, decision_fn)

        # 4. 验证结果
        assert report.reviewed == 5
        # 分数 0.90, 0.87, 0.84, 0.81, 0.78
        # 4个 > 0.80 接受, 1个降级
        assert report.accepted_as_is == 4
        assert report.forced_raw == 1

        # 5. 检查报告
        summary = report.summary()
        print(f"\n{summary}")
        assert "5" in summary

    def test_empty_input(self):
        """测试空输入"""
        reviewer = HumanReviewer()
        session = reviewer.collect_reviewable([])

        assert len(session) == 0
        report = reviewer.accept_all(session)
        assert report.reviewed == 0
        summary = report.summary()
        assert "0" in summary


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
