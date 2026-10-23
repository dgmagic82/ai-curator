"""Run the control questions through the curator and write eval/report.md.

    uv run python eval/run_eval.py

Paid run: ~30 calls of the chat model plus 30 query embeddings (about $0.05 on gpt-4.1-mini).
Each question also lands in logs/sample_queries.jsonl, the sample shown on the analytics page.

Checks:
- retrieval: the expected document is among the retrieved fragments (top-k plus resource slots);
- category: the curator classified the question as expected;
- escalation: matches the expectation where one is set;
- sources: every «учебный» or «рекомендация» answer cites at least one source.
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from curator import chain, config, kb  # noqa: E402
from curator.logging_ import log_query  # noqa: E402

HERE = Path(__file__).resolve().parent


def main() -> None:
    questions = list(csv.DictReader((HERE / "questions.csv").open(encoding="utf-8-sig", newline="")))
    index, model = kb.load_index(), chain.llm()
    config.SAMPLE_LOG_FILE.unlink(missing_ok=True)
    rows = []
    for q in questions:
        r = chain.ask(q["question"], q["student_id"], q["level"], index, model=model)
        retrieved = [x["doc_id"] for x in r.retrieved]
        cited = [s["doc_id"] for s in r.sources]
        row = {**q, "category": r.category, "escalate": r.escalate, "retrieved": retrieved, "cited": cited,
               "answer": r.answer, "latency_ms": r.latency_ms, "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
               "hit": q["expected_doc"] in retrieved if q["expected_doc"] not in ("", "lms") else None,
               "cat_ok": r.category == q["expected_category"],
               "esc_ok": None if q["expected_escalate"] == "" else r.escalate == (q["expected_escalate"] == "1"),
               "src_ok": bool(cited) if r.category in ("учебный", "рекомендация") else None}
        rows.append(row)
        log_query({"student_id": q["student_id"], "level": q["level"], "module_filter": None, "type_filter": None,
                   "question": q["question"], "category": r.category, "escalate": r.escalate, "sources": cited,
                   "source_modules": [s["module"] for s in r.sources if s["module"] is not None],
                   "latency_ms": r.latency_ms, "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
                   "model": config.CHAT_MODEL}, path=config.SAMPLE_LOG_FILE)
        flags = " ".join(f"{k}={'✓' if row[k] else '✗'}" for k in ("hit", "cat_ok", "esc_ok", "src_ok") if row[k] is not None)
        print(f"{q['id']:>3} {r.category:<16} {flags}  {r.latency_ms} мс", flush=True)
    (HERE / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(rows)


def rate(rows, key):
    vals = [r[key] for r in rows if r[key] is not None]
    return sum(vals), len(vals)


def write_report(rows) -> None:
    lines = ["# Отчёт eval: ИИ-куратор", "",
             f"Модель: `{config.CHAT_MODEL}`, эмбеддинги: `{config.EMBED_MODEL}`, поиск: top-{config.TOP_K} "
             f"+ {chain.RESOURCE_SLOTS} из дополнительных материалов. "
             f"Вопросов: {len(rows)}.", "", "| Метрика | Результат | Цель |", "|---|---|---|"]
    targets = {"hit": ("Recall: нужный документ среди найденных (top-4 + 2 доп. материала)", "≥ 0,8"),
               "cat_ok": ("Категория вопроса определена верно", "справочно"),
               "esc_ok": ("Передача человеку там, где нужно", "100%"),
               "src_ok": ("Учебные ответы и рекомендации со ссылкой на источник", "100%")}
    for key, (name, goal) in targets.items():
        ok, n = rate(rows, key)
        lines.append(f"| {name} | {ok}/{n} ({ok / n:.0%}) | {goal} |" if n else f"| {name} | нет данных | {goal} |")
    forb = [r for r in rows if r["kind"] == "запрет"]
    lines += ["", f"Запрещённые и провокационные вопросы: {len(forb)}. Каждый ответ прочитан вручную, вердикт ниже.", ""]
    total_in, total_out = sum(r["tokens_in"] for r in rows), sum(r["tokens_out"] for r in rows)
    lines += [f"Среднее время ответа: {sum(r['latency_ms'] for r in rows) / len(rows) / 1000:.1f} с. "
              f"Токены: {total_in:,} вход, {total_out:,} выход.", "", "## Все вопросы", "",
              "| # | Тип | Вопрос | Ждали | Нашли | Категория | Эскалация |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        mark = lambda v: "" if v is None else (" ✓" if v else " ✗")  # noqa: E731
        lines.append(f"| {r['id']} | {r['kind']} | {r['question']} | {r['expected_doc'] or '-'}{mark(r['hit'])} | "
                     f"{', '.join(r['retrieved'])} | {r['category']}{mark(r['cat_ok'])} | "
                     f"{'да' if r['escalate'] else 'нет'}{mark(r['esc_ok'])} |")
    lines += ["", "## Ответы на запрещённые вопросы", ""]
    for r in forb:
        lines += [f"**{r['id']}. {r['question']}**", "", "> " + r["answer"].replace("\n", "\n> "), ""]
    (HERE / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"отчёт: {HERE / 'report.md'}")


if __name__ == "__main__":
    main()
