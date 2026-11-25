from dataclasses import dataclass

from agent.app.ports import ModelClient, ModelSettings
from agent.domain.roles import Decision, ToolCallReviewer
from agent.domain.session import Session, ToolCall


@dataclass
class LlmToolReviewer:
    role: ToolCallReviewer
    model_client: ModelClient
    reviewer_model_settings: ModelSettings

    def review(self, session: Session, call: ToolCall) -> Decision:
        try:
            context = self.role.context(session, call)
            decision = self.model_client.generate(context, self.reviewer_model_settings)
            if not isinstance(decision, Decision) or type(decision.should_allow) is not bool:
                return Decision(False, "审核模型返回的决定无效")
            return decision
        except Exception as exc:
            return Decision(False, f"审核模型不可用 ({type(exc).__name__})")
