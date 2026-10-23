"""Offline tests of the SQLite log, the cache and the request handler; the model call is replaced by a stub."""

import csv
from datetime import datetime, timedelta, timezone

import pytest

from curator import chain, db_logger, service
from curator.db_logger import DatabaseLogger


@pytest.fixture
def logger(tmp_path):
    return DatabaseLogger(tmp_path / "test.db")


def fake_result(category="учебный", sources=None, answer="Ответ"):
    return chain.CuratorResult(answer=answer, category=category, escalate=False,
                               sources=sources if sources is not None else
                               [{"n": 1, "source": "Л / Р", "doc_id": "lec-3-1", "type": "лекция", "module": 3}],
                               retrieved=[], latency_ms=1200, tokens_in=4000, tokens_out=150)


@pytest.fixture
def stub_chain(monkeypatch):
    calls = []

    def ask(question, student_id, level, index, **kw):
        calls.append((question, student_id))
        if "дедлайн" in question:
            return fake_result("организационный", [{"n": 0, "source": "LMS", "doc_id": "lms", "type": "lms",
                                                   "module": None}], answer=f"Дедлайн для {student_id}")
        return fake_result()

    monkeypatch.setattr(chain, "ask", ask)
    return calls


def handle(logger, question, student="s001", **kw):
    return service.handle(question, student_id=student, level="начинающий", channel="telegram",
                          user_ref=kw.pop("user", "chat-1"), index=None, logger=logger, **kw)


def test_mask_pii_and_hash():
    text = db_logger.mask_pii("пишите на ivan@mail.ru или +7 (912) 345-67-89, спасибо")
    assert "ivan@" not in text and "912" not in text and "[email]" in text and "[телефон]" in text
    assert db_logger.user_hash("123") == db_logger.user_hash("123") != db_logger.user_hash("124")
    assert len(db_logger.user_hash("123")) == 12


def test_log_row_has_no_raw_id_or_pii(logger):
    logger.log(channel="telegram", user_ref="555000", question="мой телефон 89123456789", status="answered")
    row = logger.rows()[0]
    assert row["user_hash"] != "555000" and "555000" not in str(row)
    assert row["question"] == "мой телефон [телефон]"


def test_retention_purges_old_rows(logger):
    old = datetime.now(timezone.utc) - timedelta(days=db_logger.RETENTION_DAYS + 1)
    logger.log(channel="web", user_ref="u", question="старый", status="answered", ts=old)
    logger.log(channel="web", user_ref="u", question="новый", status="answered")
    assert logger.purge() == 1
    assert [r["question"] for r in logger.rows()] == ["новый"]


def test_rejections_are_logged_with_reason(logger, stub_chain):
    assert handle(logger, "   ").status == "rejected"
    assert handle(logger, "я" * 1001).status == "rejected"
    stats = logger.get_stats()
    assert stats["rejected"] == {"пустой вопрос": 1, "слишком длинный вопрос": 1} and stub_chain == []


def test_daily_limit(logger, stub_chain):
    for i in range(3):
        assert handle(logger, f"вопрос номер {i}", daily_limit=3).status == "answered"
    assert handle(logger, "ещё один", daily_limit=3).text == service.REJECT_TEXT["дневной лимит"]
    assert handle(logger, "у другого пользователя", daily_limit=3, user="chat-2").status == "answered"


def test_general_answers_are_shared_between_students(logger, stub_chain):
    first = handle(logger, "Что такое эмбеддинги?", student="s001")
    second = handle(logger, "что такое  ЭМБЕДДИНГИ", student="s002")
    assert not first.from_cache and second.from_cache and len(stub_chain) == 1
    assert logger.get_stats()["from_cache"] == 1


def test_personal_answers_are_cached_per_student(logger, stub_chain):
    handle(logger, "Когда дедлайн по ДЗ-2?", student="s001")
    other = handle(logger, "Когда дедлайн по ДЗ-2?", student="s003")
    again = handle(logger, "Когда дедлайн по ДЗ-2?", student="s001")
    assert other.text == "Дедлайн для s003" and not other.from_cache  # never another student's deadline
    assert again.from_cache and len(stub_chain) == 2


def test_follow_up_questions_skip_cache(logger, stub_chain):
    handle(logger, "Что такое эмбеддинги?")
    r = handle(logger, "Что такое эмбеддинги?", history=[{"role": "user", "content": "привет"}])
    assert not r.from_cache and len(stub_chain) == 2


def test_errors_are_logged_and_raised(logger, monkeypatch):
    def boom(*a, **kw):
        raise TimeoutError
    monkeypatch.setattr(chain, "ask", boom)
    with pytest.raises(TimeoutError):
        handle(logger, "вопрос")
    row = logger.rows()[0]
    assert row["status"] == "error" and row["reject_reason"] == "TimeoutError"


def test_stats_and_csv_export(logger, stub_chain, tmp_path):
    handle(logger, "Что такое эмбеддинги?")
    handle(logger, "Что такое эмбеддинги?", user="chat-2")
    handle(logger, "")
    s = logger.get_stats()
    assert (s["total"], s["answered"], s["users"], s["from_cache"], s["by_channel"]) == (3, 2, 2, 1, {"telegram": 3})
    assert s["avg_ms"] == 1200 and s["tokens_in"] == 4000 and s["cost_usd"] > 0
    with logger.export_csv(tmp_path / "logs.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3 and set(rows[0]) == set(db_logger.COLUMNS)
