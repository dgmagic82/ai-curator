"""Page «Аналитика»: what students ask, where answers come from, where the content has gaps."""

import pandas as pd
import streamlit as st

from curator import config
from curator.logging_ import read_log

st.title("📊 Аналитика вопросов к куратору")

live = read_log(config.LOG_FILE)
use_sample = st.toggle("Показать пример лога (прогон 30 контрольных вопросов)", value=not live)
rows = read_log(config.SAMPLE_LOG_FILE) if use_sample else live
if not rows:
    st.info("Лог пока пуст: задайте куратору несколько вопросов.")
    st.stop()

df = pd.DataFrame(rows)
df["без источника"] = (df["sources"].str.len() == 0) & (df["category"] != "вне рамок")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Вопросов", len(df))
c2.metric("Среднее время ответа", f"{df['latency_ms'].mean() / 1000:.1f} с")
c3.metric("Переданы человеку", f"{df['escalate'].mean():.0%}")
c4.metric("Без источника", f"{df['без источника'].mean():.0%}", help="Кандидаты на новый контент в базе знаний")

left, right = st.columns(2)
with left:
    st.subheader("Типы вопросов")
    st.bar_chart(df["category"].value_counts(), horizontal=True, sort="-count")
with right:
    st.subheader("Модули, к которым относятся ответы")
    names = {0: "Организационное", 1: "1. Основы", 2: "2. Техники", 3: "3. RAG", 4: "4. Агенты"}
    modules = df["source_modules"].explode().dropna().astype(int).map(names)
    st.bar_chart(modules.value_counts(), horizontal=True, sort="-count")

st.subheader("Самые цитируемые документы")
st.bar_chart(df["sources"].explode().dropna().value_counts().head(10), horizontal=True, sort="-count")

st.subheader("Вопросы без источника: чего не хватает в базе")
gaps = df[df["без источника"]][["ts", "question", "category"]]
if len(gaps):
    st.dataframe(gaps, hide_index=True)
else:
    st.caption("Таких вопросов нет.")

st.subheader("Последние вопросы")
st.dataframe(df.sort_values("ts", ascending=False)[["ts", "student_id", "level", "category", "escalate", "question",
                                                      "sources", "latency_ms"]].head(30),
             hide_index=True)
st.caption(f"Расход: {df['tokens_in'].sum():,} входных и {df['tokens_out'].sum():,} выходных токенов, "
           f"модель {df['model'].iloc[-1]}.")
