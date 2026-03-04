"""
DocDashApp — Streamlit app (Dashboard + Gestión) para expedientes KYC/PLD.
100% Streamlit (sin HTML/unsafe HTML).

Ejecutar:
  streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

from core.config import load_config, resolve_paths

st.set_page_config(
    page_title="DocDash",
    page_icon="📁",
    layout="wide",
    initial_sidebar_state="expanded",
)

@st.cache_resource
def _paths() -> dict:
    cfg = load_config()
    return resolve_paths(cfg)

try:
    paths = _paths()
except Exception as e:
    st.error(f"No pude cargar configuración: {e}")
    st.info("Revisa app_config.json junto a app.py (ver README).")
    st.stop()

# ------------------------------------------------------------
# Router state (NO es key del widget)
# ------------------------------------------------------------
# ------------------------------------------------------------
# Auto-scan después de guardados (chips/email/phone/tipo/rfc/curp, etc.)
# ------------------------------------------------------------
SCAN_FLAG_KEY = "_docdash_needs_scan"

if st.session_state.get(SCAN_FLAG_KEY, False):
    # Baja la bandera ANTES del scan para evitar loops si algo truena.
    st.session_state[SCAN_FLAG_KEY] = False

    from core.scan import scan as run_scan

    with st.spinner("Recalculando outputs (checklist / operations / anomalies)..."):
        ok_scan, msg_scan = run_scan(paths)

    # Evita lecturas viejas cacheadas
    st.cache_data.clear()

    if not ok_scan:
        st.error(f"Guardado OK, pero falló el scan: {msg_scan}")
    else:
        st.success("Outputs actualizados ✅")

# ------------------------------------------------------------
# Router state (NO es key del widget)
# ------------------------------------------------------------
PAGE_STATE = "page_nav"           # source of truth (editable desde cualquier vista)
PAGE_WIDGET = "page_nav_widget"   # key del radio (no lo tocamos directo después)
pages = ["📊 Dashboard", "🗂️ Gestión"]

if PAGE_STATE not in st.session_state:
    st.session_state[PAGE_STATE] = pages[0]

# Si el estado pide otra página, resetea el widget ANTES de crearlo
# (así Streamlit permite que cambie el default)
if PAGE_WIDGET in st.session_state and st.session_state[PAGE_WIDGET] != st.session_state[PAGE_STATE]:
    st.session_state.pop(PAGE_WIDGET, None)

def _sync_page_from_widget():
    st.session_state[PAGE_STATE] = st.session_state.get(PAGE_WIDGET, pages[0])

st.sidebar.title("📁 DocDash")
st.sidebar.caption(f"Data: {paths['DATA_DIR']}")
st.sidebar.caption(f"Output: {paths['OUTPUT_DIR']}")
st.sidebar.markdown("---")

# default index basado en PAGE_STATE
try:
    default_index = pages.index(st.session_state[PAGE_STATE])
except ValueError:
    default_index = 0

st.sidebar.radio(
    "Sección",
    pages,
    index=default_index,
    key=PAGE_WIDGET,
    on_change=_sync_page_from_widget,
    label_visibility="collapsed",
)

# NO sync extra aquí — PAGE_STATE solo cambia vía on_change o por código explícito.
# El sync automático era el que regresaba al usuario al Dashboard sin querer.

st.sidebar.markdown("---")

# Router
page = st.session_state[PAGE_STATE]

if page == "📊 Dashboard":
    from views.dashboard import render
    render(paths)
else:
    from views.gestion import render
    render(paths)