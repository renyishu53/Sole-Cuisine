import asyncio
import json
from collections.abc import AsyncIterator, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import ConversationRepository
from app.schemas import CalendarEvent, MemberProfile, PlanningRequest
from app.schemas.domain import (
    AgentStep,
    ChatMessageResponse,
    ChatSessionSummary,
    ChatTurnResponse,
)
from app.services.planning import planning_service
from app.services.runtime import runtime_state


class PlanningCancelledError(RuntimeError):
    pass


class SummaryStreamExtractor:
    def __init__(self) -> None:
        self._buffer = ""
        self._started = False
        self._escaped = False
        self.done = False

    def feed(self, chunk: str) -> str:
        if self.done:
            return ""
        if not self._started:
            self._buffer += chunk
            marker = '"summary"'
            marker_index = self._buffer.find(marker)
            if marker_index < 0:
                self._buffer = self._buffer[-len(marker) :]
                return ""
            quote_index = self._buffer.find('"', marker_index + len(marker))
            if quote_index < 0:
                return ""
            chunk = self._buffer[quote_index + 1 :]
            self._buffer = ""
            self._started = True

        output: list[str] = []
        escapes = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
        for character in chunk:
            if self._escaped:
                output.append(escapes.get(character, character))
                self._escaped = False
            elif character == "\\":
                self._escaped = True
            elif character == '"':
                self.done = True
                break
            else:
                output.append(character)
        return "".join(output)


def _message_response(message: object) -> ChatMessageResponse:
    return ChatMessageResponse.model_validate(message, from_attributes=True)


def _session_summary(chat: object) -> ChatSessionSummary:
    return ChatSessionSummary.model_validate(chat, from_attributes=True)


def _sse(event_id: str, event: str, data: dict[str, object]) -> str:
    """Format an SSE block with an event ID for reconnection support."""
    return f"id: {event_id}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class ConversationService:
    async def _emit(
        self, session_id: str, event: str, data: dict[str, object]
    ) -> str:
        """Generate an event ID, persist to Redis, and return the formatted SSE block."""
        event_id = await runtime_state.next_event_id(session_id)
        await runtime_state.append_event(session_id, event_id, event, data)
        return _sse(event_id, event, data)

    async def run_turn(
        self,
        session: AsyncSession,
        *,
        session_id: str,
        user_id: int,
        content: str,
        budget: float,
        members: Sequence[MemberProfile],
        events: Sequence[CalendarEvent],
    ) -> ChatTurnResponse | None:
        repository = ConversationRepository(session)
        chat = await repository.get_session(session_id, user_id)
        if chat is None:
            return None
        history = list(chat.messages)
        user_message = await repository.add_message(chat, role="user", content=content)
        request = self._planning_request(history, content, budget, user_id)
        response = await planning_service.generate(
            request,
            members=members,
            events=events,
            session=session,
        )
        assistant = await repository.add_message(
            chat,
            role="assistant",
            content=response.summary,
            run_id=str(response.run_id),
            payload=response.model_dump(mode="json"),
        )
        return ChatTurnResponse(
            session=_session_summary(chat),
            user_message=_message_response(user_message),
            assistant_message=_message_response(assistant),
            plan=response,
        )

    async def stream_turn(
        self,
        session: AsyncSession,
        *,
        session_id: str,
        user_id: int,
        content: str,
        budget: float,
        members: Sequence[MemberProfile],
        events: Sequence[CalendarEvent],
    ) -> AsyncIterator[str]:
        repository = ConversationRepository(session)
        chat = await repository.get_session(session_id, user_id)
        if chat is None:
            yield _sse("0", "error", {"message": "对话不存在"})
            return
        history = list(chat.messages)
        await runtime_state.clear_turn(session_id)
        await runtime_state.set_turn_status(session_id, "running")
        await runtime_state.clear_cancelled(session_id)
        await repository.set_status(chat, "running")
        user_message = await repository.add_message(chat, role="user", content=content)
        yield await self._emit(
            session_id, "message", _message_response(user_message).model_dump(mode="json")
        )

        queue: asyncio.Queue[tuple[str, AgentStep | str]] = asyncio.Queue()
        summary_stream = SummaryStreamExtractor()
        token_emitted = False

        async def on_step(step: AgentStep) -> None:
            if await runtime_state.is_cancelled(session_id):
                raise PlanningCancelledError("用户已取消本次规划")
            await queue.put(("step", step))

        async def on_token(chunk: str) -> None:
            if await runtime_state.is_cancelled(session_id):
                raise asyncio.CancelledError
            summary_chunk = summary_stream.feed(chunk)
            if summary_chunk:
                await queue.put(("token", summary_chunk))

        request = self._planning_request(history, content, budget, user_id)
        task = asyncio.create_task(
            planning_service.generate(
                request,
                members=members,
                events=events,
                session=session,
                on_step=on_step,
                on_token=on_token,
            )
        )
        try:
            while not task.done() or not queue.empty():
                try:
                    event, payload = await asyncio.wait_for(queue.get(), timeout=0.2)
                except TimeoutError:
                    if await runtime_state.is_cancelled(session_id):
                        task.cancel()
                    continue
                if event == "step":
                    assert isinstance(payload, AgentStep)
                    yield await self._emit(session_id, "step", payload.model_dump(mode="json"))
                else:
                    token_emitted = True
                    yield await self._emit(session_id, "token", {"content": str(payload)})
            response = await task
            assistant = await repository.add_message(
                chat,
                role="assistant",
                content=response.summary,
                run_id=str(response.run_id),
                payload=response.model_dump(mode="json"),
            )
            await repository.set_status(chat, "active")
            if not token_emitted:
                yield await self._emit(session_id, "token", {"content": response.summary})
            yield await self._emit(
                session_id,
                "complete",
                {
                    "message": _message_response(assistant).model_dump(mode="json"),
                    "plan": response.model_dump(mode="json"),
                },
            )
            await runtime_state.set_turn_status(session_id, "completed")
        except PlanningCancelledError as exc:
            await repository.set_status(chat, "cancelled")
            await repository.add_message(chat, role="system", content=str(exc))
            yield await self._emit(session_id, "cancelled", {"message": str(exc)})
            await runtime_state.set_turn_status(session_id, "cancelled")
        except asyncio.CancelledError:
            message = "用户已取消本次规划"
            await repository.set_status(chat, "cancelled")
            await repository.add_message(chat, role="system", content=message)
            yield await self._emit(session_id, "cancelled", {"message": message})
            await runtime_state.set_turn_status(session_id, "cancelled")
        except Exception as exc:
            await repository.set_status(chat, "failed")
            yield await self._emit(
                session_id, "error", {"message": f"{type(exc).__name__}: {str(exc)[:500]}"}
            )
            await runtime_state.set_turn_status(session_id, "failed")

    @staticmethod
    def _planning_request(
        history: Sequence[object],
        content: str,
        budget: float,
        user_id: int,
    ) -> PlanningRequest:
        lines = [
            f"{getattr(message, 'role', 'user')}: {getattr(message, 'content', '')}"
            for message in history[-8:]
        ]
        context = "\n".join(lines)
        prompt = f"多轮历史：\n{context}\n当前需求：{content}" if context else content
        return PlanningRequest(
            prompt=prompt[-1000:],
            budget=budget,
            user_id=user_id,
        )


conversation_service = ConversationService()
