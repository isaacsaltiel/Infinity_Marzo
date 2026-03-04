"""views/dashboard.py - Dashboard (semaforo, faltantes por chips, ops, anomalias, notas)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Tuple

import pandas as pd
import streamlit as st

from core.actions import (
    open_in_os,
    save_file_for_rule,
    update_client,
    update_party,
)
from core.models import ANOMALIES_COLS, CHECKLIST_COLS, OPERATIONS_COLS
from core.rules import DOC_RULES
from core.scan import scan as run_scan
from core.storage import atomic_write_json, read_csv, read_json


PAGE_KEY = "page_nav"           # debe matchear app.py
OPEN_KEY = "_dash_open_client"  # cliente actualmente abierto en dashboard
SCAN_FLAG_KEY = "_docdash_needs_scan"  # si Gestión tocó datos, al volver a Dashboard se recalcula Output


# ---------------------------------------------------------------------
# Helpers (I/O + parsing)
# ---------------------------------------------------------------------
def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0

@st.cache_data(show_spinner=False)
def _load_csv(path_str: str, cols: list[str], mtime: float) -> pd.DataFrame:
    _ = mtime
    path = Path(path_str)

    pending = sorted(
        path.parent.glob(f"{path.stem}__PENDING__*{path.suffix}"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if pending:
        if (not path.exists()) or (pending[0].stat().st_mtime > path.stat().st_mtime):
            path = pending[0]

    if not path.exists():
        return pd.DataFrame(columns=cols)

    df = pd.read_csv(path, dtype=str).fillna("")
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]

@st.cache_data(show_spinner=False)
def _load_notes(path_str: str, mtime: float) -> dict:
    _ = mtime
    p = Path(path_str)
    if not p.exists():
        return {}
    return read_json(p, default={})

def _parse_list(s: str) -> list[str]:
    if not s:
        return []
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return [str(x) for x in v if str(x)]
        return []
    except Exception:
        return [x for x in str(s).split("|") if x]

def _boolish(x) -> bool:
    s = str(x).strip().lower()
    return s in {"1", "true", "t", "si", "sí", "y", "yes"}

def _mark(x) -> str:
    if str(x).strip() == "":
        return "--"
    return "✅" if _boolish(x) else "❌"

def _safe_pct(x: Any) -> float:
    """
    Interpreta bien valores 0..1 y los pasa a porcentaje.
    Tolera "76.9%" y strings.
    """
    if x is None:
        return 0.0
    s = str(x).strip()
    if not s:
        return 0.0
    if s.endswith("%"):
        s = s[:-1].strip()
    try:
        v = float(s)
    except Exception:
        return 0.0

    if 0.0 <= v <= 1.0:
        v *= 100.0

    if v < 0:
        return 0.0
    if v > 100:
        return 100.0
    return v


# ---------------------------------------------------------------------
# Unified "save + rebuild output"
# ---------------------------------------------------------------------
def _rebuild_output(paths: dict, label_ok: str = "Output actualizado ✅") -> Tuple[bool, str]:
    # 🔥 CLAVE: limpia cache ANTES del scan (para que lea clientes_master actualizado)
    st.cache_data.clear()

    status = st.empty()
    bar = st.progress(0)

    try:
        status.info("Paso 1/2 · Preparando actualización…")
        bar.progress(15)

        status.info("Paso 2/2 · Escaneando y recalculando Output…")
        bar.progress(45)

        ok, msg = run_scan(paths)

        bar.progress(95)
        if ok:
            status.success(label_ok)
            st.toast(label_ok, icon="✅")
        else:
            status.error("Falló el recálculo de Output.")
            st.toast("Falló el recálculo de Output", icon="⚠️")

        bar.progress(100)
        return ok, msg
    finally:
        # deja un micro “feedback” visual antes del rerun si lo hay
        pass


def _after_mutation(paths: dict, client_id: str, ok: bool, msg: str, *, rebuild: bool = True) -> None:
    (st.success if ok else st.error)(msg)
    if not ok:
        return

    if rebuild:
        ok2, msg2 = _rebuild_output(paths)
        (st.success if ok2 else st.error)(msg2)

    st.session_state["_docdash_focus_client"] = client_id
    st.cache_data.clear()  # ok dejarlo aquí también
    st.rerun()



# ---------------------------------------------------------------------
# Navegacion a Gestion
# ---------------------------------------------------------------------
def _go_gestion_client(client_id: str, section: str = "Clientes") -> None:
    st.session_state[PAGE_KEY] = "🗂️ Gestión"
    st.session_state["_nav_gestion_section"] = section
    st.session_state["_nav_gestion_client"] = client_id
    st.rerun()

def _go_gestion_op(client_id: str, op_folder: str) -> None:
    st.session_state[PAGE_KEY] = "🗂️ Gestión"
    st.session_state["_nav_gestion_section"] = "Operaciones"
    st.session_state["_nav_gestion_client"] = client_id
    st.session_state["_nav_gestion_op"] = op_folder
    st.rerun()


# ---------------------------------------------------------------------
# Layout toggle
# ---------------------------------------------------------------------
def _toggle_open_client(client_id: str) -> None:
    cur = str(st.session_state.get(OPEN_KEY, "") or "")
    if cur == client_id:
        st.session_state[OPEN_KEY] = ""
        st.session_state["_docdash_focus_client"] = ""
    else:
        st.session_state[OPEN_KEY] = client_id
        st.session_state["_docdash_focus_client"] = client_id


# ---------------------------------------------------------------------
# Labeling (chips)
# ---------------------------------------------------------------------
def label_for(k: str) -> str:
    if k == "CLIENTE_EMAIL":
        return "Email"
    if k == "CLIENTE_PHONE":
        return "Teléfono"
    if k == "CLIENTE_TIPO_PERSONA":
        return "Tipo"
    if k == "INE_CLIENTE":
        return "INE"

    if k.startswith("PARTE::"):
        try:
            _, role, pid, kk = k.split("::", 3)
        except ValueError:
            return k

        short = "REP" if role == "REP_LEGAL" else ("AV" if role == "AVAL" else role[:2])
        try:
            n = int(pid)
        except Exception:
            n = pid
        prefix = f"{short}{n}"

        if kk == "INE_PARTE":
            return f"{prefix} · INE"
        if kk in {"PARTE_EMAIL", "PARTE_PHONE"}:
            return f"{prefix} · {'EMAIL' if kk.endswith('EMAIL') else 'TEL'}"
        if kk in DOC_RULES:
            return f"{prefix} · {DOC_RULES[kk].label}"
        return f"{prefix} · {kk}"

    if k.startswith("OP::"):
        try:
            _, op_folder, kk = k.split("::", 2)
            op_id = op_folder.split("__")[1] if "__" in op_folder else op_folder
            lab = DOC_RULES[kk].label if kk in DOC_RULES else kk
            return f"{op_id} · {lab}"
        except Exception:
            return k

    return DOC_RULES[k].label if k in DOC_RULES else k

def _request_dialog(payload: str) -> None:
    st.session_state["_docdash_dialog"] = payload
    st.session_state["_docdash_focus_client"] = payload.split("|", 1)[0] if "|" in payload else None

def _chip_grid(labels_and_payloads: list[Tuple[str, str]], cols: int = 5) -> None:
    if not labels_and_payloads:
        st.caption("Sin faltantes 🎉")
        return

    grid_id = st.session_state.get("_chipgrid_seq", 0)
    st.session_state["_chipgrid_seq"] = grid_id + 1

    rows = [labels_and_payloads[i : i + cols] for i in range(0, len(labels_and_payloads), cols)]
    for r_idx, r in enumerate(rows):
        cs = st.columns(cols, gap="small")
        for c_idx in range(cols):
            with cs[c_idx]:
                if c_idx >= len(r):
                    st.write("")
                    continue
                label, payload = r[c_idx]
                btn_key = f"chipbtn::{grid_id}::{payload}::r{r_idx}::c{c_idx}"
                st.button(
                    label,
                    key=btn_key,
                    use_container_width=True,
                    on_click=_request_dialog,
                    args=(payload,),
                )


# ---------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------
def _note_key(client_id: str) -> str:
    return f"client:{client_id}"

def _save_note(notes_path: Path, client_id: str, text: str) -> Tuple[bool, str]:
    data = read_json(notes_path, default={})
    data[_note_key(client_id)] = {"text": text, "updated": str(date.today())}
    return atomic_write_json(data, notes_path)


# ---------------------------------------------------------------------
# Client + OP helpers
# ---------------------------------------------------------------------
def _safe_date(s: Any):
    try:
        return date.fromisoformat(str(s)) if str(s).strip() else date.today()
    except Exception:
        return date.today()

def _client_row(paths: dict, client_id: str) -> dict:
    df = read_csv(
        paths["CLIENTES_MASTER"],
        [
            "client_id",
            "folder_name",
            "display_name",
            "legal_name",
            "tipo_persona",
            "email",
            "phone",
            "rfc",
            "curp",
            "created_date",
            "client_status",
        ],
    ).fillna("")
    row = df[df["client_id"] == client_id]
    return row.iloc[0].to_dict() if not row.empty else {}

def _client_folder(paths: dict, client_id: str) -> Path:
    row = _client_row(paths, client_id)
    folder = str(row.get("folder_name", "")).strip()
    if not folder:
        return paths["DATA_DIR"] / "_MISSING_"
    return paths["DATA_DIR"] / folder

def _read_op_meta(paths: dict, client_id: str, op_folder: str) -> dict:
    p = _client_folder(paths, client_id) / "_OPERACIONES" / op_folder / "OP_META.json"
    return read_json(p, default={})

def _write_op_meta(paths: dict, client_id: str, op_folder: str, meta: dict) -> None:
    p = _client_folder(paths, client_id) / "_OPERACIONES" / op_folder / "OP_META.json"
    atomic_write_json(meta, p)

def _party_row(paths: dict, client_id: str, role: str, pid: str) -> dict:
    if "PARTES_RELACIONADAS" not in paths:
        return {}
    df = read_csv(paths["PARTES_RELACIONADAS"], ["client_id", "role", "party_id", "nombre", "email", "phone", "rfc", "curp"]).fillna("")
    pid_norm = str(pid).strip()
    role_norm = str(role).strip().upper()
    row = df[
        (df["client_id"].astype(str).str.strip() == str(client_id).strip()) &
        (df["role"].astype(str).str.strip().str.upper() == role_norm) &
        (df["party_id"].astype(str).str.strip() == pid_norm)
    ]
    return row.iloc[0].to_dict() if not row.empty else {}

def _parte_folder_from_role_pid(paths: dict, client_id: str, role: str, pid: str) -> str:
    root = _client_folder(paths, client_id) / "_PARTES"
    if not root.exists():
        return ""
    pref = f"{role.upper()}__{str(pid).zfill(2)}__"
    for d in root.iterdir():
        if d.is_dir() and d.name.upper().startswith(pref):
            return d.name
    return ""


# ---------------------------------------------------------------------
# Dialog: resolve missing
# ---------------------------------------------------------------------
@st.dialog("Editar faltante", width="large")
def _dialog_missing(paths: dict, client_id: str, missing_key: str) -> None:
    st.write(f"Cliente: **{client_id}**")
    st.divider()

    # ---------------- PARTES ----------------
    if missing_key.startswith("PARTE::"):
        _, role, pid, key = missing_key.split("::", 3)
        st.subheader(f"Parte: {role} {pid}")

        prow = _party_row(paths, client_id, role, pid)

        if key in {"PARTE_EMAIL", "PARTE_PHONE", "PARTE_RFC", "PARTE_CURP"}:
            field_map = {"PARTE_EMAIL": "email", "PARTE_PHONE": "phone", "PARTE_RFC": "rfc", "PARTE_CURP": "curp"}
            field = field_map[key]
            current = str(prow.get(field, "")).strip()
            val = st.text_input(
                field.upper(),
                value=current,
                placeholder="Escribe y guarda (vacío = borrar)",
                key=f"dlg_{client_id}_{missing_key}_{field}",
            )
            if st.button("Guardar", type="primary"):
                ok, msg = update_party(paths, client_id, role, pid, {field: val.strip()})
                _after_mutation(paths, client_id, ok, msg, rebuild=True)
            return

        if key == "INE_PARTE":
            st.caption("Sube 1 archivo (FRENTE) o 2 (FRENTE y REVERSO).")
            ups = st.file_uploader("INE", type=None, accept_multiple_files=True)
            if st.button("Guardar INE", type="primary", disabled=not ups):
                pf = _parte_folder_from_role_pid(paths, client_id, role, pid)
                if len(ups) >= 1:
                    ok1, msg1, _ = save_file_for_rule(
                        paths, client_id, "INE_FRENTE_PARTE", ups[0].name, ups[0].getbuffer(), parte_folder=pf
                    )
                else:
                    ok1, msg1 = False, "No subiste archivo."
                if len(ups) >= 2:
                    ok2, msg2, _ = save_file_for_rule(
                        paths, client_id, "INE_REVERSO_PARTE", ups[1].name, ups[1].getbuffer(), parte_folder=pf
                    )
                else:
                    ok2, msg2 = True, "OK (sin reverso)"
                ok = ok1 and ok2
                msg = f"{msg1} | {msg2}"
                _after_mutation(paths, client_id, ok, msg, rebuild=True)
            return

        if key in DOC_RULES:
            up = st.file_uploader(DOC_RULES[key].label, type=None)
            if st.button("Guardar documento", type="primary", disabled=not up):
                pf = _parte_folder_from_role_pid(paths, client_id, role, pid)
                ok, msg, _ = save_file_for_rule(paths, client_id, key, up.name, up.getbuffer(), parte_folder=pf)
                _after_mutation(paths, client_id, ok, msg, rebuild=True)
            return

        st.info("Faltante de parte no reconocido.")
        return

    # ---------------- OPERACIONES ----------------
    if missing_key.startswith("OP::"):
        _, op_folder, key = missing_key.split("::", 2)
        st.subheader(f"Operación: {op_folder}")

        meta = _read_op_meta(paths, client_id, op_folder)

        # Permitir editar meta SIN obligar upload
        if key in {"PAGARE", "MUTUO"}:
            st.caption("Puedes guardar metadata sin subir doc. (Útil para borrar firma/vence).")
            no_firma = st.checkbox("Sin fecha de firma", value=(not str(meta.get("firma", "")).strip()))
            no_vence = st.checkbox("Sin fecha de vencimiento", value=(not str(meta.get("vence", "")).strip()))

            firma = None
            vence = None
            c1, c2 = st.columns(2, gap="small")
            with c1:
                if not no_firma:
                    firma = st.date_input("Firma", value=_safe_date(meta.get("firma")))
            with c2:
                if not no_vence:
                    vence = st.date_input("Vence", value=_safe_date(meta.get("vence")))

            up = st.file_uploader(DOC_RULES[key].label if key in DOC_RULES else key, type=None)

            b1, b2 = st.columns([1, 1], gap="small")
            if b1.button("Guardar solo metadata", type="secondary"):
                meta["firma"] = "" if no_firma else str(firma)
                meta["vence"] = "" if no_vence else str(vence)
                _write_op_meta(paths, client_id, op_folder, meta)
                _after_mutation(paths, client_id, True, "Metadata guardada.", rebuild=True)
                return

            if b2.button("Guardar doc + metadata", type="primary", disabled=not up):
                meta["firma"] = "" if no_firma else str(firma)
                meta["vence"] = "" if no_vence else str(vence)
                _write_op_meta(paths, client_id, op_folder, meta)
                ok, msg, _ = save_file_for_rule(
                    paths, client_id, key, up.name, up.getbuffer(), op_folder_name=op_folder, op_meta=meta
                )
                _after_mutation(paths, client_id, ok, msg, rebuild=True)
            return

        if key == "GARANTIA":
            tipo = st.text_input("TIPO de garantía (ej. HIPOTECA, PRENDA)", value="")
            up = st.file_uploader(DOC_RULES[key].label if key in DOC_RULES else "Garantía", type=None)
            if st.button("Guardar", type="primary", disabled=not up):
                ok, msg, _ = save_file_for_rule(
                    paths, client_id, key, up.name, up.getbuffer(), op_folder_name=op_folder, op_meta=meta, extra=tipo
                )
                _after_mutation(paths, client_id, ok, msg, rebuild=True)
            return

        if key in {"CONVENIO_MEDIACION", "CONVENIO_MODIFICATORIO", "RECIBO_EFECTIVO", "RECIBO_EFECTIVO_FIRMADO"}:
            extra = ""
            if key == "CONVENIO_MODIFICATORIO":
                extra = st.text_input("Tag (opcional)", value="")
            up = st.file_uploader(DOC_RULES[key].label if key in DOC_RULES else key, type=None)
            if st.button("Guardar documento", type="primary", disabled=not up):
                ok, msg, _ = save_file_for_rule(
                    paths, client_id, key, up.name, up.getbuffer(), op_folder_name=op_folder, op_meta=meta, extra=extra
                )
                _after_mutation(paths, client_id, ok, msg, rebuild=True)
            return

        st.info("Faltante de operación no reconocido.")
        return

    # ---------------- CLIENTE (datos) ----------------
    if missing_key in {"CLIENTE_EMAIL", "CLIENTE_PHONE", "CLIENTE_TIPO_PERSONA", "CLIENTE_RFC", "CLIENTE_CURP"}:
        crow = _client_row(paths, client_id)

        if missing_key == "CLIENTE_TIPO_PERSONA":
            cur = str(crow.get("tipo_persona", "PF")).strip().upper() or "PF"
            idx = 0 if cur == "PF" else 1
            val = st.selectbox(
                "tipo_persona",
                ["PF", "PM"],
                index=idx,
                key=f"dlg_{client_id}_{missing_key}_tipo",
            )
            field = "tipo_persona"
        else:
            field_map = {
                "CLIENTE_EMAIL": "email",
                "CLIENTE_PHONE": "phone",
                "CLIENTE_RFC": "rfc",
                "CLIENTE_CURP": "curp",
            }
            field = field_map.get(missing_key) or ("email" if missing_key.endswith("EMAIL") else "phone")
            cur = str(crow.get(field, "")).strip()
            val = st.text_input(field.upper(), value=cur, placeholder="Escribe y guarda (vacío = borrar)")

        if st.button("Guardar", type="primary"):
            ok, msg = update_client(paths, client_id, {field: str(val).strip()}, rename_folder=False)
            _after_mutation(paths, client_id, ok, msg, rebuild=True)
        return

    if missing_key == "INE_CLIENTE":
        st.caption("Sube 1 archivo (FRENTE) o 2 (FRENTE y REVERSO).")
        ups = st.file_uploader("INE", type=None, accept_multiple_files=True)
        if st.button("Guardar INE", type="primary", disabled=not ups):
            if len(ups) >= 1:
                ok1, msg1, _ = save_file_for_rule(paths, client_id, "INE_FRENTE_CLIENTE", ups[0].name, ups[0].getbuffer())
            else:
                ok1, msg1 = False, "No subiste archivo."
            if len(ups) >= 2:
                ok2, msg2, _ = save_file_for_rule(paths, client_id, "INE_REVERSO_CLIENTE", ups[1].name, ups[1].getbuffer())
            else:
                ok2, msg2 = True, "OK (sin reverso)"
            ok = ok1 and ok2
            msg = f"{msg1} | {msg2}"
            _after_mutation(paths, client_id, ok, msg, rebuild=True)
        return

    if missing_key in DOC_RULES and DOC_RULES[missing_key].scope == "CLIENTE":
        rule = DOC_RULES[missing_key]
        up = st.file_uploader(rule.label, type=None)
        if st.button("Guardar documento", type="primary", disabled=not up):
            ok, msg, _ = save_file_for_rule(paths, client_id, missing_key, up.name, up.getbuffer())
            _after_mutation(paths, client_id, ok, msg, rebuild=True)
        return

    st.info("Faltante no reconocido.")


# ---------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------
def render(paths: dict) -> None:
    # Si Gestión tocó algo, al volver al Dashboard se recalcula Output automáticamente
    if st.session_state.pop(SCAN_FLAG_KEY, False):
        ok, msg = _rebuild_output(paths, label_ok="Output actualizado (desde Gestión) ✅")
        (st.success if ok else st.error)(msg)
        st.cache_data.clear()

    # Top Actions (Botón recalcular)
    c_btn, c_tip, _ = st.columns([1, 2, 5])
    with c_btn:
        if st.button("🔄 Recalcular Todo", use_container_width=True, type="primary"):
            ok, msg = _rebuild_output(paths)
            (st.success if ok else st.error)(msg)
            st.cache_data.clear()
            st.rerun()
    with c_tip:
        st.caption("Tip: Presiona para actualizar los datos desde Output.")

    # consumir payload una vez
    payload = st.session_state.pop("_docdash_dialog", None)
    if payload:
        cid, mk = payload.split("|", 1)
        _dialog_missing(paths, cid, mk)
        

    # Columns: tolerate both old/new names
    checklist_cols = list(CHECKLIST_COLS)
    for extra in ["missing_p4", "p4_missing", "missing_p3_ops"]:
        if extra not in checklist_cols:
            checklist_cols.append(extra)

    ops_cols = list(OPERATIONS_COLS)
    anom_cols = list(ANOMALIES_COLS)

    checklist = _load_csv(str(paths["CHECKLIST_CSV"]), checklist_cols, _mtime(paths["CHECKLIST_CSV"]))
    ops = _load_csv(str(paths["OPERATIONS_CSV"]), ops_cols, _mtime(paths["OPERATIONS_CSV"]))
    anoms = _load_csv(str(paths["ANOMALIES_CSV"]), anom_cols, _mtime(paths["ANOMALIES_CSV"]))
    notes = _load_notes(str(paths["NOTES_JSON"]), _mtime(paths["NOTES_JSON"]))

    if checklist.empty:
        st.info("Aún no hay Output/checklist.csv. Presiona Recalcular Todo.")
        return

    # ---------------- KPI + filtros state ----------------
    if "_sem_filtro" not in st.session_state:
        st.session_state["_sem_filtro"] = "Todos"
    if "_f_q" not in st.session_state:
        st.session_state["_f_q"] = ""
    if "_status_filtro" not in st.session_state:
        st.session_state["_status_filtro"] = "Todos"
    if "_tipo_filtro" not in st.session_state:
        st.session_state["_tipo_filtro"] = "Todos"
    if "_sort_by" not in st.session_state:
        st.session_state["_sort_by"] = "Urgencia (Próx. Venc)"
    if "_vence60_filtro" not in st.session_state:
        st.session_state["_vence60_filtro"] = False

    totales = len(checklist)

    status_series = checklist.get("client_status", pd.Series([""] * totales)).astype(str).str.upper()
    activos = int((status_series == "ACTIVE").sum())

    sem_series = checklist.get("semaforo", pd.Series([""] * totales)).astype(str).str.upper()
    criticos = int((sem_series == "ROJO").sum())
    incompletos = int((sem_series == "AMARILLO").sum())
    pasables = int((sem_series == "VERDE").sum())
    completados = int((sem_series == "COMPLETADO").sum())

    ops_activas_col = checklist.get("active_ops_count", pd.Series(["0"] * totales)).astype(str)
    sin_ops = int((ops_activas_col == "0").sum())
    tot_anoms = len(anoms) if not anoms.empty else 0

    # Clientes con alguna op que vence en los próximos 60 días
    from datetime import date as _date
    _today = _date.today()
    _hoy   = pd.Timestamp(_today)
    _d60   = pd.Timestamp(_today.replace(year=_today.year + (1 if _today.month > 6 and _today.day > 2 else 0)) ) # placeholder
    _d60   = _hoy + pd.Timedelta(days=60)
    _nd    = pd.to_datetime(checklist.get("next_due", pd.Series([""] * totales)), errors="coerce")
    vence60 = int(((_nd >= _hoy) & (_nd <= _d60)).sum())

    # KPI "tarjetas" clickeables
    kpi_cols = st.columns(7, gap="small")
    with kpi_cols[0]:
        if st.button(f"TOTAL\n{totales}", use_container_width=True, help=f"{activos} activos"):
            st.session_state["_sem_filtro"] = "Todos"
            st.session_state["_vence60_filtro"] = False
            st.rerun()
    with kpi_cols[1]:
        if st.button(f"🔴 CRÍTICO\n{criticos}", use_container_width=True, help="Docs P1/P2 faltantes"):
            st.session_state["_sem_filtro"] = "🔴 Rojo"
            st.session_state["_vence60_filtro"] = False
            st.rerun()
    with kpi_cols[2]:
        if st.button(f"🟡 INCOMPLETO\n{incompletos}", use_container_width=True, help="Revisar P3/P4"):
            st.session_state["_sem_filtro"] = "🟡 Amarillo"
            st.session_state["_vence60_filtro"] = False
            st.rerun()
    with kpi_cols[3]:
        if st.button(f"🟢 PASABLE\n{pasables}", use_container_width=True, help="Operativo"):
            st.session_state["_sem_filtro"] = "🟢 Verde"
            st.session_state["_vence60_filtro"] = False
            st.rerun()
    with kpi_cols[4]:
        if st.button(f"🔵 COMPLETADO\n{completados}", use_container_width=True, help="Al 100%"):
            st.session_state["_sem_filtro"] = "🔵 Completado"
            st.session_state["_vence60_filtro"] = False
            st.rerun()
    with kpi_cols[5]:
        if st.button(f"⚪ SIN OPS\n{sin_ops}", use_container_width=True, help=f"{tot_anoms} anomalías totales"):
            st.session_state["_sem_filtro"] = "⚪ Sin ops"
            st.session_state["_vence60_filtro"] = False
            st.rerun()
    with kpi_cols[6]:
        active_vence60 = st.session_state.get("_vence60_filtro", False)
        btn_style = "primary" if active_vence60 else "secondary"
        if st.button(
            f"⏳ VENCE",
            use_container_width=True,
            type=btn_style,
            help="Operaciones que vencen en los próximos 60 días",
        ):
            st.session_state["_vence60_filtro"] = not active_vence60
            st.session_state["_sem_filtro"] = "Todos"
            st.rerun()

    st.write("")

    # ---------------- Filtros en barra ----------------
    f1, f2, f3, f4, f5, f6 = st.columns([1.6, 3.2, 1.0, 1.0, 1.4, 0.8], gap="small")

    with f1:
        q = st.text_input("🔍 Buscar...", value=st.session_state["_f_q"]).strip().upper()
        st.session_state["_f_q"] = q

    with f2:
        chip_opts = ["Todos", "🔴 Rojo", "🟡 Amarillo", "🟢 Verde", "🔵 Completado", "⚪ Sin ops"]
        default_sem = st.session_state.get("_sem_filtro", "Todos")
        idx_sem = chip_opts.index(default_sem) if default_sem in chip_opts else 0
        sem_chip = st.radio(
            "Semáforo",
            chip_opts,
            horizontal=True,
            label_visibility="collapsed",
            index=idx_sem,
        )
        st.session_state["_sem_filtro"] = sem_chip

    with f3:
        estatus_opts = ["Todos"] + sorted(
            list({str(x).strip().upper() for x in checklist.get("client_status", pd.Series(dtype=str)).tolist() if str(x).strip()})
        )
        default_status = st.session_state.get("_status_filtro", "Todos")
        if default_status not in estatus_opts:
            default_status = "Todos"
        estatus = st.selectbox("Estatus", estatus_opts, index=estatus_opts.index(default_status))
        st.session_state["_status_filtro"] = estatus

    with f4:
        tipo_opts = ["Todos", "PF", "PM"]
        default_tipo = st.session_state.get("_tipo_filtro", "Todos")
        if default_tipo not in tipo_opts:
            default_tipo = "Todos"
        tipo = st.selectbox("Tipo", tipo_opts, index=tipo_opts.index(default_tipo))
        st.session_state["_tipo_filtro"] = tipo

    with f5:
        sort_opts = [
            "Urgencia (Próx. Venc)",
            "Nombre (A-Z)",
            "Avance (Mayor %)",
            "Avance (Menor %)",
            "Más anomalías",
        ]
        default_sort = st.session_state.get("_sort_by", sort_opts[0])
        if default_sort not in sort_opts:
            default_sort = sort_opts[0]
        sort_by = st.selectbox("Ordenar por", sort_opts, index=sort_opts.index(default_sort))
        st.session_state["_sort_by"] = sort_by

    with f6:
        st.write("")
        st.write("")
        if st.button("↺ Reset", use_container_width=True):
            st.session_state["_sem_filtro"] = "Todos"
            st.session_state["_f_q"] = ""
            st.session_state["_status_filtro"] = "Todos"
            st.session_state["_tipo_filtro"] = "Todos"
            st.session_state["_sort_by"] = "Urgencia (Próx. Venc)"
            st.session_state["_vence60_filtro"] = False
            st.rerun()

    # ---------------- Aplicar filtros ----------------
    df = checklist.copy()

    if q:
        cid  = df.get("client_id",   pd.Series(dtype=str)).astype(str).str.upper()
        name = df.get("client_name", pd.Series(dtype=str)).astype(str).str.upper()
        df   = df[cid.str.contains(q) | name.str.contains(q)]

    if sem_chip != "Todos":
        if "Sin ops" in sem_chip:
            df = df[df.get("active_ops_count", "0").astype(str) == "0"]
        else:
            sem_val = sem_chip.split()[1].upper()
            df = df[df.get("semaforo", "").astype(str).str.upper() == sem_val]

    if estatus != "Todos":
        df = df[df.get("client_status", "").astype(str).str.upper() == estatus]

    if tipo != "Todos":
        df = df[df.get("tipo_persona", "").astype(str).str.upper() == tipo]

    # Filtro ⏳ Vence ≤60d: solo clientes cuya próxima op vence entre hoy y 60 días
    if st.session_state.get("_vence60_filtro", False):
        df["_nd_temp"] = pd.to_datetime(df.get("next_due", ""), errors="coerce")
        df = df[(df["_nd_temp"] >= _hoy) & (df["_nd_temp"] <= _d60)]
        df = df.drop(columns=["_nd_temp"], errors="ignore")
        if not df.empty:
            st.info(f"⏳ Mostrando {len(df)} cliente(s) con ops que vencen en los próximos 60 días.")

    # ---------------- Ordenamiento ----------------
    if sort_by == "Nombre (A-Z)":
        df = df.sort_values(by="client_name", ascending=True)
    elif sort_by == "Avance (Mayor %)":
        df["pct_num"] = pd.to_numeric(df.get("pct_ok", "0"), errors="coerce").fillna(0)
        df["pct_num"] = df["pct_num"].apply(_safe_pct)
        df = df.sort_values(by="pct_num", ascending=False)
    elif sort_by == "Avance (Menor %)":
        df["pct_num"] = pd.to_numeric(df.get("pct_ok", "0"), errors="coerce").fillna(0)
        df["pct_num"] = df["pct_num"].apply(_safe_pct)
        df = df.sort_values(by="pct_num", ascending=True)
    elif sort_by == "Más anomalías":
        df["anom_num"] = pd.to_numeric(df.get("anomalies_count", "0"), errors="coerce").fillna(0)
        df = df.sort_values(by="anom_num", ascending=False)
    else:
        df["date_temp"] = pd.to_datetime(df.get("next_due", ""), errors="coerce")
        df = df.sort_values(by=["date_temp"], ascending=True, na_position="last")

    # Si no hay resultados, no “pantalla en blanco”
    if df.empty:
        st.divider()
        st.warning("No hay resultados con esos filtros.")
        if st.button("↺ Reset filtros", type="primary"):
            st.session_state["_sem_filtro"] = "Todos"
            st.session_state["_f_q"] = ""
            st.session_state["_status_filtro"] = "Todos"
            st.session_state["_tipo_filtro"] = "Todos"
            st.session_state["_sort_by"] = "Urgencia (Próx. Venc)"
            st.session_state["_vence60_filtro"] = False
            st.rerun()
        return

    # Auto-open
    focus_client = str(st.session_state.get("_docdash_focus_client", "") or "").strip()
    if focus_client:
        st.session_state[OPEN_KEY] = focus_client

    open_client = str(st.session_state.get(OPEN_KEY, "") or "").strip()
    if open_client and open_client not in set(df.get("client_id", pd.Series(dtype=str)).astype(str).tolist()):
        st.session_state[OPEN_KEY] = ""
        open_client = ""

    # ---------------- Tabla estilo tarjetas ----------------
    st.divider()

    hdr = st.columns([1, 3, 1, 1.2, 1.2, 1.5, 1, 1.2, 1, 0.7], gap="small")
    for col, lab in zip(hdr, ["ID", "CLIENTE", "TIPO", "ESTATUS", "SEMÁFORO", "AVANCE", "OPS", "PRÓX. VENC.", "ANOM.", ""]):
        col.caption(f"**{lab}**")

    for _, row in df.iterrows():
        client_id = str(row.get("client_id", "")).strip()
        if not client_id:
            continue

        sem_val = str(row.get("semaforo", "")).strip().upper()
        pct = _safe_pct(row.get("pct_ok", 0.0))
        next_due = str(row.get("next_due", "")).strip()
        ops_active = str(row.get("active_ops_count", "0")).strip()
        anom_count = str(row.get("anomalies_count", "0")).strip()
        tipo_pers = str(row.get("tipo_persona", "")).strip().upper()
        est = str(row.get("client_status", "")).strip().upper()

        is_open = (open_client == client_id)

        sem_emoji = {"ROJO": "🔴", "AMARILLO": "🟡", "VERDE": "🟢", "COMPLETADO": "🔵", "GRIS": "⚪"}.get(sem_val, "⚪")
        sem_format = {
            "ROJO": "`🔴` :red[**ROJO**]",
            "AMARILLO": "`🟡` :orange[**AMARILLO**]",
            "VERDE": "`🟢` :green[**VERDE**]",
            "COMPLETADO": "`🔵` :blue[**COMPLETADO**]",
            "GRIS": "`⚪` **GRIS**",
        }.get(sem_val, f"`⚪` {sem_val or '--'}")

        with st.container(border=True):
            cols = st.columns([1, 3, 1, 1.2, 1.2, 1.5, 1, 1.2, 1, 0.7], gap="small")

            cols[0].write(f"**{client_id}**")

            cols[1].button(
                str(row.get("client_name", "")).strip() or "--",
                key=f"btn_name_{client_id}",
                use_container_width=True,
                on_click=_toggle_open_client,
                args=(client_id,),
            )
            if anom_count != "0":
                cols[1].caption(f"⚠️ :orange[{anom_count} anomalías]")

            cols[2].markdown(f"`{tipo_pers or '--'}`")

            est_dot = "🟢" if est == "ACTIVE" else ("⚪" if est == "INACTIVE" else "⚫")
            cols[3].markdown(f"{est_dot} {est or '--'}")

            cols[4].markdown(sem_format)

            with cols[5]:
                v = max(0.0, min(1.0, float(pct) / 100.0))
                st.progress(v)
                pct_color = "red" if pct < 50 else ("orange" if pct < 90 else "green")
                st.markdown(f":{pct_color}[**{pct:.1f}%**]")

            cols[6].write(f"**{ops_active}** act.")

            if next_due:
                cols[7].markdown(f"🚨 :red[{next_due}]")
            else:
                cols[7].write("--")

            if anom_count != "0":
                cols[8].markdown(f"`{anom_count}`")
            else:
                cols[8].write("--")

            cols[9].button(
                "➖" if is_open else "➕",
                key=f"btn_plus_{client_id}",
                use_container_width=True,
                on_click=_toggle_open_client,
                args=(client_id,),
            )

        if is_open:
            with st.container(border=True):
                top = st.columns([3, 1], gap="small")
                top[0].subheader(f"Detalle · {client_id}")
                with top[1]:
                    st.button(
                        "Cerrar",
                        key=f"close_{client_id}",
                        use_container_width=True,
                        on_click=_toggle_open_client,
                        args=(client_id,),
                    )

                a, b, c = st.columns([1.2, 2.3, 2.0], gap="large")

                with a:
                    st.subheader("Resumen")
                    st.write(f"Semáforo: **{sem_emoji} {sem_val or '--'}**")
                    st.write(f"Ops activas: **{ops_active}**")
                    st.write(f"Próx. vence: **{next_due or '--'}**")
                    st.write(f"Anomalías: **{anom_count or '0'}**")

                    st.divider()
                    st.button(
                        "✏️ Abrir en Gestión",
                        key=f"to_gestion_client_{client_id}",
                        use_container_width=True,
                        on_click=_go_gestion_client,
                        args=(client_id, "Clientes"),
                    )

                    st.divider()
                    st.subheader("Nota")
                    key = _note_key(client_id)
                    existing = (notes.get(key, {}) or {}).get("text", "")
                    txt = st.text_area(
                        "Nota del cliente",
                        value=existing,
                        height=120,
                        label_visibility="collapsed",
                        key=f"note_txt_{client_id}",
                    )
                    if st.button("Guardar nota", key=f"save_note_{client_id}", type="primary"):
                        ok, msg = _save_note(paths["NOTES_JSON"], client_id, txt)
                        (st.success if ok else st.error)(msg)
                        st.cache_data.clear()
                        st.rerun()

                    st.divider()
                    st.button(
                        "📂 Abrir carpeta cliente",
                        key=f"open_folder_{client_id}",
                        use_container_width=True,
                        on_click=lambda c_id=client_id: open_in_os(_client_folder(paths, c_id)),
                    )

                with b:
                    st.subheader("Faltantes (chips)")

                    p1 = _parse_list(str(row.get("missing_p1", "") or ""))
                    p2 = _parse_list(str(row.get("missing_p2", "") or ""))
                    p3 = _parse_list(str(row.get("missing_p3", "") or ""))
                    p4 = _parse_list(str(row.get("missing_p4", "") or row.get("p4_missing", "") or ""))

                    st.caption("P1 (crítico)")
                    _chip_grid([(label_for(k), f"{client_id}|{k}") for k in p1], cols=5)

                    st.caption("P2")
                    _chip_grid([(label_for(k), f"{client_id}|{k}") for k in p2], cols=5)

                    st.caption("P3")
                    _chip_grid([(label_for(k), f"{client_id}|{k}") for k in p3], cols=5)

                    st.caption("P4")
                    _chip_grid([(label_for(k), f"{client_id}|{k}") for k in p4], cols=5)

                with c:
                    st.subheader("Operaciones")
                    ops_c = ops[ops["client_id"] == client_id].copy() if not ops.empty else pd.DataFrame()

                    if ops_c.empty:
                        st.caption("Sin operaciones en Output.")
                    else:
                        next_due_str = next_due.strip()
                        if "vence" in ops_c.columns:
                            ops_c["⏳"] = ops_c["vence"].astype(str).str.strip().apply(lambda v: "⏳" if v == next_due_str and v else "")
                        else:
                            ops_c["⏳"] = ""

                        show = pd.DataFrame(
                            {
                                "⏳": ops_c.get("⏳", pd.Series([""] * len(ops_c))),
                                "OP": ops_c.get("op_id", pd.Series([""] * len(ops_c))),
                                "FIRMA": ops_c.get("firma", pd.Series([""] * len(ops_c))),
                                "VENCE": ops_c.get("vence", pd.Series([""] * len(ops_c))),
                                "ACT": ops_c.get("active", pd.Series([""] * len(ops_c))).apply(lambda v: "✅" if _boolish(v) else "--"),
                                "PAGARÉ": ops_c.get("has_pagare", pd.Series([""] * len(ops_c))).apply(_mark),
                                "MUTUO": ops_c.get("has_mutuo", pd.Series([""] * len(ops_c))).apply(_mark),
                                "GAR": ops_c.get("has_garantia", pd.Series([""] * len(ops_c))).apply(_mark),
                                "MED": ops_c.get("has_conv_mediacion", pd.Series([""] * len(ops_c))).apply(_mark),
                                "MOD": ops_c.get("has_conv_modif", pd.Series([""] * len(ops_c))).apply(_mark),
                                "REC": ops_c.get("has_recibo", pd.Series([""] * len(ops_c))).apply(_mark),
                                "REC_F": ops_c.get("has_recibo_firmado", pd.Series([""] * len(ops_c))).apply(_mark),
                            }
                        )
                        st.dataframe(show, use_container_width=True, hide_index=True)

                        st.caption("Atajos a Gestión (editar operación)")
                        if "op_folder" in ops_c.columns:
                            for _, op in ops_c.iterrows():
                                op_folder = str(op.get("op_folder", "")).strip()
                                op_id = str(op.get("op_id", "")).strip() or op_folder
                                if not op_folder:
                                    continue
                                st.button(
                                    f"✏️ Editar {op_id}",
                                    key=f"to_gestion_op_{client_id}_{op_folder}",
                                    use_container_width=True,
                                    on_click=_go_gestion_op,
                                    args=(client_id, op_folder),
                                )
                        else:
                            st.caption("Nota: operations.csv no trae op_folder, no puedo deep-link a una OP específica.")

                        st.divider()
                        st.caption("Chips de docs faltantes por operación")
                        for _, op in ops_c.iterrows():
                            op_folder = str(op.get("op_folder", "")).strip()
                            if not op_folder:
                                continue
                            mp1 = _parse_list(str(op.get("missing_p1", "") or ""))
                            mp2 = _parse_list(str(op.get("missing_p2", "") or ""))
                            chips = []
                            for k in mp1 + mp2:
                                lab = DOC_RULES[k].label if k in DOC_RULES else k
                                chips.append((f"{op.get('op_id','')} · {lab}", f"{client_id}|OP::{op_folder}::{k}"))
                            _chip_grid(chips, cols=4)

                    st.divider()
                    st.subheader("Anomalías")
                    an = anoms[anoms["client_id"] == client_id].copy() if not anoms.empty else pd.DataFrame()
                    if an.empty:
                        st.caption("Sin anomalías.")
                    else:
                        st.dataframe(an, use_container_width=True, hide_index=True)

        st.write("")