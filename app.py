"""ИИ-куратор: entry point with two pages.

    uv run streamlit run app.py
"""

import streamlit as st

st.set_page_config(page_title="ИИ-куратор курса", page_icon="🎓", layout="wide")
st.navigation([st.Page("views/curator.py", title="Куратор", icon="🎓", default=True),
               st.Page("views/analytics.py", title="Аналитика", icon="📊")]).run()
