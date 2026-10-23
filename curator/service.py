"""One request handler for every channel (site, Telegram bot, eval): validate → cache → curator chain → log."""

import time
from dataclasses import dataclass, field

from curator import chain, config
from curator.db_logger import DatabaseLogger, normalize

REJECT_TEXT = {
    "пустой вопрос": "Напишите вопрос текстом: про дедлайны, материалы курса или что почитать дальше.",
    "слишком длинный вопрос": f"Вопрос длиннее {config.MAX_QUESTION_CHARS} символов. Сформулируйте его короче.",
    "дневной лимит": "На сегодня лимит демо исчерпан. Возвращайтесь завтра.",
}


@dataclass
class Reply:
    status: str                      # answered | rejected
    text: str
    category: str = ""
    escalate: bool = False
    sources: list[dict] = field(default_factory=list)
    from_cache: bool = False
    response_time_ms: int = 0


def cache_keys(question: str, level: str, student_id: str, **filters) -> tuple[str, str]:
    """A general key for answers that suit any student, and a per-student key for answers built on LMS data."""
    base = "|".join([normalize(question), level] + [str(filters.get(k)) for k in ("module", "doc_type", "topic")])
    return base, f"{base}|{student_id}"


def is_personal(category: str, sources: list[dict]) -> bool:
    # an answer that used the student's deadlines or statuses must never be served to another student
    return category == "организационный" or any(s["doc_id"] == "lms" for s in sources)


def handle(question: str, *, student_id: str, level: str, channel: str, user_ref: str, index, logger: DatabaseLogger,
           history: list[dict] | None = None, daily_limit: int | None = None, model=None, **filters) -> Reply:
    question = question.strip()
    base = {"channel": channel, "user_ref": user_ref, "student_id": student_id, "level": level, "question": question}

    reason = ("пустой вопрос" if not question else
              "слишком длинный вопрос" if len(question) > config.MAX_QUESTION_CHARS else
              "дневной лимит" if daily_limit and logger.count_today(user_ref, channel) >= daily_limit else None)
    if reason:
        logger.log(**base, status="rejected", reject_reason=reason)
        return Reply(status="rejected", text=REJECT_TEXT[reason])

    started = time.perf_counter()
    general_key, student_key = cache_keys(question, level, student_id, **filters)
    # follow-up questions depend on the dialog, so only the first question of a dialog uses the cache
    cached = None if history else (logger.cache_get(general_key) or logger.cache_get(student_key))
    if cached:
        ms = int((time.perf_counter() - started) * 1000)
        logger.log(**base, status="answered", answer=cached["answer"], category=cached["category"],
                   escalate=cached["escalate"], sources=[s["doc_id"] for s in cached["sources"]], from_cache=True,
                   response_time_ms=ms, model=config.CHAT_MODEL)
        return Reply(status="answered", text=cached["answer"], category=cached["category"],
                     escalate=cached["escalate"], sources=cached["sources"], from_cache=True, response_time_ms=ms)

    try:
        r = chain.ask(question, student_id, level, index, history=history, model=model, **filters)
    except Exception as e:
        logger.log(**base, status="error", reject_reason=type(e).__name__,
                   response_time_ms=int((time.perf_counter() - started) * 1000), model=config.CHAT_MODEL)
        raise
    logger.log(**base, status="answered", answer=r.answer, category=r.category, escalate=r.escalate,
               sources=[s["doc_id"] for s in r.sources], response_time_ms=r.latency_ms, tokens_in=r.tokens_in,
               tokens_out=r.tokens_out, model=config.CHAT_MODEL)
    if not history:
        logger.cache_put(student_key if is_personal(r.category, r.sources) else general_key,
                         {"answer": r.answer, "category": r.category, "escalate": r.escalate, "sources": r.sources})
    return Reply(status="answered", text=r.answer, category=r.category, escalate=r.escalate, sources=r.sources,
                 response_time_ms=r.latency_ms)
