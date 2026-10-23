"""Offline tests: no network, no API key. Run with `uv run pytest`."""

from datetime import date

import pytest
from langchain_core.documents import Document

from curator import chain, kb, lms

DOC = """---
doc_id: lec-9-9
title: Тестовая лекция
module: 2
module_title: Продвинутые техники
topic: тест
type: лекция
level: все
---
# Тестовая лекция

## Первый раздел
Текст первого раздела.

## Второй раздел
Текст второго раздела.
"""


def test_parse_doc_reads_front_matter():
    meta, body = kb.parse_doc(DOC)
    assert meta["doc_id"] == "lec-9-9" and meta["module"] == 2 and meta["type"] == "лекция"
    assert body.lstrip().startswith("# Тестовая лекция")


def test_parse_doc_rejects_missing_keys():
    with pytest.raises(ValueError, match="level"):
        kb.parse_doc(DOC.replace("level: все\n", ""))


def test_split_doc_one_chunk_per_section_with_citation():
    chunks = kb.split_doc(*kb.parse_doc(DOC))
    assert [c.metadata["section"] for c in chunks] == ["Первый раздел", "Второй раздел"]
    assert chunks[0].metadata["source"] == "Тестовая лекция / Первый раздел"
    assert chunks[1].page_content.startswith("Тестовая лекция\n## Второй раздел")


def test_split_doc_splits_long_section():
    long = DOC.replace("Текст первого раздела.", "Длинное предложение про промпты. " * 120)
    chunks = kb.split_doc(*kb.parse_doc(long))
    first = [c for c in chunks if c.metadata["section"] == "Первый раздел"]
    assert len(first) > 1 and all(len(c.page_content) < kb.MAX_CHUNK_CHARS + 200 for c in first)


def test_whole_kb_parses():
    chunks = kb.load_chunks()
    types = {c.metadata["type"] for c in chunks}
    assert {"лекция", "методичка", "faq", "ресурсы"} <= types
    assert len({c.metadata["doc_id"] for c in chunks}) == 19  # 12 lectures, 4 guides, FAQ, 2 resource lists
    assert all("—" not in c.page_content for c in chunks)


@pytest.mark.parametrize("meta,filters,expected", [
    ({"module": 0, "type": "faq", "level": "все", "topic": "организационные вопросы"}, {"module": 3}, True),  # FAQ passes any module
    ({"module": 2, "type": "лекция", "level": "все"}, {"module": 3}, False),
    ({"module": 3, "type": "лекция", "level": "все"}, {"module": 3, "doc_type": "лекция"}, True),
    ({"module": 3, "type": "методичка", "level": "все"}, {"doc_type": "лекция"}, False),
    ({"module": 0, "type": "ресурсы", "level": "продвинутый"}, {"level": "начинающий"}, False),
    ({"module": 0, "type": "ресурсы", "level": "начинающий"}, {"level": "начинающий"}, True),
])
def test_make_filter(meta, filters, expected):
    assert kb.make_filter(**filters)(meta) is expected


def test_topic_filter_and_topic_list():
    meta = {"module": 3, "type": "лекция", "level": "все", "topic": "эмбеддинги"}
    assert kb.make_filter(topic="эмбеддинги")(meta) and not kb.make_filter(topic="агенты")(meta)
    all_topics, module3 = kb.topics(), kb.topics(3)
    assert set(module3) < set(all_topics)
    assert "организационные вопросы" in module3  # FAQ stays reachable under any module, as in make_filter


def test_assignments_use_personal_extension():
    by_id = {a["assignment_id"]: a for a in lms.get_assignments("s003", today=date(2026, 10, 23))}
    assert by_id["dz-2"]["extended"] and by_id["dz-2"]["effective_deadline"].startswith("2026-11-06")
    assert by_id["dz-2"]["days_left"] == 14
    other = {a["assignment_id"]: a for a in lms.get_assignments("s001", today=date(2026, 10, 23))}
    assert not other["dz-2"]["extended"] and other["dz-2"]["days_left"] == 10


def test_student_context_mentions_overdue_and_statuses():
    text = lms.student_context("s003", today=date(2026, 10, 23))
    assert "продлён куратором" in text and "Сегодня: 23.10.2026" in text
    assert "ДЗ-1" in text and "статус «" in text
    assert "заявку на продление можно подать до 05.11.2026 23:59 МСК" in text  # 24 h before the extended dz-2


def test_extension_request_deadline_is_day_before():
    assert "ДЗ-2: few-shot классификатор отзывов: дедлайн 02.11.2026 23:59 МСК, осталось 10 дн., статус «не сдано», "            "заявку на продление можно подать до 01.11.2026 23:59 МСК" in lms.student_context("s001", today=date(2026, 10, 23))


def test_resolve_sources_drops_unknown_numbers():
    hits = [(Document("x", metadata={"source": "Л / Р", "doc_id": "lec-1-1", "type": "лекция", "module": 1}), 0.1)]
    got = chain.resolve_sources([1, 0, 7, 1], hits)
    assert [s["n"] for s in got] == [1, 0]
    assert got[1]["source"] == chain.LMS_SOURCE


def test_system_prompt_fills_placeholders_for_both_levels():
    for level in ("начинающий", "продвинутый"):
        text = chain.system_prompt(level)
        assert "{{" not in text and level in text
    assert "бытовую аналогию" in chain.system_prompt("начинающий")
    assert "бытовую аналогию" not in chain.system_prompt("продвинутый")


def test_user_message_numbers_fragments():
    hits = [(Document("текст", metadata={"source": "Л / Р", "type": "лекция", "module": 1}), 0.1)]
    msg = chain.user_message("Вопрос?", "LMS", hits)
    assert msg.startswith("[0] ") and "[1] Источник: Л / Р" in msg and msg.endswith("Вопрос студента: Вопрос?")
