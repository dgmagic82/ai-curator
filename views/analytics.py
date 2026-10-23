"""Page «Аналитика»: what students ask, where answers come from, where the content has gaps."""

import json
import re

import pandas as pd
import streamlit as st

from curator import config
from curator.db_logger import DatabaseLogger
from curator.logging_ import read_log

MODULE_NAMES = {0: "Организационное", 1: "1. Основы", 2: "2. Техники", 3: "3. RAG", 4: "4. Агенты"}


def doc_module(doc_id: str) -> int:
    m = re.match(r"(?:lec|gd)-(\d)", doc_id)
    return int(m.group(1)) if m else 0  # faq, reading lists and LMS data are organizational


def live_frame() -> pd.DataFrame:
    df = pd.DataFrame(DatabaseLogger().rows())
    if df.empty:
        return df
    df["sources"] = df["sources"].map(json.loads)
    df["escalate"] = df["escalate"].astype(bool)
    return df


def sample_frame() -> pd.DataFrame:
    df = pd.DataFrame(read_log(config.SAMPLE_LOG_FILE))
    return df.assign(status="answered", channel="eval", from_cache=0, reject_reason=None,
                     response_time_ms=df["latency_ms"])


st.title("📊 Аналитика вопросов к куратору")
live = live_frame()
use_sample = st.toggle("Показать пример лога (прогон 30 контрольных вопросов)", value=live.empty)
df = sample_frame() if use_sample else live
if df.empty:
    st.info("Лог пока пуст: задайте куратору несколько вопросов.")
    st.stop()

ok = df[df["status"] == "answered"].copy()
ok["без источника"] = (ok["sources"].str.len() == 0) & (ok["category"] != "вне рамок")
fresh = ok[ok["from_cache"] == 0]

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Запросов", len(df), help="Включая отклонённые")
c2.metric("Время ответа", f"{fresh['response_time_ms'].mean() / 1000:.1f} с" if len(fresh) else "-",
          help=f"Среднее без кэша, 95-й перцентиль {fresh['response_time_ms'].quantile(.95) / 1000:.1f} с"
          if len(fresh) else None)
c3.metric("Из кэша", f"{ok['from_cache'].mean():.0%}" if len(ok) else "-")
c4.metric("Переданы человеку", f"{ok['escalate'].mean():.0%}" if len(ok) else "-")
c5.metric("Без источника", f"{ok['без источника'].mean():.0%}" if len(ok) else "-",
          help="Кандидаты на новый контент в базе знаний")

left, right = st.columns(2)
with left:
    st.subheader("Типы вопросов")
    st.bar_chart(ok["category"].value_counts(), horizontal=True, sort="-count")
with right:
    st.subheader("Модули, к которым относятся ответы")
    modules = ok["sources"].explode().dropna().map(doc_module).map(MODULE_NAMES)
    st.bar_chart(modules.value_counts(), horizontal=True, sort="-count")

left, right = st.columns(2)
with left:
    st.subheader("Самые цитируемые документы")
    st.bar_chart(ok["sources"].explode().dropna().value_counts().head(10), horizontal=True, sort="-count")
with right:
    st.subheader("Каналы и отказы")
    st.bar_chart(df["channel"].value_counts(), horizontal=True, sort="-count")
    rejected = df[df["status"] != "answered"]
    if len(rejected):
        st.dataframe(rejected.groupby(["status", "reject_reason"]).size().rename("запросов").reset_index(),
                     hide_index=True)
    else:
        st.caption("Отклонённых запросов и ошибок нет.")

st.subheader("Вопросы без источника: чего не хватает в базе")
gaps = ok[ok["без источника"]][["ts", "question", "category"]]
if len(gaps):
    st.dataframe(gaps, hide_index=True)
else:
    st.caption("Таких вопросов нет.")

st.subheader("Последние запросы")
st.dataframe(df.sort_values("ts", ascending=False)[["ts", "channel", "status", "category", "from_cache", "escalate",
                                                      "question", "sources", "response_time_ms"]].head(30),
             hide_index=True)
st.caption(f"Расход: {df['tokens_in'].sum():,} входных и {df['tokens_out'].sum():,} выходных токенов. "
           "Пользователи в логе обезличены: хеш вместо id, телефоны и почты в вопросах замаскированы.")
