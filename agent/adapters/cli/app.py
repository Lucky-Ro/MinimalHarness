import json

from agent.app.ports import Effort, SessionNotFoundError
from agent.app.service import AgentService
from agent.domain.session import AssistantMessage, Message, Session, ToolResult, UserMessage


_EFFORT_CHOICES = " / ".join(effort.value for effort in Effort)


def _show_message(message: Message) -> None:
    if isinstance(message, UserMessage):
        print(f"你 › {message.text}")
    elif isinstance(message, AssistantMessage):
        if message.thinking:
            print(f"思考 › {message.thinking}")
        for call in message.tool_calls:
            arguments = json.dumps(call.arguments, ensure_ascii=False)
            print(f"工具请求 [{call.id}] {call.tool_name} {arguments}")
        if message.text:
            print(f"助手 › {message.text}")
    elif isinstance(message, ToolResult):
        if not message.is_error:
            status = "审核通过，执行成功"
        elif message.content.startswith("Tool call rejected:"):
            status = "审核拒绝"
        else:
            status = "执行失败或中断"
        print(f"工具结果 [{message.tool_call_id}] {status} › {message.content}")


def _summary_status(session: Session) -> str:
    return "有摘要" if session.summary else "无摘要"


class CliApp:
    def __init__(self, service: AgentService):
        self.service = service

    def run(self) -> None:
        current_id = self.service.new_session().id
        print(f"当前会话 {current_id}（思考档位 {self.service.get_effort().value}）")
        print("命令  /new  /list  /use <会话 ID>  /history  /effort [档位]  /quit")
        while True:
            try:
                text = input(f"[{current_id}] > ").strip()
                if not text:
                    continue
                if text in ("/quit", "/exit"):
                    return
                if text.startswith("/"):
                    current_id = self._command(current_id, text)
                else:
                    for message in self.service.chat(current_id, text):
                        if not isinstance(message, UserMessage):
                            _show_message(message)
            except (KeyboardInterrupt, EOFError):
                print()
                return
            except SessionNotFoundError:
                print("找不到该会话。可用 /list 查看会话 ID。")
            except Exception as exc:
                # Provider responses can contain authentication material.
                print(f"操作失败（{type(exc).__name__}）。可用 /history 查看已保存的进度。")

    def _command(self, current_id: str, text: str) -> str:
        words = text.split()
        if words == ["/new"]:
            current_id = self.service.new_session().id
            print(f"当前会话 {current_id}")
        elif words == ["/list"]:
            for session in self.service.list_sessions():
                marker = "*" if session.id == current_id else " "
                print(f"{marker} {session.id}  {len(session.messages)} 条消息  {_summary_status(session)}")
        elif words[0] == "/use":
            if len(words) != 2:
                print("用法  /use <会话 ID>")
            else:
                current_id = self.service.get_session(words[1]).id
                print(f"当前会话 {current_id}")
        elif words == ["/history"]:
            session = self.service.get_session(current_id)
            print(f"{session.id}  {len(session.messages)} 条消息  {_summary_status(session)}")
            if session.summary:
                print(f"前 {session.compacted_until} 条消息的摘要 › {session.summary}")
            for message in session.messages:
                _show_message(message)
        elif words[0] == "/effort":
            if len(words) == 1:
                print(f"当前思考档位 {self.service.get_effort().value}（可选：{_EFFORT_CHOICES}）")
            elif len(words) != 2:
                print(f"用法  /effort [{_EFFORT_CHOICES}]")
            else:
                try:
                    effort = Effort.parse(words[1])
                except ValueError:
                    print(f"无效的思考档位。用法  /effort <{_EFFORT_CHOICES}>")
                else:
                    updated = self.service.set_effort(effort)
                    print(f"已切换思考档位为 {updated.effort.value}")
        else:
            print("未知命令。可用 /new、/list、/use、/history、/effort 或 /quit。")
        return current_id
