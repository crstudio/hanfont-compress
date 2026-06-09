"""
模块4: 人工审核工具 (HumanReviewer)

用于标记需要人工审核的汉字，并提供程序化/交互式两种审核方式。

核心功能:
1. 收集需要审核的汉字 (EncodedChar 中 manual_review_needed = True)
2. 程序化审核: 通过配置规则自动标记处理结果
3. 交互式审核: 通过命令行/控制台人工操作
4. 统计报告: 输出审核进度和分布情况

硬约束: 人工标注 <= 1000 字 (MANUAL_REVIEW_LIMIT)

参考: docs/technical-design.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Optional

from .component_matcher import EncodedChar, PartInstance
from .glyph_extractor import GlyphContours


# 硬约束: 人工标注上限
MANUAL_REVIEW_LIMIT = 1000


# 审核决策
class ReviewDecision(str, Enum):
    """
    审核决策结果。

    - ACCEPT_AS_IS: 接受当前匹配结果，保持原样
    - ACCEPT_COMPONENT: 接受部件匹配，标记为"已人工确认"
    - FORCE_RAW: 强制降级为原始轮廓存储
    - ADD_NEW_PART: 作为新部件加入部件库
    - SKIP: 跳过，保持待审核状态
    """

    ACCEPT_AS_IS = "accept_as_is"
    ACCEPT_COMPONENT = "accept_component"
    FORCE_RAW = "force_raw"
    ADD_NEW_PART = "add_new_part"
    SKIP = "skip"


@dataclass
class ReviewItem:
    """一个待审核条目"""

    unicode: int
    glyph: GlyphContours
    encoded_char: EncodedChar
    decision: ReviewDecision = ReviewDecision.SKIP
    reviewer_notes: str = ""

    def char_repr(self) -> str:
        """返回可读的字符表示"""
        if 0 < self.unicode < 0x110000:
            return f"U+{self.unicode:04X} ({chr(self.unicode)})"
        return f"U+{self.unicode:04X}"

    def to_dict(self) -> dict:
        """序列化"""
        return {
            "unicode": self.unicode,
            "decision": self.decision.value,
            "notes": self.reviewer_notes,
            "original_mode": self.encoded_char.mode,
            "match_score": self.encoded_char.match_score,
            "parts": [p.component_id for p in self.encoded_char.parts],
        }


@dataclass
class ReviewReport:
    """审核报告"""

    total_reviewable: int = 0
    reviewed: int = 0
    accepted_as_is: int = 0
    accepted_component: int = 0
    forced_raw: int = 0
    new_parts: int = 0
    skipped: int = 0
    over_limit_ignored: int = 0

    def summary(self) -> str:
        """返回简要摘要"""
        lines = [
            "=" * 50,
            "人工审核报告",
            "=" * 50,
            f"待审核总数:   {self.total_reviewable}",
            f"已审核:       {self.reviewed}",
            f"  └─ 接受原样 (ACCEPT_AS_IS):      {self.accepted_as_is}",
            f"  └─ 接受部件 (ACCEPT_COMPONENT):  {self.accepted_component}",
            f"  └─ 降级原始 (FORCE_RAW):         {self.forced_raw}",
            f"  └─ 新增部件 (ADD_NEW_PART):      {self.new_parts}",
            f"  └─ 跳过 (SKIP):                  {self.skipped}",
            f"超限忽略(> {MANUAL_REVIEW_LIMIT}): {self.over_limit_ignored}",
            "=" * 50,
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "total_reviewable": self.total_reviewable,
            "reviewed": self.reviewed,
            "accepted_as_is": self.accepted_as_is,
            "accepted_component": self.accepted_component,
            "forced_raw": self.forced_raw,
            "new_parts": self.new_parts,
            "skipped": self.skipped,
            "over_limit_ignored": self.over_limit_ignored,
            "manual_review_limit": MANUAL_REVIEW_LIMIT,
        }


@dataclass
class ReviewSession:
    """
    一个审核会话。

    保存审核状态，便于持久化和恢复。
    """

    items: list[ReviewItem] = field(default_factory=list)
    report: ReviewReport = field(default_factory=ReviewReport)
    completed: bool = False

    def add_item(self, item: ReviewItem) -> None:
        self.items.append(item)

    def needs_review(self) -> list[ReviewItem]:
        """返回仍待审核的条目"""
        return [i for i in self.items if i.decision == ReviewDecision.SKIP]

    def reviewed_count(self) -> int:
        """已审核数量"""
        return sum(1 for i in self.items if i.decision != ReviewDecision.SKIP)

    def save(self, path: str | Path) -> None:
        """保存会话到 JSON 文件"""
        data = {
            "items": [item.to_dict() for item in self.items],
            "report": self.report.to_dict(),
            "completed": self.completed,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def __len__(self) -> int:
        return len(self.items)


class HumanReviewer:
    """
    人工审核工具。

    使用方式1: 程序化审核（基于规则）
        reviewer = HumanReviewer()
        results = reviewer.review_programmatic(
            chars,
            decision_fn=lambda item: ReviewDecision.ACCEPT_AS_IS
                if item.encoded_char.match_score > 0.85
                else ReviewDecision.FORCE_RAW,
        )

    使用方式2: 交互式审核（控制台）
        reviewer = HumanReviewer()
        results = reviewer.review_interactive(chars, max_reviews=500)

    使用方式3: 接受全部（快速通道）
        results = reviewer.accept_all(chars)
    """

    def __init__(
        self,
        limit: int = MANUAL_REVIEW_LIMIT,
        auto_accept_threshold: Optional[float] = None,
    ):
        """
        Args:
            limit: 人工标注上限，超过此数量的条目自动降级为 RAW
            auto_accept_threshold: 相似度高于此阈值自动接受 (None 表示不自动)
        """
        self.limit = limit
        self.auto_accept_threshold = auto_accept_threshold

    # ------------------------------------------------------------------
    # 主接口
    # ------------------------------------------------------------------

    def collect_reviewable(
        self,
        encoded_chars: Iterable[tuple[EncodedChar, GlyphContours]],
    ) -> ReviewSession:
        """
        从一组编码结果中收集需要审核的条目。

        Args:
            encoded_chars: (encoded_char, glyph) 元组序列

        Returns:
            ReviewSession: 包含所有需要审核的条目
        """
        session = ReviewSession()

        for enc, glyph in encoded_chars:
            if enc.manual_review_needed and enc.mode == "COMPONENT":
                item = ReviewItem(
                    unicode=enc.unicode,
                    glyph=glyph,
                    encoded_char=enc,
                )
                session.add_item(item)

        session.report.total_reviewable = len(session)
        return session

    def review_programmatic(
        self,
        session: ReviewSession,
        decision_fn: Callable[[ReviewItem], ReviewDecision],
    ) -> ReviewReport:
        """
        程序化审核: 用自定义决策函数处理条目。

        Args:
            session: 待审核会话
            decision_fn: (ReviewItem) -> ReviewDecision

        Returns:
            ReviewReport: 审核报告
        """
        report = session.report
        reviewed_counter = 0

        for item in session.items:
            if item.decision != ReviewDecision.SKIP:
                continue

            # 检查硬约束
            if reviewed_counter >= self.limit:
                report.over_limit_ignored += 1
                # 超限的条目: 强制降级为 RAW
                self._apply_decision(item, ReviewDecision.FORCE_RAW, report)
                continue

            # 阈值自动接受
            if self.auto_accept_threshold is not None:
                score = item.encoded_char.match_score
                if score >= self.auto_accept_threshold:
                    self._apply_decision(
                        item, ReviewDecision.ACCEPT_AS_IS, report
                    )
                    reviewed_counter += 1
                    continue

            # 应用用户决策函数
            decision = decision_fn(item)
            self._apply_decision(item, decision, report)
            if decision != ReviewDecision.SKIP:
                reviewed_counter += 1

        report.reviewed = reviewed_counter
        session.report = report
        return report

    def accept_all(
        self,
        session: ReviewSession,
    ) -> ReviewReport:
        """
        快速通道: 接受所有待审核条目，保持原样。

        不检查硬约束 (假设调用方已确认数量)。

        Args:
            session: 待审核会话

        Returns:
            ReviewReport
        """
        report = session.report

        for item in session.items:
            if item.decision == ReviewDecision.SKIP:
                if report.reviewed >= self.limit:
                    report.over_limit_ignored += 1
                    self._apply_decision(item, ReviewDecision.FORCE_RAW, report)
                else:
                    self._apply_decision(
                        item, ReviewDecision.ACCEPT_AS_IS, report
                    )
                    report.reviewed += 1

        session.report = report
        return report

    def force_raw_all(
        self,
        session: ReviewSession,
    ) -> ReviewReport:
        """
        将所有待审核条目降级为原始轮廓存储。

        这是最保守的策略——对不确定的字全部保留原始数据。
        """
        report = session.report

        for item in session.items:
            if item.decision == ReviewDecision.SKIP:
                if report.reviewed >= self.limit:
                    report.over_limit_ignored += 1
                self._apply_decision(item, ReviewDecision.FORCE_RAW, report)
                report.reviewed += 1

        session.report = report
        return report

    def review_interactive(
        self,
        session: ReviewSession,
        max_reviews: Optional[int] = None,
    ) -> ReviewReport:
        """
        交互式命令行审核 (供调试和小批量处理使用)。

        对每个待审核字，显示信息并等待用户决策:
            a = 接受原样  c = 接受为部件
            r = 降级原始  n = 作为新部件
            s = 跳过       q = 退出

        Args:
            session: 待审核会话
            max_reviews: 可选的本次交互最大处理数

        Returns:
            ReviewReport
        """
        report = session.report
        remaining = session.needs_review()
        to_review = remaining[:max_reviews] if max_reviews else remaining

        print(f"\n=== 开始交互式审核 (共 {len(to_review)} 项) ===")
        print("输入: a=接受原样, c=接受为部件, r=降级原始, n=新部件, s=跳过, q=退出\n")

        for idx, item in enumerate(to_review):
            if report.reviewed >= self.limit:
                print(f"\n⚠️  已达人工审核上限 ({self.limit})，剩余条目自动降级为 RAW。")
                for remaining_item in to_review[idx:]:
                    self._apply_decision(remaining_item, ReviewDecision.FORCE_RAW, report)
                    report.over_limit_ignored += 1
                break

            enc = item.encoded_char
            print(f"\n[{idx + 1}/{len(to_review)}] {item.char_repr()}")
            print(f"  模式: {enc.mode}, 匹配分数: {enc.match_score:.3f}")
            if enc.parts:
                parts_str = ", ".join(
                    f"{p.component_id}({p.similarity:.2f})" for p in enc.parts
                )
                print(f"  部件: {parts_str}")

            while True:
                choice = input("  决策 (a/c/r/n/s/q): ").strip().lower()
                if choice == "a":
                    self._apply_decision(item, ReviewDecision.ACCEPT_AS_IS, report)
                    break
                elif choice == "c":
                    self._apply_decision(item, ReviewDecision.ACCEPT_COMPONENT, report)
                    break
                elif choice == "r":
                    self._apply_decision(item, ReviewDecision.FORCE_RAW, report)
                    break
                elif choice == "n":
                    self._apply_decision(item, ReviewDecision.ADD_NEW_PART, report)
                    break
                elif choice == "s":
                    self._apply_decision(item, ReviewDecision.SKIP, report)
                    break
                elif choice == "q":
                    print("\n退出交互式审核。")
                    report.reviewed = sum(
                        1 for i in session.items if i.decision != ReviewDecision.SKIP
                    )
                    session.report = report
                    return report
                else:
                    print("  无效输入，请重试。")

            report.reviewed += 1

        session.report = report
        session.completed = True
        return report

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_decision(
        item: ReviewItem,
        decision: ReviewDecision,
        report: ReviewReport,
    ) -> None:
        """应用决策到条目和 EncodedChar"""
        item.decision = decision

        if decision == ReviewDecision.ACCEPT_AS_IS:
            report.accepted_as_is += 1
            item.encoded_char.manual_review_needed = False

        elif decision == ReviewDecision.ACCEPT_COMPONENT:
            report.accepted_component += 1
            item.encoded_char.manual_review_needed = False

        elif decision == ReviewDecision.FORCE_RAW:
            report.forced_raw += 1
            # 修改编码结果: 强制降级为 RAW
            item.encoded_char.mode = "RAW"
            item.encoded_char.parts = []
            item.encoded_char.raw_contours = item.glyph.contours if item.glyph else None
            item.encoded_char.manual_review_needed = False

        elif decision == ReviewDecision.ADD_NEW_PART:
            report.new_parts += 1
            item.encoded_char.manual_review_needed = False

        elif decision == ReviewDecision.SKIP:
            report.skipped += 1


# ----------------------------------------------------------------------
# 便捷函数
# ----------------------------------------------------------------------

def review_with_simple_threshold(
    encoded_chars: Iterable[tuple[EncodedChar, GlyphContours]],
    accept_threshold: float = 0.85,
    raw_threshold: float = 0.60,
    limit: int = MANUAL_REVIEW_LIMIT,
) -> tuple[list[EncodedChar], ReviewReport]:
    """
    便捷函数: 基于简单阈值的自动审核策略。

    策略:
        match_score >= accept_threshold  → ACCEPT_AS_IS (接受)
        match_score <= raw_threshold     → FORCE_RAW (降级为原始)
        中间区间 → ACCEPT_AS_IS (保守接受)

    Args:
        encoded_chars: (encoded_char, glyph) 元组序列
        accept_threshold: 自动接受阈值
        raw_threshold: 自动降级阈值
        limit: 人工标注上限

    Returns:
        (处理后的 encoded_chars 列表, 审核报告)
    """
    reviewer = HumanReviewer(limit=limit)
    session = reviewer.collect_reviewable(encoded_chars)

    def decision_fn(item: ReviewItem) -> ReviewDecision:
        score = item.encoded_char.match_score
        if score <= raw_threshold:
            return ReviewDecision.FORCE_RAW
        return ReviewDecision.ACCEPT_AS_IS

    report = reviewer.review_programmatic(session, decision_fn)

    # 收集处理结果 (encoded_chars 中的对象已被 in-place 修改)
    result_chars = []
    seen_ids = set()
    for enc, _ in encoded_chars:
        if id(enc) not in seen_ids:
            seen_ids.add(id(enc))
            result_chars.append(enc)

    return result_chars, report
