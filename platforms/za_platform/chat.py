"""对话路由：会话 CRUD + SSE 流式问答（中心 Agent）+ 引用核验。"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse
from za_common import agent_identity, answer_language, conversation_context, inline_citations, security_guard

from . import agent_core, config, db, deps, models, verification

logger = logging.getLogger("za_platform.chat")

router = APIRouter(prefix="/api/conversations", tags=["chat"])

class _ConcurrencyGate:
    """问答并发闸门：限制全局 + 单用户同时进行的问答数，防资源/费用 DoS。

    超额直接拒绝（让调用方返回 429），不排队，避免请求堆积放大压力。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0
        self._per_user: dict[int, int] = {}

    def try_acquire(self, user_id: int) -> bool:
        with self._lock:
            if self._total >= config.AGENT_MAX_CONCURRENCY:
                return False
            if self._per_user.get(user_id, 0) >= config.AGENT_MAX_PER_USER:
                return False
            self._total += 1
            self._per_user[user_id] = self._per_user.get(user_id, 0) + 1
            return True

    def release(self, user_id: int) -> None:
        with self._lock:
            self._total = max(0, self._total - 1)
            n = self._per_user.get(user_id, 0) - 1
            if n <= 0:
                self._per_user.pop(user_id, None)
            else:
                self._per_user[user_id] = n


_gate = _ConcurrencyGate()


def _conv_or_404(conv_id: int, user: dict) -> dict:
    conv = db.query_one(
        "SELECT * FROM app_conversations WHERE id = %s AND user_id = %s", [conv_id, user["id"]]
    )
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv


@router.get("")
def list_conversations(user: dict = Depends(deps.get_current_user)):
    rows = db.query_all(
        "SELECT id, title, created_at, updated_at FROM app_conversations WHERE user_id = %s ORDER BY updated_at DESC",
        [user["id"]],
    )
    return {"conversations": rows}


@router.post("")
def create_conversation(req: models.ConversationCreateReq, user: dict = Depends(deps.get_current_user)):
    conv = db.execute(
        "INSERT INTO app_conversations (user_id, title) VALUES (%s, %s) RETURNING id, title, created_at, updated_at",
        [user["id"], req.title],
    )
    return {"conversation": conv}


@router.get("/{conv_id}")
def get_conversation(conv_id: int, user: dict = Depends(deps.get_current_user)):
    conv = _conv_or_404(conv_id, user)
    msgs = db.query_all(
        """
        SELECT id, role, content, citations, tool_trace, verification, created_at
        FROM app_messages WHERE conversation_id = %s ORDER BY created_at, id
        """,
        [conv_id],
    )
    for message in msgs:
        if message.get("role") == "assistant":
            message["verification"] = inline_citations.enrich_verification(
                message.get("content") or "",
                message.get("citations") or [],
                message.get("verification") or {},
            )
    return {"conversation": conv, "messages": msgs}


@router.patch("/{conv_id}")
def patch_conversation(conv_id: int, req: models.ConversationPatchReq, user: dict = Depends(deps.get_current_user)):
    _conv_or_404(conv_id, user)
    db.execute("UPDATE app_conversations SET title = %s, updated_at = now() WHERE id = %s", [req.title, conv_id])
    return {"ok": True}


@router.delete("/{conv_id}")
def delete_conversation(conv_id: int, user: dict = Depends(deps.get_current_user)):
    _conv_or_404(conv_id, user)
    db.execute("DELETE FROM app_conversations WHERE id = %s", [conv_id])
    return {"ok": True}


@router.post("/{conv_id}/chat")
async def chat(conv_id: int, req: models.ChatReq, request: Request,
               user: dict = Depends(deps.get_current_user)):
    response_language = answer_language.resolve(
        req.answer_language, request.headers.get("accept-language", ""), req.message)
    conv = _conv_or_404(conv_id, user)

    # 并发闸门：全局/单用户超额即拒绝，防一人并发刷爆模型与检索造成资源/费用 DoS。
    if not _gate.try_acquire(user["id"]):
        raise HTTPException(status_code=429, detail="并发请求过多，请待当前查明完成后再试")

    # 预处理阶段若出错，必须在此释放闸门（此后所有权移交给 event_gen 的 finally）。
    try:
        # 落库用户消息
        db.execute(
            "INSERT INTO app_messages (conversation_id, role, content) VALUES (%s, 'user', %s)",
            [conv_id, req.message],
        )
        # 首条消息自动生成标题
        if not conv.get("title"):
            title = " ".join(req.message.split())[:30] or "新会话"
            db.execute("UPDATE app_conversations SET title = %s WHERE id = %s", [title, conv_id])

        input_messages = conversation_context.load_input_messages(db, conv_id)
    except Exception:
        _gate.release(user["id"])
        raise

    async def event_gen():
        answer_parts: list[str] = []
        citations: list = []
        tool_trace: list = []
        verif: dict | None = None
        started = time.monotonic()
        agent_stream = None
        try:
            # 安全护栏：拦截越狱/诱导无限检索/批量蒸馏检索库/疑似注入语法特征的消息，
            # 优先于问题分诊——命中直接拒答，不进入 Agent、不消耗检索预算。
            sec_category = security_guard.screen_message(req.message)
            if sec_category:
                should_ban = False
                try:
                    db.audit(user["id"], "security_block",
                             {"category": sec_category, "excerpt": req.message[:500]})
                    should_ban = security_guard.record_violation(user["id"])
                    if should_ban:
                        db.execute("UPDATE app_users SET is_active = false WHERE id = %s", [user["id"]])
                        db.audit(user["id"], "user_auto_suspended", {"reason": "repeated_security_violations"})
                except Exception as exc:
                    logger.warning("security block bookkeeping failed (conv=%s): %s", conv_id, exc)
                refusal = ("检测到您的账号短时间内多次触发安全限制，账号已被自动限制，请联系管理员。"
                           if should_ban else
                           "抱歉，这条消息包含系统无法处理的内容（可能涉及试图绕过平台限制、提取内部提示词"
                           "或数据库攻击特征），本次不予作答。如果这是误判，请换一种表达方式重新提问。")
                refusal = answer_language.ui_text(refusal)
                answer_parts.append(refusal)
                yield {"event": "delta", "data": json.dumps({"text": refusal}, ensure_ascii=False)}
                yield {"event": "final",
                       "data": json.dumps({"citations": [], "tool_trace": [{"security_block": sec_category}]}, ensure_ascii=False)}
                return
            identity = agent_identity.reply(req.message, "za", response_language)
            if identity:
                answer_parts.append(identity)
                yield {"event": "delta", "data": json.dumps({"text": identity}, ensure_ascii=False)}
                yield {"event": "final", "data": json.dumps({"citations": [], "tool_trace": []})}
                return
            # 问题分诊：宽泛问题先反问澄清，不启动检索（省下的预算留给真正聚焦的问题）。
            # fail-open：分诊本身异常/超时不影响主流程，见 agent_core.assess_query_scope。
            try:
                triage = await asyncio.wait_for(agent_core.assess_query_scope(input_messages), timeout=15)
            except Exception as exc:
                logger.warning("triage assess failed, fail-open to proceed (conv=%s): %s", conv_id, exc)
                triage = {"decision": "proceed", "clarifying_question": ""}
            if triage.get("decision") == "clarify" and triage.get("clarifying_question"):
                q = triage["clarifying_question"]
                answer_parts.append(q)
                yield {"event": "delta", "data": json.dumps({"text": q}, ensure_ascii=False)}
                yield {"event": "final",
                       "data": json.dumps({"citations": [], "tool_trace": [{"triage": "clarify"}]}, ensure_ascii=False)}
                return
            agent_stream = agent_core.stream_answer(input_messages, mode=req.mode)
            async for ev in agent_stream:
                # 墙钟预算：单次问答超时即停，防止异常长链路长期占用模型/检索资源。
                if time.monotonic() - started > config.AGENT_WALL_CLOCK_SECONDS:
                    logger.warning("chat wall-clock exceeded (conv=%s), stopping stream", conv_id)
                    yield {"event": "error", "data": json.dumps({"error": "本次查明耗时过长已中止，请用更聚焦的问题重试"}, ensure_ascii=False)}
                    break
                # 客户端断开即停，不再空耗算力。
                if await request.is_disconnected():
                    logger.info("chat client disconnected (conv=%s), stopping stream", conv_id)
                    break
                if ev["type"] == "delta":
                    answer_parts.append(ev["text"])
                    yield {"event": "delta", "data": json.dumps({"text": ev["text"]}, ensure_ascii=False)}
                elif ev["type"] == "tool":
                    yield {"event": "tool", "data": json.dumps(ev, ensure_ascii=False)}
                elif ev["type"] == "final":
                    citations = ev.get("citations", [])
                    tool_trace = ev.get("tool_trace", [])
                    if not answer_parts and ev.get("text"):
                        answer_parts.append(ev["text"])
                    yield {
                        "event": "final",
                        "data": json.dumps({"citations": citations, "tool_trace": tool_trace}, ensure_ascii=False),
                    }
                    # 引用核验（反臆造）：规则引擎回查答案中的法条/案名引用，
                    # 失败不影响主流程（核验是增强而非阻断）。
                    # V1.3-B2.4 多轮断链修复：候选池合并本会话历史轮次已落库的 citations——
                    # follow-up 里复述上一轮已取证的法条/判例不再被误判 unverified。
                    try:
                        def _verify_with_history() -> dict:
                            merged = list(citations)
                            try:
                                hist = db.query_all(
                                    """SELECT citations FROM app_messages
                                       WHERE conversation_id = %s AND role = 'assistant'
                                         AND citations IS NOT NULL
                                       ORDER BY id DESC LIMIT 10""",
                                    [conv_id],
                                )
                                for h in hist:
                                    merged.extend(h.get("citations") or [])
                            except Exception:
                                pass
                            answer = "".join(answer_parts)
                            verified = verification.verify_answer(answer, merged[:400])
                            return inline_citations.enrich_verification(answer, merged[:400], verified)
                        verif = await asyncio.to_thread(_verify_with_history)
                        yield {"event": "verification",
                               "data": json.dumps(verif, ensure_ascii=False)}
                    except Exception as exc:
                        logger.warning("verification failed (conv=%s): %s", conv_id, exc)
                        verif = None
        except Exception as exc:
            # 详细异常只写服务端日志，客户端仅得通用提示，避免泄露内部 URL/依赖细节。
            logger.exception("chat stream failed (conv=%s): %s", conv_id, exc)
            yield {"event": "error", "data": json.dumps({"error": "生成回答时出现错误，请稍后重试"}, ensure_ascii=False)}
        finally:
            try:
                if agent_stream is not None:
                    await agent_stream.aclose()
            finally:
                _gate.release(user["id"])
            answer = "".join(answer_parts).strip()
            if answer:
                db.execute(
                    """
                    INSERT INTO app_messages (conversation_id, role, content, citations, tool_trace, verification)
                    VALUES (%s, 'assistant', %s, %s::jsonb, %s::jsonb, %s::jsonb)
                    """,
                    [
                        conv_id,
                        answer,
                        json.dumps(citations, ensure_ascii=False),
                        json.dumps(tool_trace, ensure_ascii=False),
                        json.dumps(verif, ensure_ascii=False) if verif else None,
                    ],
                )
            db.execute("UPDATE app_conversations SET updated_at = now() WHERE id = %s", [conv_id])

    return EventSourceResponse(answer_language.stream(event_gen(), response_language),
                               headers={"Content-Language": response_language})
