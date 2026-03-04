"""views/gestion.py — Gestión (CRUD clientes/partes/ops + administrar documentos).

Navegación desde Dashboard
--------------------------
El Dashboard puede hacer deep-link a cualquier sección/cliente/operación
escribiendo en st.session_state antes del rerun:
    _nav_gestion_section  → "Clientes" | "Partes relacionadas" | "Operaciones"
    _nav_gestion_client   → client_id
    _nav_gestion_op       → op_folder_name   (implica sección Operaciones + modo Editar)

Estos valores se consumen con .pop() al inicio del render, se aplican al
session_state de los widgets, y Streamlit los usa como valor inicial en ese
mismo render — sin reruns extra.

Botón "← Dashboard"
---------------------
Se muestra solo cuando vinimos desde el Dashboard (_gestion_from_dash=True).
Solo navega cuando el usuario lo pica explícitamente; nunca hace redirect
automático. No asignamos `page_nav_widget` porque ese key ya fue instanciado
por app.py en el mismo render — app.py lo limpia solo cuando detecta que
PAGE_STATE cambió.

Fases implementadas
-------------------
Fase 4  — Botón "➡️ Operaciones" en Editar cliente → navega a Operaciones del mismo cliente
Fase 5  — PF no puede tener REP_LEGAL; solo muestra AVAL para clientes PF
Fase 6  — Crear cliente → selecciona automáticamente al cliente nuevo para editarlo
Fase 7  — Crear operación → cambia a modo Editar y selecciona la op recién creada
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from core.actions import (
    load_clientes,
    load_partes,
    create_client,
    update_client,
    delete_client,
    add_party,
    update_party,
    create_garantia,
    create_operation,
    update_operation,
    open_in_os,
    read_op_meta,
    save_file_for_rule,
    delete_doc_safe,
    party_label,
    add_parte_to_op,
    remove_parte_from_op,
    get_ops_for_party,
    get_garantias_for_client,
)

# Key compartido con app.py para disparar re-scan automático
SCAN_FLAG_KEY = "_docdash_needs_scan"


def _mark_needs_scan() -> None:
    """Señala a app.py que debe correr scan() en el próximo render."""
    st.session_state[SCAN_FLAG_KEY] = True


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — etiquetas de clientes
# ─────────────────────────────────────────────────────────────────────────────

def _clean(s: object) -> str:
    return str(s or "").strip()

def _client_best_name(row: dict) -> str:
    for k in ("display_name", "legal_name", "folder_name"):
        v = _clean(row.get(k, ""))
        if v:
            return v
    return ""

def _client_label_map(clientes: pd.DataFrame) -> dict[str, str]:
    mp: dict[str, str] = {}
    if clientes is None or clientes.empty:
        return mp
    for c in ["client_id", "display_name", "legal_name", "folder_name"]:
        if c not in clientes.columns:
            clientes[c] = ""
    for _, r in clientes.iterrows():
        cid = _clean(r.get("client_id", ""))
        if not cid:
            continue
        name = _client_best_name(r.to_dict())
        mp[cid] = f"{cid} - {name}" if name else cid
    return mp

def _fmt_client(label_map: dict[str, str]):
    def _f(x: object) -> str:
        xs = _clean(x)
        return "--" if xs in {"--", ""} else label_map.get(xs, xs)
    return _f


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — SI/NO/DESCONOCIDO
# ─────────────────────────────────────────────────────────────────────────────

_YND = ["DESCONOCIDO", "SI", "NO"]

def _norm_ynd(v: object) -> str:
    s = _clean(v).upper()
    if s in {"SÍ", "SI"}:
        return "SI"
    if s == "NO":
        return "NO"
    return "DESCONOCIDO"

def _ynd_index(v: object) -> int:
    s = _norm_ynd(v)
    return _YND.index(s) if s in _YND else 0


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — carpetas y documentos
# ─────────────────────────────────────────────────────────────────────────────

def _client_folder(paths: dict, client_id: str) -> Path:
    clientes = load_clientes(paths["CLIENTES_MASTER"])
    row = clientes[clientes["client_id"] == client_id]
    if row.empty:
        return paths["DATA_DIR"] / "_MISSING_"
    return paths["DATA_DIR"] / row.iloc[0]["folder_name"]

def _list_docs_in_dir(dir_path: Path) -> list[Path]:
    if not dir_path.exists():
        return []
    return sorted(
        [p for p in dir_path.iterdir() if p.is_file() and p.name != "OP_META.json"],
        key=lambda x: x.name.lower(),
    )

def _section_header(title: str, subtitle: str = "") -> None:
    st.subheader(title)
    if subtitle:
        st.caption(subtitle)
    st.divider()

def _next_op_id(ops_root: Path) -> str:
    """Sugiere el siguiente OP ID (max existente + 1)."""
    max_n = 0
    if ops_root.exists():
        for d in ops_root.iterdir():
            if not d.is_dir():
                continue
            m = re.search(r"OP(\d{3})", d.name.upper())
            if m:
                max_n = max(max_n, int(m.group(1)))
                continue
            meta_p = d / "OP_META.json"
            if meta_p.exists():
                try:
                    meta = json.loads(meta_p.read_text(encoding="utf-8"))
                    mid  = str(meta.get("op_id", "")).strip().upper()
                    m2   = re.match(r"^OP(\d{3})$", mid)
                    if m2:
                        max_n = max(max_n, int(m2.group(1)))
                except Exception:
                    pass
    return f"OP{max_n + 1:03d}" if max_n > 0 else "OP001"

def _existing_op_ids(ops_root: Path) -> set[str]:
    """Retorna el conjunto de op_ids ya usados en una carpeta de operaciones."""
    out: set[str] = set()
    if not ops_root.exists():
        return out
    for d in ops_root.iterdir():
        if not d.is_dir():
            continue
        m = re.search(r"OP(\d{3})", d.name.upper())
        if m:
            out.add(f"OP{m.group(1)}")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Helper — Fase 9: reusar garantía de op anterior y/o aval de _PARTES
# ─────────────────────────────────────────────────────────────────────────────

def _garantia_docs_in_op(op_folder: Path) -> list[Path]:
    """Archivos de Garantía en una carpeta de operación (los únicos reutilizables entre ops)."""
    if not op_folder.exists():
        return []
    return sorted(
        [p for p in op_folder.iterdir()
         if p.is_file() and p.name.upper().startswith("GARANTIA__")],
        key=lambda x: x.name.lower(),
    )

def _aval_folders(client_folder: Path) -> list[Path]:
    """Carpetas de avales en _PARTES del cliente."""
    partes_root = client_folder / "_PARTES"
    if not partes_root.exists():
        return []
    return sorted(
        [d for d in partes_root.iterdir() if d.is_dir() and d.name.upper().startswith("AVAL__")],
        key=lambda x: x.name.lower(),
    )

def _docs_in_folder(folder: Path) -> list[Path]:
    """Archivos copiables en una carpeta de parte (excluye OP_META.json)."""
    if not folder.exists():
        return []
    return sorted(
        [p for p in folder.iterdir() if p.is_file() and p.name != "OP_META.json"],
        key=lambda x: x.name.lower(),
    )

def _copy_garantia_to_new_op(
    src_op_folder: Path,
    dst_op_folder: Path,
    filenames: list[str],
    new_op_id: str,
    new_firma: str,
    new_vence: str,
) -> list[str]:
    """
    Copia archivos de Garantía desde src_op_folder a dst_op_folder,
    actualizando el op_id y las fechas en el nombre del archivo.
    Retorna lista de mensajes de resultado.
    """
    logs: list[str] = []
    for fname in filenames:
        src = src_op_folder / fname
        if not src.exists():
            logs.append(f"⚠️ No encontré {fname} en origen.")
            continue
        new_name = re.sub(r"OP\d{3}", new_op_id, fname)
        new_name = re.sub(r"FIRMA[\d\-]+", f"FIRMA{new_firma}", new_name, flags=re.IGNORECASE)
        new_name = re.sub(r"VENCE[\d\-]+", f"VENCE{new_vence}", new_name, flags=re.IGNORECASE)
        dst = dst_op_folder / new_name
        try:
            shutil.copy2(str(src), str(dst))
            logs.append(f"✅ Garantía copiada: {new_name}")
        except Exception as e:
            logs.append(f"⚠️ Error copiando {fname}: {e}")
    return logs

def _copy_aval_docs(
    src_aval_folder: Path,
    dst_aval_folder: Path,
    filenames: list[str],
) -> list[str]:
    """
    Copia documentos de aval desde src a dst (nombres sin cambio — son de persona, no de op).
    Si dst no existe, lo crea.
    Retorna lista de mensajes de resultado.
    """
    logs: list[str] = []
    dst_aval_folder.mkdir(parents=True, exist_ok=True)
    for fname in filenames:
        src = src_aval_folder / fname
        if not src.exists():
            logs.append(f"⚠️ No encontré {fname}.")
            continue
        dst = dst_aval_folder / fname
        try:
            shutil.copy2(str(src), str(dst))
            logs.append(f"✅ Doc de aval copiado: {fname}")
        except Exception as e:
            logs.append(f"⚠️ Error copiando {fname}: {e}")
    return logs


# ─────────────────────────────────────────────────────────────────────────────
# Helper — navegación interna (Fase 4)
# ─────────────────────────────────────────────────────────────────────────────

def _nav_to_ops(client_id: str) -> None:
    """
    Solicita navegar a la sección Operaciones con el cliente dado.
    Escribe en _pending_nav (key no-widget) para que el TOP del siguiente
    render aplique los valores ANTES de que los widgets sean instanciados.
    Llamar antes de st.rerun().
    """
    st.session_state["_pending_nav"] = {
        "gestion_section":    "Operaciones",
        "gestion_client_sel": client_id,
        "gestion_op_mode":    "➕ Crear operación",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Render principal
# ─────────────────────────────────────────────────────────────────────────────

def render(paths: dict) -> None:
    st.title("🗂️ Gestión")

    # ── Pending navigation (aplicado antes de crear cualquier widget) ────────
    # Algunos botones necesitan cambiar keys de widgets (gestion_section,
    # gestion_op_mode, gestion_op_sel). Streamlit prohíbe hacerlo en el
    # mismo render en que el widget ya fue creado. La solución:
    #   1. El botón escribe en _pending_nav (key no-widget).
    #   2. En el SIGUIENTE render, aquí arriba leemos _pending_nav y lo
    #      aplicamos a los widget-keys ANTES de que se creen los widgets.
    if "_pending_nav" in st.session_state:
        for k, v in st.session_state.pop("_pending_nav").items():
            st.session_state[k] = v

    # ── Deep-link desde Dashboard ────────────────────────────────────────────
    # Leemos con .pop() para que los valores no persistan en reruns siguientes.
    # Se aplican ANTES de crear cualquier widget: Streamlit los usa como
    # valor inicial sin necesitar un rerun extra.
    nav_section = st.session_state.pop("_nav_gestion_section", None)
    nav_client  = st.session_state.pop("_nav_gestion_client",  None)
    nav_op      = st.session_state.pop("_nav_gestion_op",      None)

    if nav_section or nav_client or nav_op:
        st.session_state["_gestion_from_dash"] = True

    if nav_section in ["Clientes", "Partes relacionadas", "Operaciones"]:
        st.session_state["gestion_section"] = nav_section
    if nav_client:
        st.session_state["gestion_client_sel"] = nav_client
    if nav_op:
        st.session_state["gestion_op_mode"] = "✏️ Editar operación"
        st.session_state["gestion_op_sel"]   = nav_op

    # ── Botón "← Dashboard" ─────────────────────────────────────────────────
    # Visible solo si llegamos desde Dashboard.
    # Usamos on_click callback: Streamlit garantiza que solo corre cuando
    # el usuario presiona ESE botón específico, nunca en otros reruns.
    # `page_nav` NO es un widget key, se puede asignar en cualquier momento.
    def _cb_back_to_dashboard() -> None:
        st.session_state.pop("_gestion_from_dash", None)
        st.session_state["page_nav"] = "📊 Dashboard"

    if st.session_state.get("_gestion_from_dash", False):
        col_back, _ = st.columns([1, 5])
        col_back.button("← Dashboard", key="_btn_back_dash", on_click=_cb_back_to_dashboard)
        st.divider()

    # ── Selector de sección ──────────────────────────────────────────────────
    sections = ["Clientes", "Partes relacionadas", "Operaciones"]
    if "gestion_section" not in st.session_state:
        st.session_state["gestion_section"] = sections[0]

    section = st.radio(
        "Sección",
        sections,
        key="gestion_section",
        horizontal=True,
        label_visibility="collapsed",
    )
    st.divider()

    # ── Clientes base (compartido entre secciones) ───────────────────────────
    clientes  = load_clientes(paths["CLIENTES_MASTER"]).fillna("")
    ids       = [c for c in clientes.get("client_id", pd.Series(dtype=str)).astype(str).tolist() if str(c).strip()]
    label_map = _client_label_map(clientes)
    fmt       = _fmt_client(label_map)

    if "gestion_client_sel" not in st.session_state:
        st.session_state["gestion_client_sel"] = "--"

    # =========================================================================
    # SECCIÓN: CLIENTES
    # =========================================================================
    if section == "Clientes":
        _section_header("Clientes", "Crear / editar / eliminar. Los campos de texto se guardan en clientes_master.csv.")

        c1, c2 = st.columns([1.2, 1.8], gap="large")

        # ── Crear cliente ────────────────────────────────────────────────────
        with c1:
            st.markdown("### Crear cliente")
            with st.form("create_client_form", clear_on_submit=False):
                display_name = st.text_input("Nombre display", value="")
                legal_name   = st.text_input("Nombre legal",   value="")
                tipo_persona = st.selectbox("Tipo persona", ["PF", "PM"], index=0)
                email        = st.text_input("Email",       value="")
                phone        = st.text_input("Teléfono",    value="")
                rfc          = st.text_input("RFC",         value="")
                curp         = st.text_input("CURP",        value="", disabled=(tipo_persona == "PM"))
                submit       = st.form_submit_button("Crear", type="primary")

            if submit:
                ok, msg, new_id = create_client(
                    paths, display_name, legal_name, tipo_persona, email, phone, rfc, curp
                )
                (st.success if ok else st.error)(msg)
                if ok:
                    _mark_needs_scan()
                    st.cache_data.clear()
                    # FASE 6: Abrir directo el formulario de edición del cliente recién creado
                    if new_id:
                        st.session_state["gestion_client_sel"] = new_id
                    st.rerun()

        # ── Editar cliente ───────────────────────────────────────────────────
        with c2:
            st.markdown("### Editar cliente")
            sel = st.selectbox("Cliente", ["--"] + ids, key="gestion_client_sel", format_func=fmt)

            if sel != "--":
                row = clientes[clientes["client_id"] == sel].iloc[0].to_dict()

                with st.form("edit_client_form"):
                    display_name2 = st.text_input("Nombre display", value=row.get("display_name", ""))
                    legal_name2   = st.text_input("Nombre legal",   value=row.get("legal_name",   ""))
                    tipo2  = st.selectbox(
                        "Tipo persona", ["PF", "PM"],
                        index=0 if row.get("tipo_persona", "PF") == "PF" else 1,
                    )
                    email2  = st.text_input("Email",    value=row.get("email",  ""))
                    phone2  = st.text_input("Teléfono", value=row.get("phone",  ""))
                    rfc2    = st.text_input("RFC",      value=row.get("rfc",    ""))
                    curp2   = st.text_input("CURP",     value=row.get("curp",   ""), disabled=(tipo2 == "PM"))
                    status2 = st.selectbox(
                        "Estatus", ["ACTIVE", "INACTIVE"],
                        index=0 if row.get("client_status", "ACTIVE") == "ACTIVE" else 1,
                    )
                    rename = st.checkbox("Renombrar carpeta para reflejar display_name", value=False)

                    # FASE 4: botón "➡️ Operaciones" junto a los otros controles
                    colA, colB, colC, colD = st.columns([1, 1, 1, 1])
                    save   = colA.form_submit_button("Guardar",        type="primary")
                    openf  = colB.form_submit_button("Abrir carpeta")
                    go_ops = colC.form_submit_button("➡️ Operaciones")
                    delete = colD.form_submit_button("Eliminar",       type="secondary")

                if save:
                    ok, msg = update_client(
                        paths, sel,
                        {
                            "display_name":  display_name2,
                            "legal_name":    legal_name2,
                            "tipo_persona":  tipo2,
                            "email":         email2,
                            "phone":         phone2,
                            "rfc":           rfc2,
                            "curp":          curp2 if tipo2 == "PF" else "",
                            "client_status": status2,
                        },
                        rename_folder=rename,
                    )
                    (st.success if ok else st.error)(msg)
                    if ok:
                        _mark_needs_scan()
                        st.cache_data.clear()
                        st.rerun()

                if openf:
                    ok, msg = open_in_os(_client_folder(paths, sel))
                    (st.success if ok else st.error)(msg)

                # FASE 4: Navegar a Operaciones del mismo cliente
                if go_ops:
                    _nav_to_ops(sel)
                    st.rerun()

                if delete:
                    ok, msg = delete_client(paths, sel)
                    (st.success if ok else st.error)(msg)
                    if ok:
                        _mark_needs_scan()
                        st.cache_data.clear()
                        st.rerun()

    # =========================================================================
    # SECCIÓN: PARTES RELACIONADAS
    # =========================================================================
    elif section == "Partes relacionadas":
        _section_header(
            "Partes relacionadas",
            "Roles: REP_LEGAL (solo PM) y AVAL (PF y PM). Se crean carpetas _PARTES/ROLE__NN__SLUG. "
            "Las garantías se gestionan desde Editar operación.",
        )

        partes = load_partes(paths["PARTES_RELACIONADAS"])
        sel    = st.selectbox("Cliente", ["--"] + ids, key="gestion_client_sel", format_func=fmt)

        if sel != "--":
            # FASE 5: Roles disponibles según tipo_persona del cliente
            client_row   = clientes[clientes["client_id"] == sel].iloc[0].to_dict() if sel in ids else {}
            tipo_cliente = _clean(client_row.get("tipo_persona", "PF")).upper()

            # REP_LEGAL es exclusivo de PM (representa a la persona moral).
            # Un cliente PF solo puede tener AVALes.
            # GARANTIA no aparece aquí — se gestiona desde Editar operación.
            roles_disponibles = ["AVAL"] if tipo_cliente == "PF" else ["REP_LEGAL", "AVAL"]

            # ── Crear parte ──────────────────────────────────────────────────
            st.markdown("### Crear parte")
            if tipo_cliente == "PF":
                st.info("ℹ️ Cliente Persona Física: solo se puede agregar AVAL. REP_LEGAL aplica únicamente a Personas Morales.")

            with st.form("add_party_form"):
                role   = st.selectbox("Role",      roles_disponibles, index=0)
                nombre = st.text_input("Nombre",   value="")
                email  = st.text_input("Email",    value="")
                phone  = st.text_input("Teléfono", value="")
                rfc    = st.text_input("RFC",      value="")
                curp   = st.text_input("CURP",     value="")
                add    = st.form_submit_button("Agregar", type="primary")

            if add:
                ok, msg = add_party(paths, sel, role, nombre, email, phone, rfc, curp)
                (st.success if ok else st.error)(msg)
                if ok:
                    _mark_needs_scan()
                    st.cache_data.clear()
                    st.rerun()

            # ── Partes existentes (excluye GARANTIA — se gestionan desde ops) ─
            st.markdown("### Partes existentes")
            df = partes[(partes["client_id"] == sel) & (partes["role"].str.upper() != "GARANTIA")].copy()
            if df.empty:
                st.caption("Sin partes.")
            else:
                for _, prow in df.iterrows():
                    role  = prow["role"]
                    pid   = prow["party_id"]
                    label = party_label(role, pid)
                    exp   = st.expander(f"{label} · {prow.get('nombre', '')}", expanded=False)
                    with exp:
                        col1, col2 = st.columns([1.2, 1.8], gap="large")

                        with col1:
                            with st.form(f"edit_party_{role}_{pid}"):
                                nombre2 = st.text_input("Nombre",   value=prow.get("nombre", ""))
                                email2  = st.text_input("Email",    value=prow.get("email",  ""))
                                phone2  = st.text_input("Teléfono", value=prow.get("phone",  ""))
                                rfc2    = st.text_input("RFC",      value=prow.get("rfc",    ""))
                                curp2   = st.text_input("CURP",     value=prow.get("curp",   ""))
                                save    = st.form_submit_button("Guardar", type="primary")
                            if save:
                                ok, msg = update_party(
                                    paths, sel, role, pid,
                                    {"nombre": nombre2, "email": email2, "phone": phone2, "rfc": rfc2, "curp": curp2},
                                )
                                (st.success if ok else st.error)(msg)
                                if ok:
                                    _mark_needs_scan()
                                    st.cache_data.clear()
                                    st.rerun()

                            # Ops en las que aparece esta parte (consulta los JSON)
                            ops_linked = get_ops_for_party(_client_folder(paths, sel), role, pid)
                            if ops_linked:
                                st.markdown("**Aparece en estas operaciones:**")
                                for op_entry in ops_linked:
                                    estado = "✅ activa" if op_entry.get("active") else "🔴 inactiva"
                                    st.caption(
                                        f"• {op_entry['op_folder']}  |  firma: {op_entry['firma']}  |  "
                                        f"vence: {op_entry['vence']}  |  {estado}"
                                    )
                            else:
                                st.caption("No está vinculada a ninguna operación aún.")

                        with col2:
                            st.caption("Documentos en carpeta de la parte (reemplazo y borrado seguro).")
                            parts_root   = _client_folder(paths, sel) / "_PARTES"
                            parte_folder = None
                            pref = f"{role}__{str(pid).zfill(2)}__".upper()
                            if parts_root.exists():
                                for d in parts_root.iterdir():
                                    if d.is_dir() and d.name.upper().startswith(pref):
                                        parte_folder = d
                                        break

                            if parte_folder is None:
                                st.error("No encuentro carpeta de la parte (estructura rota).")
                            else:
                                if st.button("📂 Abrir carpeta", key=f"open_parte_{role}_{pid}"):
                                    ok, msg = open_in_os(parte_folder)
                                    (st.success if ok else st.error)(msg)

                                st.write("Subir / reemplazar:")
                                up_csf = st.file_uploader("CSF", key=f"up_csf_{role}_{pid}")
                                if st.button("Guardar CSF", key=f"save_csf_{role}_{pid}", disabled=not up_csf):
                                    ok, msg, _ = save_file_for_rule(
                                        paths, sel, "CSF_PARTE",
                                        up_csf.name, up_csf.getbuffer(),
                                        parte_folder=parte_folder.name,
                                    )
                                    (st.success if ok else st.error)(msg)
                                    if ok:
                                        _mark_needs_scan()
                                        st.cache_data.clear()
                                        st.rerun()

                                ups_ine = st.file_uploader("INE (1 o 2 archivos)", accept_multiple_files=True, key=f"up_ine_{role}_{pid}")
                                if st.button("Guardar INE", key=f"save_ine_{role}_{pid}", disabled=not ups_ine):
                                    if len(ups_ine) == 1:
                                        ok, msg, _ = save_file_for_rule(
                                            paths, sel, "INE_FRENTE_PARTE",
                                            ups_ine[0].name, ups_ine[0].getbuffer(),
                                            parte_folder=parte_folder.name,
                                        )
                                        (st.success if ok else st.error)(msg)
                                    else:
                                        ok1, msg1, _ = save_file_for_rule(
                                            paths, sel, "INE_FRENTE_PARTE",
                                            ups_ine[0].name, ups_ine[0].getbuffer(),
                                            parte_folder=parte_folder.name,
                                        )
                                        ok2, msg2, _ = save_file_for_rule(
                                            paths, sel, "INE_REVERSO_PARTE",
                                            ups_ine[1].name, ups_ine[1].getbuffer(),
                                            parte_folder=parte_folder.name,
                                        )
                                        (st.success if ok1 and ok2 else st.error)(f"{msg1} | {msg2}")
                                    _mark_needs_scan()
                                    st.cache_data.clear()
                                    st.rerun()

                                st.write("Docs actuales:")
                                docs = _list_docs_in_dir(parte_folder)
                                if not docs:
                                    st.caption("Sin docs aún.")
                                else:
                                    for p in docs:
                                        cA, cB, cC = st.columns([3, 1, 1], gap="small")
                                        cA.write(p.name)
                                        if cB.button("Abrir",  key=f"open_{p}"):
                                            ok, msg = open_in_os(p)
                                            (st.success if ok else st.error)(msg)
                                        if cC.button("Borrar", key=f"trash_{p}"):
                                            ok, msg = delete_doc_safe(p)
                                            (st.success if ok else st.error)(msg)
                                            if ok:
                                                _mark_needs_scan()
                                                st.cache_data.clear()
                                                st.rerun()

    # =========================================================================
    # SECCIÓN: OPERACIONES
    # =========================================================================
    else:
        _section_header("Operaciones", "Crear/editar OP_META.json y administrar documentos de operación.")

        sel = st.selectbox("Cliente", ["--"] + ids, key="gestion_client_sel", format_func=fmt)

        if sel != "--":
            client_folder = _client_folder(paths, sel)
            ops_root      = client_folder / "_OPERACIONES"
            ops_root.mkdir(parents=True, exist_ok=True)
            ops = sorted([d.name for d in ops_root.iterdir() if d.is_dir()], key=str.lower)

            if "gestion_op_mode" not in st.session_state:
                st.session_state["gestion_op_mode"] = "➕ Crear operación"

            mode = st.radio(
                "Modo",
                ["➕ Crear operación", "✏️ Editar operación"],
                key="gestion_op_mode",
                horizontal=True,
                label_visibility="collapsed",
            )
            st.divider()

            # ── Crear operación ──────────────────────────────────────────────
            if mode == "➕ Crear operación":
                suggested_op = _next_op_id(ops_root)

                # Resetear op_id sugerido cuando cambia el cliente seleccionado
                if st.session_state.get("_create_op_client") != sel:
                    st.session_state["_create_op_client"] = sel
                    st.session_state["create_op_id"]      = suggested_op
                    st.session_state["_op_created_flag"]  = False

                if "create_op_id" not in st.session_state:
                    st.session_state["create_op_id"] = suggested_op

                # Si el op_id actual ya existe, avanzar al siguiente sugerido
                existing = _existing_op_ids(ops_root)
                cur = str(st.session_state.get("create_op_id", "") or "").strip().upper()
                if (not cur) or (cur in existing):
                    st.session_state["create_op_id"] = suggested_op

                st.markdown("### Crear operación")

                # ── Formulario de metadatos ────────────────────────────────
                # Las partes (avales, garantías) se vinculan DESPUÉS de crear
                # la operación, desde el formulario de Editar operación.
                # Esto evita complejidad prematura y es más claro para el usuario.
                with st.form("create_op_form"):
                    op_id  = st.text_input("op_id (OP001)", key="create_op_id")
                    firma  = st.date_input("Firma", value=date.today())
                    vence  = st.date_input("Vence", value=date.today())

                    col1, col2 = st.columns([1, 1], gap="small")
                    garantia = col1.selectbox("Garantía",               _YND, index=0)
                    conv_m   = col2.selectbox("Convenio mediación",     _YND, index=0)
                    conv_mod = col1.selectbox("Convenio modificatorio", _YND, index=0)
                    recibo   = col2.selectbox("Recibo efectivo",        _YND, index=0)

                    num_pagare    = st.text_input("Número pagaré", value="")
                    pagare_status = st.selectbox("pagare_status", ["DESCONOCIDO", "NO_HAY_DOC_FIRMADO", "FIRMADO_FISICO", "FIRMADO_DIGITAL"], index=0)
                    mutuo_status  = st.selectbox("mutuo_status",  ["DESCONOCIDO", "NO_HAY_DOC_FIRMADO", "FIRMADO_FISICO", "FIRMADO_DIGITAL"], index=0)
                    create        = st.form_submit_button("Crear operación", type="primary")

                if create:
                    ok, msg = create_operation(
                        paths, sel, op_id, str(firma), str(vence),
                        {
                            "garantia":               _norm_ynd(garantia),
                            "convenio_mediacion":     _norm_ynd(conv_m),
                            "convenio_modificatorio": _norm_ynd(conv_mod),
                            "recibo_efectivo_policy": _norm_ynd(recibo),
                            "numero_pagare":          num_pagare,
                            "pagare_status":          pagare_status,
                            "mutuo_status":           mutuo_status,
                            "active":                 True,
                        },
                    )
                    (st.success if ok else st.error)(msg)
                    if ok:
                        _mark_needs_scan()
                        st.cache_data.clear()
                        st.session_state["_op_created_flag"] = True
                        # FASE 7: Ir directo al formulario de edición de la op recién creada.
                        # Usamos _pending_nav porque gestion_op_mode ya fue instanciado
                        # como widget en este render — no se puede modificar directamente.
                        new_ops = sorted([d.name for d in ops_root.iterdir() if d.is_dir()], key=str.lower)
                        created_folder = next(
                            (n for n in new_ops if op_id.strip().upper() in n.upper()),
                            None,
                        )
                        if created_folder:
                            st.session_state["_pending_nav"] = {
                                "gestion_op_mode": "✏️ Editar operación",
                                "gestion_op_sel":  created_folder,
                            }
                        st.rerun()

            # ── Editar operación ─────────────────────────────────────────────
            else:
                st.markdown("### Editar operación")

                if "gestion_op_sel" not in st.session_state:
                    st.session_state["gestion_op_sel"] = "--"

                op_sel = st.selectbox("Operación", ["--"] + ops, key="gestion_op_sel")

                if op_sel != "--":
                    op_folder = ops_root / op_sel
                    meta      = read_op_meta(op_folder)

                    with st.form("edit_op_form"):
                        op_id2   = st.text_input("op_id",              value=meta.get("op_id",  ""))
                        firma2   = st.text_input("firma (YYYY-MM-DD)", value=meta.get("firma",  ""))
                        vence2   = st.text_input("vence (YYYY-MM-DD)", value=meta.get("vence",  ""))
                        active2  = st.checkbox("active", value=bool(meta.get("active", True)))
                        num_pag2 = st.text_input("numero_pagare",      value=str(meta.get("numero_pagare", "")))

                        col1, col2 = st.columns([1, 1], gap="small")
                        garantia2  = col1.selectbox("garantia",               _YND, index=_ynd_index(meta.get("garantia")),               key=f"edit_gar_{op_sel}")
                        convm2     = col2.selectbox("convenio_mediacion",     _YND, index=_ynd_index(meta.get("convenio_mediacion")),     key=f"edit_convm_{op_sel}")
                        convmod2   = col1.selectbox("convenio_modificatorio", _YND, index=_ynd_index(meta.get("convenio_modificatorio")), key=f"edit_convmod_{op_sel}")
                        recibo2    = col2.selectbox("recibo_efectivo_policy", _YND, index=_ynd_index(meta.get("recibo_efectivo_policy")), key=f"edit_rec_{op_sel}")
                        gar_desc2  = st.text_input(
                            "Descripción garantía",
                            value=meta.get("garantia_descripcion", ""),
                            placeholder='Ej: "Hipoteca depto Polanco" o "Aval solidario"',
                            key=f"edit_gar_desc_{op_sel}",
                        )

                        pag_opts       = ["DESCONOCIDO", "NO_HAY_DOC_FIRMADO", "FIRMADO_FISICO", "FIRMADO_DIGITAL"]
                        pagare_status2 = st.selectbox(
                            "pagare_status", pag_opts,
                            index=pag_opts.index(meta.get("pagare_status", "DESCONOCIDO"))
                            if meta.get("pagare_status", "DESCONOCIDO") in pag_opts else 0,
                        )
                        mutuo_status2  = st.selectbox(
                            "mutuo_status", pag_opts,
                            index=pag_opts.index(meta.get("mutuo_status", "DESCONOCIDO"))
                            if meta.get("mutuo_status", "DESCONOCIDO") in pag_opts else 0,
                        )

                        rename = st.checkbox("Renombrar carpeta según firma", value=False)
                        colA, colB = st.columns([1, 1])
                        save  = colA.form_submit_button("Guardar",       type="primary")
                        openf = colB.form_submit_button("Abrir carpeta")

                    if save:
                        ok, msg = update_operation(
                            paths, sel, op_sel,
                            {
                                "op_id":                  op_id2.strip().upper(),
                                "firma":                  firma2.strip(),
                                "vence":                  vence2.strip(),
                                "active":                 active2,
                                "numero_pagare":          num_pag2.strip(),
                                "garantia":               _norm_ynd(garantia2),
                                "garantia_descripcion":   gar_desc2.strip(),
                                "convenio_mediacion":     _norm_ynd(convm2),
                                "convenio_modificatorio": _norm_ynd(convmod2),
                                "recibo_efectivo_policy": _norm_ynd(recibo2),
                                "pagare_status":          pagare_status2,
                                "mutuo_status":           mutuo_status2,
                                # partes_op NO se incluye aquí: update_operation hace
                                # {**meta_existente, **updates}, así que se preserva sola.
                            },
                            rename_folder=rename,
                        )
                        (st.success if ok else st.error)(msg)
                        if ok:
                            # Feedback de renombrado automático de Pagaré/Mutuo
                            op_id_check   = op_id2.strip().upper()
                            new_firma_str = firma2.strip()
                            renamed = [
                                p.name for p in op_folder.iterdir()
                                if p.is_file() and (
                                    p.name.upper().startswith(f"PAGARE__{op_id_check}__FIRMA{new_firma_str}") or
                                    p.name.upper().startswith(f"MUTUO__{op_id_check}__FIRMA{new_firma_str}")
                                )
                            ]
                            if renamed:
                                st.info(f"📄 Archivos actualizados: {', '.join(renamed)}")
                            _mark_needs_scan()
                            st.cache_data.clear()
                            st.rerun()

                    if openf:
                        ok, msg = open_in_os(op_folder)
                        (st.success if ok else st.error)(msg)

                    # =========================================================
                    # PARTES VINCULADAS (Avales + Rep. Legales)
                    # =========================================================
                    # Todas las partes viven en partes_relacionadas.csv y sus
                    # carpetas en _PARTES/. La operación las referencia en
                    # partes_op del JSON (role + party_id + nombre snapshot).
                    # GARANTIA tiene su propia sección separada abajo.
                    st.markdown("### Partes vinculadas")
                    st.caption("Avales y representantes legales asociados a esta operación.")

                    partes_df  = load_partes(paths["PARTES_RELACIONADAS"])
                    partes_op  = meta.get("partes_op", [])
                    if not isinstance(partes_op, list):
                        partes_op = []

                    # Partes del cliente excluyendo GARANTIA (esas van abajo)
                    partes_cli = partes_df[
                        (partes_df["client_id"] == sel) &
                        (partes_df["role"].str.upper() != "GARANTIA")
                    ].copy() if not partes_df.empty else pd.DataFrame()

                    # ── Partes ya vinculadas (sin GARANTIA) ──────────────────
                    partes_op_personas = [p for p in partes_op if p.get("role","").upper() != "GARANTIA"]
                    if not partes_op_personas:
                        st.caption("Sin partes vinculadas aún.")
                    else:
                        for p in partes_op_personas:
                            role_p = p.get("role", "")
                            pid_p  = p.get("party_id", "")
                            nom_p  = p.get("nombre", "—")
                            col_lbl, col_btn = st.columns([4, 1], gap="small")
                            col_lbl.markdown(f"**{role_p}** · {party_label(role_p, pid_p)} · {nom_p}")
                            if col_btn.button("✕ Quitar", key=f"rm_parte_{op_sel}_{role_p}_{pid_p}"):
                                ok_rm, msg_rm = remove_parte_from_op(op_folder, role_p, pid_p)
                                (st.success if ok_rm else st.error)(msg_rm)
                                if ok_rm:
                                    _mark_needs_scan()
                                    st.cache_data.clear()
                                    st.rerun()

                    # ── Vincular parte existente ──────────────────────────────
                    if not partes_cli.empty:
                        st.markdown("**Vincular parte existente:**")
                        vinculadas   = {(p.get("role"), p.get("party_id")) for p in partes_op}
                        opciones_per = [
                            (prow["role"], prow["party_id"], prow.get("nombre", ""))
                            for _, prow in partes_cli.iterrows()
                            if (prow["role"], prow["party_id"]) not in vinculadas
                        ]
                        if opciones_per:
                            opts_labels = [f"{r} · {party_label(r, pid)} · {nom}" for r, pid, nom in opciones_per]
                            add_col, btn_col = st.columns([4, 1], gap="small")
                            chosen_idx = add_col.selectbox(
                                "Parte a vincular",
                                range(len(opts_labels)),
                                format_func=lambda i: opts_labels[i],
                                key=f"add_parte_sel_{op_sel}",
                                label_visibility="collapsed",
                            )
                            if btn_col.button("＋ Vincular", key=f"add_parte_btn_{op_sel}", type="primary"):
                                r_add, pid_add, nom_add = opciones_per[chosen_idx]
                                ok_add, msg_add = add_parte_to_op(op_folder, r_add, pid_add, nom_add)
                                (st.success if ok_add else st.error)(msg_add)
                                if ok_add:
                                    _mark_needs_scan()
                                    st.cache_data.clear()
                                    st.rerun()
                        else:
                            st.caption("Todas las partes de este cliente ya están vinculadas.")

                    # ── Crear nueva parte desde aquí ──────────────────────────
                    # Navega a Partes relacionadas con este cliente ya seleccionado.
                    st.caption("¿No existe la parte aún?")
                    if st.button("➕ Ir a crear parte relacionada", key=f"goto_crear_parte_{op_sel}"):
                        st.session_state["_pending_nav"] = {
                            "gestion_section":    "Partes relacionadas",
                            "gestion_client_sel": sel,
                        }
                        st.rerun()

                    st.divider()

                    # =========================================================
                    # GARANTÍAS VINCULADAS
                    # =========================================================
                    # Las garantías son activos del cliente (no personas).
                    # Se registran en partes_relacionadas.csv con role=GARANTIA
                    # y sus carpetas van en _CLIENTE/GARANTIA__NN__SLUG/.
                    # Se crean y vinculan desde aquí; no aparecen en la sección
                    # "Partes relacionadas" del menú principal.
                    st.markdown("### Garantías vinculadas")
                    st.caption("Garantías asociadas a esta operación.")

                    garantias_cli = get_garantias_for_client(paths, sel)
                    partes_op_gar = [p for p in partes_op if p.get("role","").upper() == "GARANTIA"]

                    # ── Garantías ya vinculadas ───────────────────────────────
                    if not partes_op_gar:
                        st.caption("Sin garantías vinculadas aún.")
                    else:
                        for g in partes_op_gar:
                            pid_g  = g.get("party_id", "")
                            nom_g  = g.get("nombre", "—")
                            # Buscar descripción en la lista del cliente
                            desc_g = next((x["descripcion"] for x in garantias_cli if x["party_id"] == pid_g), "")
                            col_lbl, col_btn = st.columns([4, 1], gap="small")
                            col_lbl.markdown(f"**GAR{pid_g}** · {nom_g}" + (f" — {desc_g}" if desc_g else ""))
                            if col_btn.button("✕ Quitar", key=f"rm_gar_{op_sel}_{pid_g}"):
                                ok_rm, msg_rm = remove_parte_from_op(op_folder, "GARANTIA", pid_g)
                                (st.success if ok_rm else st.error)(msg_rm)
                                if ok_rm:
                                    _mark_needs_scan()
                                    st.cache_data.clear()
                                    st.rerun()

                    # ── Vincular garantía existente ───────────────────────────
                    vinculadas_gar = {p.get("party_id") for p in partes_op_gar}
                    disponibles    = [g for g in garantias_cli if g["party_id"] not in vinculadas_gar]
                    if disponibles:
                        st.markdown("**Vincular garantía existente:**")
                        gar_labels = [f"GAR{g['party_id']} · {g['tipo']}" + (f" — {g['descripcion']}" if g['descripcion'] else "") for g in disponibles]
                        gc1, gc2 = st.columns([4, 1], gap="small")
                        gar_idx = gc1.selectbox(
                            "Garantía a vincular",
                            range(len(gar_labels)),
                            format_func=lambda i: gar_labels[i],
                            key=f"add_gar_sel_{op_sel}",
                            label_visibility="collapsed",
                        )
                        if gc2.button("＋ Vincular", key=f"add_gar_btn_{op_sel}", type="primary"):
                            g_sel   = disponibles[gar_idx]
                            ok_g, msg_g = add_parte_to_op(op_folder, "GARANTIA", g_sel["party_id"], g_sel["tipo"])
                            (st.success if ok_g else st.error)(msg_g)
                            if ok_g:
                                _mark_needs_scan()
                                st.cache_data.clear()
                                st.rerun()

                    # ── Crear nueva garantía desde aquí ──────────────────────
                    with st.expander("➕ Crear nueva garantía", expanded=False):
                        st.caption("Se registra en _CLIENTE/GARANTIA__NN__SLUG/ y queda disponible para vincular.")
                        ng_tipo = st.text_input("Tipo de garantía", placeholder='Ej: HIPOTECA, AVAL_SOLIDARIO, PRENDA', key=f"ng_tipo_{op_sel}")
                        ng_desc = st.text_input("Descripción", placeholder='Ej: "Depto Polanco escritura 1234"', key=f"ng_desc_{op_sel}")
                        ng_file = st.file_uploader("Documento (opcional)", key=f"ng_file_{op_sel}")
                        if st.button("Crear y vincular garantía", key=f"ng_save_{op_sel}", type="primary", disabled=not ng_tipo.strip()):
                            fb = ng_file.getbuffer() if ng_file else b""
                            fn = ng_file.name       if ng_file else ""
                            ok_ng, msg_ng, pid_ng = create_garantia(paths, sel, ng_tipo, ng_desc, fn, bytes(fb))
                            (st.success if ok_ng else st.error)(msg_ng)
                            if ok_ng and pid_ng:
                                ok_vg, msg_vg = add_parte_to_op(op_folder, "GARANTIA", pid_ng, ng_tipo.strip().upper())
                                if ok_vg:
                                    st.success("Garantía vinculada a esta operación.")
                                else:
                                    st.warning(f"Garantía creada pero no vinculada: {msg_vg}")
                                _mark_needs_scan()
                                st.cache_data.clear()
                                st.rerun()

                    st.divider()

                    # ── Documentos de la operación ───────────────────────────
                    st.markdown("### Documentos de operación (reemplazo / borrado seguro)")
                    st.caption("Pagaré y Mutuo requieren firma+vence. Garantía se gestiona arriba.")

                    up_pag = st.file_uploader("Pagaré", key=f"up_pag_{op_sel}")
                    if st.button("Guardar Pagaré", key=f"save_pag_{op_sel}", disabled=not up_pag):
                        meta2 = read_op_meta(op_folder)
                        ok, msg, _ = save_file_for_rule(
                            paths, sel, "PAGARE", up_pag.name, up_pag.getbuffer(),
                            op_folder_name=op_sel, op_meta=meta2,
                        )
                        (st.success if ok else st.error)(msg)
                        if ok:
                            _mark_needs_scan()
                            st.cache_data.clear()
                            st.rerun()

                    up_mut = st.file_uploader("Mutuo", key=f"up_mut_{op_sel}")
                    if st.button("Guardar Mutuo", key=f"save_mut_{op_sel}", disabled=not up_mut):
                        meta2 = read_op_meta(op_folder)
                        ok, msg, _ = save_file_for_rule(
                            paths, sel, "MUTUO", up_mut.name, up_mut.getbuffer(),
                            op_folder_name=op_sel, op_meta=meta2,
                        )
                        (st.success if ok else st.error)(msg)
                        if ok:
                            _mark_needs_scan()
                            st.cache_data.clear()
                            st.rerun()

                    st.write("Docs actuales:")
                    docs = _list_docs_in_dir(op_folder)
                    if not docs:
                        st.caption("Sin documentos aún.")
                    else:
                        for p in docs:
                            cA, cB, cC = st.columns([3, 1, 1], gap="small")
                            cA.write(p.name)
                            if cB.button("Abrir",  key=f"open_{p}"):
                                ok, msg = open_in_os(p)
                                (st.success if ok else st.error)(msg)
                            if cC.button("Borrar", key=f"trash_{p}"):
                                ok, msg = delete_doc_safe(p)
                                (st.success if ok else st.error)(msg)
                                if ok:
                                    _mark_needs_scan()
                                    st.cache_data.clear()
                                    st.rerun()
