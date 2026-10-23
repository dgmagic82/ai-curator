"""Answer chain: retrieval + LMS data + prompt → structured answer with sources.

Prompt layout (see docs/ARCHITECTURE.md):
    system  = prompts/system.md + level block from prompts/levels.md + prompts/few_shot.md
    history = the last turns of the dialog, text only
    user    = LMS block [0] + retrieved fragments [1..k] + the question
"""

import time
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from curator import config, kb, lms

Category = Literal["организационный", "учебный", "рекомендация", "вне рамок"]
LMS_SOURCE = "Данные LMS: расписание и статусы заданий"
HISTORY_TURNS = 4
RESOURCE_SLOTS = 2


class CuratorAnswer(BaseModel):
    answer: str = Field(description="Ответ студенту в Markdown")
    sources: list[int] = Field(description="Номера использованных фрагментов: 0 = данные LMS, 1..N = фрагменты базы")
    category: Category = Field(description="Тип вопроса")
    escalate: bool = Field(description="Нужен куратор-человек: перенос дедлайна, оценка, апелляция, личная ситуация")


@dataclass
class CuratorResult:
    answer: str
    category: str
    escalate: bool
    sources: list[dict]
    retrieved: list[dict]
    latency_ms: int
    tokens_in: int = 0
    tokens_out: int = 0
    raw_sources: list[int] = field(default_factory=list)


def _read(name: str) -> str:
    return (config.PROMPTS_DIR / name).read_text(encoding="utf-8")


def level_block(level: str) -> str:
    text = _read("levels.md")
    marker = f"## {level}"
    start = text.index(marker) + len(marker)
    end = text.find("\n## ", start)
    return text[start:end if end != -1 else None].strip()


def system_prompt(level: str) -> str:
    return (_read("system.md")
            .replace("{{LEVEL_BLOCK}}", level_block(level))
            .replace("{{FEW_SHOT}}", _read("few_shot.md")))


def user_message(question: str, lms_text: str, hits: list) -> str:
    parts = [f"[0] {LMS_SOURCE}\n{lms_text}", "Фрагменты базы знаний курса:"]
    for i, (doc, _score) in enumerate(hits, 1):
        m = doc.metadata
        parts.append(f"[{i}] Источник: {m['source']} (тип: {m['type']}, модуль: {m['module']})\n{doc.page_content}")
    if not hits:
        parts.append("(по запросу ничего не найдено)")
    parts.append(f"Вопрос студента: {question}")
    return "\n\n".join(parts)


def resolve_sources(numbers: list[int], hits: list) -> list[dict]:
    """Turns the model's fragment numbers into citations; unknown numbers are dropped, not trusted."""
    out, seen = [], set()
    for n in numbers:
        if n in seen:
            continue
        seen.add(n)
        if n == 0:
            out.append({"n": 0, "source": LMS_SOURCE, "doc_id": "lms", "type": "lms", "module": None})
        elif 1 <= n <= len(hits):
            m = hits[n - 1][0].metadata
            out.append({"n": n, "source": m["source"], "doc_id": m["doc_id"], "type": m["type"], "module": m["module"]})
    return out


def llm() -> ChatOpenAI:
    return ChatOpenAI(model=config.CHAT_MODEL, base_url=config.BASE_URL, api_key=config.api_key(),
                      temperature=config.TEMPERATURE, timeout=60, max_retries=2)


def ask(question: str, student_id: str, level: str, index, history: list[dict] | None = None,
        module: int | None = None, doc_type: str | None = None, model: ChatOpenAI | None = None) -> CuratorResult:
    started = time.perf_counter()
    hits = kb.search(index, question, module=module, doc_type=doc_type, level=level)
    if doc_type is None:
        # lectures outrank reading lists on almost any topic, so recommendations get their own slots
        seen = {d.page_content for d, _ in hits}
        hits += [h for h in kb.search(index, question, k=RESOURCE_SLOTS, module=module, doc_type="ресурсы", level=level)
                 if h[0].page_content not in seen]
    messages = [SystemMessage(system_prompt(level))]
    for turn in (history or [])[-HISTORY_TURNS:]:
        messages.append(HumanMessage(turn["content"]) if turn["role"] == "user" else AIMessage(turn["content"]))
    messages.append(HumanMessage(user_message(question, lms.student_context(student_id), hits)))

    out = (model or llm()).with_structured_output(CuratorAnswer, method="json_schema", include_raw=True).invoke(messages)
    if out["parsing_error"] or out["parsed"] is None:
        raise RuntimeError(f"Модель вернула ответ не по схеме: {out['parsing_error']}")
    parsed: CuratorAnswer = out["parsed"]
    usage = getattr(out["raw"], "usage_metadata", None) or {}
    return CuratorResult(
        answer=parsed.answer, category=parsed.category, escalate=parsed.escalate,
        sources=resolve_sources(parsed.sources, hits),
        retrieved=[{"source": d.metadata["source"], "doc_id": d.metadata["doc_id"], "score": round(float(s), 4)}
                   for d, s in hits],
        latency_ms=int((time.perf_counter() - started) * 1000),
        tokens_in=usage.get("input_tokens", 0), tokens_out=usage.get("output_tokens", 0),
        raw_sources=parsed.sources)
