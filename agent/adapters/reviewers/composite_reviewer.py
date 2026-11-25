from collections.abc import Sequence

from agent.app.ports import ToolReviewer
from agent.domain.roles import Decision
from agent.domain.session import Session, ToolCall


class CompositeReviewer:
    def __init__(self, reviewers: Sequence[ToolReviewer]) -> None:
        if not reviewers:
            raise ValueError("至少需要一个审核器")
        self.reviewers = tuple(reviewers)

    def review(self, session: Session, call: ToolCall) -> Decision:
        for reviewer in self.reviewers:
            decision = reviewer.review(session, call)
            if not decision.should_allow:
                return decision
        return Decision(True)
