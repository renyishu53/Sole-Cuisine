import json
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import LLMGenerationError, get_chat_assistant
from app.models import NutritionGoal, UserProfile
from app.repositories import ConversationRepository, PlanningRepository
from app.schemas.domain import (
    ChatMessageResponse,
    ChatSessionSummary,
    ChatTurnResponse,
)
from app.services.knowledge import get_knowledge_service
from app.services.runtime import runtime_state


class PlanningCancelledError(RuntimeError):
    pass


class SummaryStreamExtractor:
    """从流式 JSON 中提取 summary 字段的工具类。

    保留供测试与潜在的 JSON 流式解析场景使用；阶段二对话改造后
    stream_turn 不再需要它（自然语言输出无需 JSON 解析）。
    """

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
    """AI 对话服务：只读问答模式，不修改业务数据。

    阶段二改造：从"每轮生成周计划"切换到"自然语言问答"。
    - 注入 UserProfile / NutritionGoal / 当前 WeeklyPlan 作为只读上下文
    - 走 RAG 检索（Milvus + Neo4j）+ DeepSeek 问答
    - 不再调用 planning_service.generate，不产生计划副作用
    """

    async def _emit(
        self, session_id: str, event: str, data: dict[str, object]
    ) -> str:
        """Generate an event ID, persist to Redis, and return the formatted SSE block."""
        event_id = await runtime_state.next_event_id(session_id)
        await runtime_state.append_event(session_id, event_id, event, data)
        return _sse(event_id, event, data)

    async def _build_readonly_context(
        self, session: AsyncSession, user_id: int
    ) -> str:
        """组装只读上下文：用户画像 + 营养目标 + 当前计划摘要。

        所有数据只读，不会修改业务状态。
        """
        parts: list[str] = []

        profile = await session.scalar(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        if profile is not None:
            constraints_str = (
                "、".join(profile.constraints)
                if isinstance(profile.constraints, list) and profile.constraints
                else (profile.constraints or "无")
            )
            parts.append(
                f"用户画像：{profile.gender}，{profile.age}岁，"
                f"{profile.weight_kg}kg，{profile.height_cm}cm，"
                f"活动水平 {profile.activity_level}，"
                f"忌口 {constraints_str}"
            )

        goal = await session.scalar(
            select(NutritionGoal).where(NutritionGoal.user_id == user_id)
        )
        if goal is not None:
            parts.append(
                f"营养目标：每日 {goal.target_calories:.0f}kcal，"
                f"蛋白质 {goal.protein_g:.0f}g / 碳水 {goal.carb_g:.0f}g / "
                f"脂肪 {goal.fat_g:.0f}g，目标类型 {goal.goal_type}"
            )

        plan = await PlanningRepository(session).get_active_plan(user_id)
        if plan is not None:
            meal_names = [m.name for m in plan.meals[:7]]
            parts.append(f"当前周计划餐食：{', '.join(meal_names)}")
            parts.append(f"本周预算：¥{plan.budget}")

        return "\n".join(parts) if parts else ""

    async def _retrieve_rag(self, question: str, user_id: int) -> list[str]:
        """RAG 检索：Milvus 向量 + Neo4j 图谱，返回相关文本片段。

        检索失败时不阻断问答，返回空列表（EAFP 模式）。
        """
        try:
            result = await get_knowledge_service().search(
                question, user_id, top_k=3, domain=None,
            )
            snippets: list[str] = [
                hit.content for hit in result.vector_hits[:3]
            ]
            for hit in result.graph_hits[:2]:
                snippets.append(
                    f"{hit.subject} {hit.relation} {hit.target}: {hit.detail}"
                )
            return snippets
        except Exception:  # noqa: BLE001 - RAG 失败不阻断问答
            return []

    @staticmethod
    def _extract_history(chat: object) -> list[tuple[str, str]]:
        """从会话消息中提取多轮历史，过滤 system 消息。"""
        return [
            (m.role, m.content)
            for m in getattr(chat, "messages", [])[-8:]
            if m.role in ("user", "assistant")
        ]

    async def run_turn(
        self,
        session: AsyncSession,
        *,
        session_id: str,
        user_id: int,
        content: str,
        budget: float,
    ) -> ChatTurnResponse | None:
        """非流式对话轮：用户提问 → RAG 检索 → LLM 回答。

        budget 参数保留以兼容前端，但对话模式不再使用。
        """
        del budget  # noqa: ARG002 - 兼容前端签名
        repository = ConversationRepository(session)
        chat = await repository.get_session(session_id, user_id)
        if chat is None:
            return None

        history = self._extract_history(chat)
        user_message = await repository.add_message(
            chat, role="user", content=content
        )

        # 组装只读上下文 + RAG 检索
        context = await self._build_readonly_context(session, user_id)
        rag_snippets = await self._retrieve_rag(content, user_id)

        # 调用对话 LLM（非规划链路）
        assistant_model = get_chat_assistant()
        if assistant_model is None:
            answer = (
                "AI 助手未启用（未配置 LLM_API_KEY），"
                "请在 .env 中配置 LLM_PROVIDER 和 LLM_API_KEY。"
            )
        else:
            chunks: list[str] = []
            try:
                async for chunk in assistant_model.answer(
                    content, context, rag_snippets, history
                ):
                    chunks.append(chunk)
                answer = "".join(chunks) or "[未生成回答]"
            except LLMGenerationError as exc:
                answer = f"[回答失败] {exc}"

        assistant = await repository.add_message(
            chat, role="assistant", content=answer
        )
        return ChatTurnResponse(
            session=_session_summary(chat),
            user_message=_message_response(user_message),
            assistant_message=_message_response(assistant),
            plan=None,
        )

    async def stream_turn(
        self,
        session: AsyncSession,
        *,
        session_id: str,
        user_id: int,
        content: str,
        budget: float,
    ) -> AsyncIterator[str]:
        """流式对话轮：SSE 推送 token 事件，支持取消。"""
        del budget  # noqa: ARG002 - 兼容前端签名
        repository = ConversationRepository(session)
        chat = await repository.get_session(session_id, user_id)
        if chat is None:
            yield _sse("0", "error", {"message": "对话不存在"})
            return

        history = self._extract_history(chat)
        await runtime_state.clear_turn(session_id)
        await runtime_state.set_turn_status(session_id, "running")
        await runtime_state.clear_cancelled(session_id)
        await repository.set_status(chat, "running")
        user_message = await repository.add_message(
            chat, role="user", content=content
        )
        yield await self._emit(
            session_id,
            "message",
            _message_response(user_message).model_dump(mode="json"),
        )

        # 首 token 返回前给出通用状态，不暴露内部检索实现。
        yield await self._emit(
            session_id, "thinking", {"hint": "正在准备回答…"}
        )

        # 组装只读上下文 + RAG 检索
        context = await self._build_readonly_context(session, user_id)
        rag_snippets = await self._retrieve_rag(content, user_id)

        assistant_model = get_chat_assistant()
        chunks: list[str] = []
        try:
            if assistant_model is None:
                raise LLMGenerationError(
                    "AI 助手未启用（未配置 LLM_API_KEY）"
                )

            async for chunk in assistant_model.answer(
                content, context, rag_snippets, history
            ):
                if await runtime_state.is_cancelled(session_id):
                    raise PlanningCancelledError("用户已取消本次对话")
                chunks.append(chunk)
                yield await self._emit(
                    session_id, "token", {"content": chunk}
                )

            answer = "".join(chunks) or "[未生成回答]"
            assistant = await repository.add_message(
                chat, role="assistant", content=answer
            )
            await repository.set_status(chat, "active")
            yield await self._emit(
                session_id,
                "complete",
                {
                    "message": _message_response(assistant).model_dump(
                        mode="json"
                    )
                },
            )
            await runtime_state.set_turn_status(session_id, "completed")
        except PlanningCancelledError as exc:
            await repository.set_status(chat, "cancelled")
            await repository.add_message(chat, role="system", content=str(exc))
            yield await self._emit(
                session_id, "cancelled", {"message": str(exc)}
            )
            await runtime_state.set_turn_status(session_id, "cancelled")
        except Exception as exc:
            await repository.set_status(chat, "failed")
            error_msg = f"{type(exc).__name__}: {str(exc)[:500]}"
            if not chunks:
                await repository.add_message(
                    chat, role="assistant", content=f"[回答失败] {error_msg}"
                )
            yield await self._emit(
                session_id, "error", {"message": error_msg}
            )
            await runtime_state.set_turn_status(session_id, "failed")


conversation_service = ConversationService()
