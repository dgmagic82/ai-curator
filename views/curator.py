"""Page «Куратор»: chat with the AI curator."""

import uuid

import streamlit as st

from curator import config, kb, lms, service
from curator.db_logger import DatabaseLogger

MODULES = {None: "Все модули"} | {m["module"]: f"Модуль {m['module']}. {m['title']}" for m in lms.get_schedule()["modules"]}
TYPES = {None: "Все материалы", "лекция": "Лекции", "методичка": "Методички", "faq": "FAQ", "ресурсы": "Доп. материалы"}


@st.cache_resource
def index():
    return kb.load_index()


@st.cache_resource
def logger():
    return DatabaseLogger()


@st.cache_data
def topics(module):
    return kb.topics(module)


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
    topic = st.selectbox("Тема", [None] + topics(module), format_func=lambda t: t or "Все темы")
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
st.session_state.setdefault("uid", uuid.uuid4().hex)  # anonymous session id, hashed again before logging

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
    st.info("Лимит демо: 15 вопросов за сессию исчерпан.")
elif question := st.chat_input("Спросите о дедлайнах, материалах или что почитать дальше"):
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Ищу в материалах курса…"):
            try:
                r = service.handle(question, student_id=sid, level=level, channel="web",
                                   user_ref=st.session_state.uid, index=index(), logger=logger(),
                                   history=st.session_state.messages, module=module, doc_type=doc_type, topic=topic)
            except Exception as e:
                st.error(f"Не получилось ответить: {type(e).__name__}. Попробуйте ещё раз.")
                st.stop()
        st.markdown(r.text)
        if r.status == "rejected":
            st.stop()
        if r.escalate:
            st.warning("Этот вопрос решает куратор-человек: форма «Заявка куратору».")
        if r.sources:
            st.caption("Источники: " + "; ".join(s["source"] for s in r.sources))
        elif r.category != "вне рамок":
            st.caption("Источник в материалах курса не найден.")
        with st.expander("Как искал ответ"):
            st.write({"категория": r.category, "время, мс": r.response_time_ms,
                      "из кэша": "да" if r.from_cache else "нет"})
    st.session_state.asked += 1
    st.session_state.messages += [{"role": "user", "content": question.strip()},
                                  {"role": "assistant", "content": r.text, "sources": r.sources}]
