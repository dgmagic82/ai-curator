"""Page «Куратор»: chat with the AI curator."""

import streamlit as st

from curator import chain, config, kb, lms
from curator.logging_ import log_query


MODULES = {None: "Все модули"} | {m["module"]: f"Модуль {m['module']}. {m['title']}" for m in lms.get_schedule()["modules"]}
TYPES = {None: "Все материалы", "лекция": "Лекции", "методичка": "Методички", "faq": "FAQ", "ресурсы": "Доп. материалы"}


@st.cache_resource
def index():
    return kb.load_index()


with st.sidebar:
    st.header("Студент")
    students = {s["student_id"]: s for s in lms.get_students()}
    sid = st.selectbox("Кто спрашивает", list(students), format_func=lambda i: f"{students[i]['name']} ({i})")
    level = st.radio("Уровень ответа", ["начинающий", "продвинутый"],
                     index=0 if students[sid]["level"] == "начинающий" else 1,
                     help="По умолчанию берётся из профиля в LMS, можно переключить",
                     key=f"level_{sid}")  # per-student key: a new student starts from their own profile level
    st.header("Где искать")
    module = st.selectbox("Модуль", list(MODULES), format_func=MODULES.get,
                          help="Организационные вопросы (FAQ) доступны при любом модуле")
    doc_type = st.selectbox("Тип материала", list(TYPES), format_func=TYPES.get)
    st.caption(f"Демо-дата LMS: {config.DEMO_TODAY:%d.%m.%Y}. Студенты и курс вымышленные.")
    with st.expander("Задания студента в LMS"):
        for a in lms.get_assignments(sid):
            st.markdown(f"**{a['title']}**: {a['status']}, дедлайн {a['effective_deadline'][:10]}"
                        + (" (продлён)" if a["extended"] else ""))

# a new student or level starts a new dialog: history of one student must not leak to another
key = f"{sid}:{level}"
if st.session_state.get("dialog_key") != key:
    st.session_state.dialog_key, st.session_state.messages = key, []
st.session_state.setdefault("asked", 0)

st.title("🎓 ИИ-куратор курса")
st.caption("«Промпт-инжиниринг: от первого запроса до агента». Отвечаю по материалам курса и данным LMS, "
           "всегда со ссылкой на источник. Оценки, дедлайны и расписание меняет только куратор-человек.")

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("sources"):
            st.caption("Источники: " + "; ".join(s["source"] for s in m["sources"]))

left = config.MAX_QUESTIONS_PER_SESSION - st.session_state.asked
if left <= 0:
    st.info("Лимит демо: 15 вопросов за сессию. Обновите страницу позже.")
elif question := st.chat_input("Спросите о дедлайнах, материалах или что почитать дальше"):
    question = question.strip()[:config.MAX_QUESTION_CHARS]
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Ищу в материалах курса…"):
            try:
                r = chain.ask(question, sid, level, index(), history=st.session_state.messages,
                              module=module, doc_type=doc_type)
            except Exception as e:
                st.error(f"Не получилось ответить: {type(e).__name__}. Попробуйте ещё раз.")
                st.stop()
        st.markdown(r.answer)
        if r.escalate:
            st.warning("Этот вопрос решает куратор-человек: форма «Заявка куратору».")
        if r.sources:
            st.caption("Источники: " + "; ".join(s["source"] for s in r.sources))
        elif r.category != "вне рамок":
            st.caption("Источник в материалах курса не найден.")
        with st.expander("Как искал ответ"):
            st.write({"категория": r.category, "время, мс": r.latency_ms,
                      "токены": f"{r.tokens_in} вход / {r.tokens_out} выход", "найденные фрагменты": r.retrieved})
    st.session_state.asked += 1
    st.session_state.messages += [{"role": "user", "content": question},
                                  {"role": "assistant", "content": r.answer, "sources": r.sources}]
    log_query({"student_id": sid, "level": level, "module_filter": module, "type_filter": doc_type,
               "question": question, "category": r.category, "escalate": r.escalate,
               "sources": [s["doc_id"] for s in r.sources],
               "source_modules": [s["module"] for s in r.sources if s["module"] is not None],
               "latency_ms": r.latency_ms, "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
               "model": config.CHAT_MODEL})
