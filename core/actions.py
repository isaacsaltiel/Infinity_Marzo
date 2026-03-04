"""core/actions.py — Lógica de negocio (sin UI) + helpers de filesystem."""
from __future__ import annotations

import os
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional, Tuple

import pandas as pd

from core.models import CLIENTES_COLS, PARTES_COLS, PAGARE_MUTUO_STATUSES
from core.rules import DOC_RULES, DocRule
from core.storage import atomic_write_csv, atomic_write_json, read_csv, read_json

# ---------------------------------------------------------------------
# Flag de "necesito scan" (lo ejecuta app.py antes de renderizar)
# ---------------------------------------------------------------------
SCAN_FLAG_KEY = "_docdash_needs_scan"

def _mark_needs_scan() -> None:
    """Marca que hay que correr core.scan.scan() en el siguiente rerun."""
    try:
        import streamlit as st
        st.session_state[SCAN_FLAG_KEY] = True
    except Exception:
        # Si estamos corriendo fuera de Streamlit (scripts/CLI), no hacemos nada.
        pass

def _extend_cols(base: list[str], extra: list[str]) -> list[str]:
    out = list(base)
    for c in extra:
        if c not in out:
            out.append(c)
    return out

# Nuevas columnas de IDs (texto) en master
ID_TEXT_COLS = ["rfc", "curp"]

CLIENTES_COLS_X = _extend_cols(CLIENTES_COLS, ID_TEXT_COLS)
PARTES_COLS_X = _extend_cols(PARTES_COLS, ID_TEXT_COLS)

def _norm_id_text(s: str) -> str:
    # Normalización ligera para guardar: mayúsculas + trim
    return str(s or "").strip().upper()

# ─────────────────────────────────────────────────────────────────────────────
# String / slug helpers
# ─────────────────────────────────────────────────────────────────────────────

def strip_accents(s: str) -> str:
    s = str(s or "")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")

def normalize_spaces(s: str) -> str:
    s = strip_accents(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def slug_upper(s: str, max_len: int = 40) -> str:
    try:
        from slugify import slugify
        slug = slugify(normalize_spaces(s), separator="_").upper()
    except Exception:
        slug = re.sub(r"[^A-Z0-9]", "_", strip_accents(s).upper())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:max_len] if max_len else slug

def truthy_flag(val: str) -> bool:
    v = str(val or "").strip().upper()
    return v in {"SI", "S", "YES", "Y", "TRUE", "1", "REQUIERE", "REQUIRED"}


# ─────────────────────────────────────────────────────────────────────────────
# CSV access
# ─────────────────────────────────────────────────────────────────────────────

def load_clientes(path: Path) -> pd.DataFrame:
    return read_csv(path, CLIENTES_COLS_X)

def load_partes(path: Path) -> pd.DataFrame:
    return read_csv(path, PARTES_COLS_X)

def write_clientes(path: Path, df: pd.DataFrame) -> Tuple[bool, str]:
    df = df.copy()
    for c in CLIENTES_COLS_X:
        if c not in df.columns:
            df[c] = ""
    return atomic_write_csv(df[CLIENTES_COLS_X], path)

def write_partes(path: Path, df: pd.DataFrame) -> Tuple[bool, str]:
    df = df.copy()
    for c in PARTES_COLS_X:
        if c not in df.columns:
            df[c] = ""
    return atomic_write_csv(df[PARTES_COLS_X], path)

# ─────────────────────────────────────────────────────────────────────────────
# IDs / folder naming
# ─────────────────────────────────────────────────────────────────────────────

def suggest_next_client_id(existing_ids: Iterable[str], id_prefix: str, start: int) -> str:
    nums = set()
    for cid in existing_ids:
        m = re.match(rf"^{re.escape(id_prefix)}(\d+)$", str(cid).strip().upper())
        if m:
            nums.add(int(m.group(1)))
    k = start
    while k in nums:
        k += 1
    return f"{id_prefix}{k}"

def make_client_folder_name(client_id: str, display_name: str) -> str:
    return f"{client_id}_{slug_upper(display_name)}"

def parse_party_index(party_id: str) -> Optional[int]:
    s = str(party_id or "").strip().upper()
    m = re.match(r"^(\d+)$", s)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)$", s)
    if m:
        return int(m.group(1))
    return None

def suggest_next_party_index(partes_df: pd.DataFrame, client_id: str, role: str) -> str:
    df = partes_df[(partes_df["client_id"] == client_id) & (partes_df["role"].str.upper() == role.upper())]
    used = set()
    for pid in df["party_id"].tolist():
        n = parse_party_index(pid)
        if n is not None:
            used.add(n)
    k = 1
    while k in used:
        k += 1
    return str(k).zfill(2)

def party_label(role: str, party_id: str) -> str:
    n = parse_party_index(party_id) or 0
    if role.upper() == "REP_LEGAL":
        return f"REP{n}"
    if role.upper() == "AVAL":
        return f"AV{n}"
    if role.upper() == "GARANTIA":
        return f"GAR{n}"
    return f"{role.upper()}-{party_id}"

def build_parte_folder(role: str, party_id: str, nombre: str) -> str:
    return f"{role.upper()}__{str(party_id).zfill(2)}__{slug_upper(nombre, max_len=30)}"

def build_op_folder(op_id: str, firma: str) -> str:
    # Firma obligatoria en carpeta (YYYY-MM-DD)
    firma = (firma or "").strip()
    if not firma:
        raise ValueError("firma es requerida para el nombre de carpeta OP__OPxxx__YYYY-MM-DD")
    return f"OP__{op_id}__{firma}"


# ─────────────────────────────────────────────────────────────────────────────
# Filesystem helpers
# ─────────────────────────────────────────────────────────────────────────────

def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

def safe_rename(src: Path, dst: Path) -> Tuple[bool, str]:
    ensure_dir(dst.parent)
    try:
        os.replace(src, dst)
        return True, "OK"
    except PermissionError as e:
        return False, f"PermissionError: {e}"
    except OSError as e:
        return False, f"OSError: {e}"

def safe_write_bytes(path: Path, data: bytes) -> None:
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)

def trash_path(root: Path, filename: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    trash_dir = root / "_TRASH" / ts
    ensure_dir(trash_dir)
    return trash_dir / filename

def move_to_trash(file_path: Path) -> Tuple[bool, str, Optional[Path]]:
    if not file_path.exists():
        return False, "No existe.", None
    dst = trash_path(file_path.parent, file_path.name)
    try:
        ensure_dir(dst.parent)
        shutil.move(str(file_path), str(dst))
        return True, "Movido a _TRASH.", dst
    except Exception as e:
        return False, f"No pude mover a _TRASH: {e}", None

def open_in_os(path: Path) -> Tuple[bool, str]:
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
            return True, "Abriendo..."
        return False, "Abrir archivo solo está soportado en Windows desde esta app."
    except Exception as e:
        return False, f"No pude abrir: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# OP_META
# ─────────────────────────────────────────────────────────────────────────────

OP_META_DEFAULTS = {
    "client_id":               "",
    "op_id":                   "",
    "numero_pagare":           "",
    "firma":                   "",
    "vence":                   "",
    "active":                  True,
    "garantia":                "DESCONOCIDO",
    "garantia_descripcion":    "",          # texto libre, ej. "Hipoteca casa Querétaro"
    "convenio_mediacion":      "DESCONOCIDO",
    "convenio_modificatorio":  "DESCONOCIDO",
    "recibo_efectivo_policy":  "DESCONOCIDO",
    "pagare_status":           "DESCONOCIDO",
    "mutuo_status":            "DESCONOCIDO",
    # Partes vinculadas a esta operación (avales, rep. legales y garantías).
    # Cada entrada: {"role": "AVAL"|"REP_LEGAL"|"GARANTIA", "party_id": "01", "nombre": "..."}
    # El nombre es snapshot del momento en que se vinculó (preserva historial legal).
    "partes_op":               [],
}

def read_op_meta(op_folder: Path) -> dict:
    return read_json(op_folder / "OP_META.json", default={})

def write_op_meta(op_folder: Path, meta: dict) -> Tuple[bool, str]:
    merged = {**OP_META_DEFAULTS, **(meta or {})}
    # Normalizar statuses conocidos
    if merged.get("pagare_status") not in PAGARE_MUTUO_STATUSES:
        merged["pagare_status"] = "DESCONOCIDO"
    if merged.get("mutuo_status") not in PAGARE_MUTUO_STATUSES:
        merged["mutuo_status"] = "DESCONOCIDO"
    # Normalizar partes_op: debe ser lista de dicts con role, party_id y nombre
    raw_partes = merged.get("partes_op", [])
    if not isinstance(raw_partes, list):
        raw_partes = []
    merged["partes_op"] = [
        {
            "role":     str(p.get("role",     "")).strip(),
            "party_id": str(p.get("party_id", "")).strip(),
            "nombre":   str(p.get("nombre",   "")).strip(),
        }
        for p in raw_partes
        if isinstance(p, dict) and p.get("role") and p.get("party_id")
    ]
    return atomic_write_json(merged, op_folder / "OP_META.json")


# ─────────────────────────────────────────────────────────────────────────────
# CRUD Cliente / Parte / Operación
# ─────────────────────────────────────────────────────────────────────────────

def create_client(
    paths: dict,
    display_name: str,
    legal_name: str,
    tipo_persona: str,
    email: str,
    phone: str,
    rfc: str = "",
    curp: str = "",
) -> Tuple[bool, str, Optional[str]]:
    clientes = load_clientes(paths["CLIENTES_MASTER"])
    client_id = suggest_next_client_id(clientes["client_id"].tolist(), paths["ID_PREFIX"], paths["CLIENT_ID_START"])
    folder_name = make_client_folder_name(client_id, display_name)
    client_folder = paths["DATA_DIR"] / folder_name

    ensure_dir(client_folder / "_CLIENTE")
    ensure_dir(client_folder / "_PARTES")
    ensure_dir(client_folder / "_OPERACIONES")
    ensure_dir(client_folder / "_INBOX")

    tipo = str(tipo_persona or "").strip().upper()

    row = {
        "client_id": client_id,
        "display_name": normalize_spaces(display_name),
        "legal_name": normalize_spaces(legal_name),
        "folder_name": folder_name,
        "tipo_persona": tipo,
        "email": str(email or "").strip(),
        "phone": str(phone or "").strip(),
        "rfc": _norm_id_text(rfc),
        # CURP solo aplica normalmente para PF, pero guardamos lo que venga
        "curp": _norm_id_text(curp),
        "created_date": str(date.today()),
        "client_status": "ACTIVE",
    }
    out = pd.concat([clientes, pd.DataFrame([row])], ignore_index=True)
    ok, msg = write_clientes(paths["CLIENTES_MASTER"], out)
    if ok:
        _mark_needs_scan()
        return True, f"Cliente creado: {client_id}", client_id
    return False, msg, None

def update_client(paths: dict, client_id: str, updates: dict, rename_folder: bool = False) -> Tuple[bool, str]:
    clientes = load_clientes(paths["CLIENTES_MASTER"])
    mask = clientes["client_id"] == client_id
    if not mask.any():
        return False, "No existe client_id."
    row = clientes.loc[mask].iloc[0].to_dict()
    old_folder = row["folder_name"]
    new_row = {**row, **updates}
    # Asegura nuevas columnas aunque el CSV viejo no las traiga todavía
    for c in CLIENTES_COLS_X:
        new_row.setdefault(c, "")
    clientes.loc[mask, :] = pd.DataFrame([new_row])[CLIENTES_COLS_X].values

    ok, msg = write_clientes(paths["CLIENTES_MASTER"], clientes)
    if not ok:
        return False, msg

    if any(k in (updates or {}) for k in ("email", "phone", "tipo_persona", "rfc", "curp")):
        _mark_needs_scan()

    if rename_folder:
        new_folder_name = make_client_folder_name(client_id, new_row.get("display_name", row["display_name"]))
        if new_folder_name != old_folder:
            src = paths["DATA_DIR"] / old_folder
            dst = paths["DATA_DIR"] / new_folder_name
            if src.exists():
                ok2, msg2 = safe_rename(src, dst)
                if ok2:
                    # actualizar folder_name en CSV
                    clientes = load_clientes(paths["CLIENTES_MASTER"])
                    clientes.loc[clientes["client_id"] == client_id, "folder_name"] = new_folder_name
                    write_clientes(paths["CLIENTES_MASTER"], clientes)
                    return True, f"Actualizado y renombrado → {new_folder_name}"
                return False, f"CSV ok, pero no pude renombrar carpeta: {msg2}"
    return True, "Actualizado."

def delete_client(paths: dict, client_id: str) -> Tuple[bool, str]:
    clientes = load_clientes(paths["CLIENTES_MASTER"])
    mask = clientes["client_id"] == client_id
    if not mask.any():
        return False, "No existe client_id."
    folder_name = clientes.loc[mask, "folder_name"].iloc[0]
    clientes = clientes.loc[~mask].copy()
    ok, msg = write_clientes(paths["CLIENTES_MASTER"], clientes)
    if not ok:
        return False, msg

    src = paths["DATA_DIR"] / folder_name
    if src.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = paths["DELETED_DIR"] / f"{folder_name}__{ts}"
        ensure_dir(paths["DELETED_DIR"])
        try:
            shutil.move(str(src), str(dst))
            return True, f"Eliminado. Carpeta movida a _DELETED ({dst.name})."
        except Exception as e:
            return False, f"Cliente eliminado del CSV, pero no pude mover carpeta: {e}"
    return True, "Eliminado del CSV (carpeta no existía)."

def add_party(
    paths: dict,
    client_id: str,
    role: str,
    nombre: str,
    email: str,
    phone: str,
    rfc: str = "",
    curp: str = "",
) -> Tuple[bool, str]:
    partes = load_partes(paths["PARTES_RELACIONADAS"])
    clientes = load_clientes(paths["CLIENTES_MASTER"])
    row = clientes[clientes["client_id"] == client_id]
    if row.empty:
        return False, "Cliente no existe."
    folder_name = row.iloc[0]["folder_name"]
    client_folder = paths["DATA_DIR"] / folder_name

    role = role.strip().upper()
    if role not in {"REP_LEGAL", "AVAL"}:
        return False, "Role inválido (solo REP_LEGAL o AVAL)."

    party_id = suggest_next_party_index(partes, client_id, role)
    parte_folder = build_parte_folder(role, party_id, nombre)
    parte_dir = client_folder / "_PARTES" / parte_folder
    ensure_dir(parte_dir / "_INBOX")

    new_row = {
        "client_id": client_id,
        "role": role,
        "party_id": party_id,
        "nombre": normalize_spaces(nombre),
        "email": str(email or "").strip(),
        "phone": str(phone or "").strip(),
        "rfc": _norm_id_text(rfc),
        "curp": _norm_id_text(curp),
    }
    out = pd.concat([partes, pd.DataFrame([new_row])], ignore_index=True)
    ok, msg = write_partes(paths["PARTES_RELACIONADAS"], out)
    if ok:
        _mark_needs_scan()
        return True, f"Parte creada: {party_label(role, party_id)}"
    return False, msg

def create_garantia(
    paths: dict,
    client_id: str,
    tipo: str,
    descripcion: str,
    file_name: str = "",
    file_bytes: bytes = b"",
) -> Tuple[bool, str, str]:
    """
    Crea una garantía para el cliente y opcionalmente guarda un documento.

    Diseño:
    - Se registra en partes_relacionadas.csv con role=GARANTIA, nombre=tipo.
    - La carpeta física va en _CLIENTE/GARANTIA__NN__SLUG/ (no en _PARTES/,
      porque es un activo del cliente, no una persona).
    - Si file_bytes se pasa, el archivo se guarda directamente en esa carpeta.

    Retorna (ok, msg, party_id).
    """
    partes   = load_partes(paths["PARTES_RELACIONADAS"])
    clientes = load_clientes(paths["CLIENTES_MASTER"])
    row      = clientes[clientes["client_id"] == client_id]
    if row.empty:
        return False, "Cliente no existe.", ""

    tipo = normalize_spaces(tipo).upper() if tipo else "SIN_TIPO"

    folder_name    = row.iloc[0]["folder_name"]
    client_folder  = paths["DATA_DIR"] / folder_name
    cliente_dir    = client_folder / "_CLIENTE"

    party_id       = suggest_next_party_index(partes, client_id, "GARANTIA")
    garantia_folder = build_parte_folder("GARANTIA", party_id, tipo)
    garantia_dir   = cliente_dir / garantia_folder
    ensure_dir(garantia_dir)

    # Guardar documento si se proporcionó
    if file_bytes and file_name:
        dest = garantia_dir / file_name
        safe_write_bytes(dest, file_bytes)

    new_row = {
        "client_id": client_id,
        "role":      "GARANTIA",
        "party_id":  party_id,
        "nombre":    tipo,
        "email":     "",
        "phone":     "",
        "rfc":       descripcion[:100] if descripcion else "",  # rfc field reutilizado para descripción
        "curp":      "",
    }
    out    = pd.concat([partes, pd.DataFrame([new_row])], ignore_index=True)
    ok, msg = write_partes(paths["PARTES_RELACIONADAS"], out)
    if ok:
        _mark_needs_scan()
        return True, f"Garantía creada: {party_label('GARANTIA', party_id)} — {tipo}", party_id
    return False, msg, ""


def update_party(paths: dict, client_id: str, role: str, party_id: str, updates: dict) -> Tuple[bool, str]:
    partes = load_partes(paths["PARTES_RELACIONADAS"])
    mask = (partes["client_id"] == client_id) & (partes["role"].str.upper() == role.upper()) & (partes["party_id"].astype(str) == str(party_id))
    if not mask.any():
        return False, "No existe esa parte."
    row = partes.loc[mask].iloc[0].to_dict()
    new_row = {**row, **updates}
    for c in PARTES_COLS_X:
        new_row.setdefault(c, "")
    partes.loc[mask, :] = pd.DataFrame([new_row])[PARTES_COLS_X].values
    ok, msg = write_partes(paths["PARTES_RELACIONADAS"], partes)
    if ok:
        if any(k in (updates or {}) for k in ("email", "phone", "rfc", "curp")):
            _mark_needs_scan()
        return True, "Parte actualizada."
    return False, msg

def add_parte_to_op(op_folder: Path, role: str, party_id: str, nombre: str) -> Tuple[bool, str]:
    """
    Vincula una parte (aval o rep. legal) a una operación.
    Si ya existe la combinación role+party_id, actualiza el nombre (snapshot).
    """
    meta = read_op_meta(op_folder)
    partes = meta.get("partes_op", [])
    if not isinstance(partes, list):
        partes = []

    # Buscar si ya existe para actualizar en lugar de duplicar
    for p in partes:
        if p.get("role") == role and p.get("party_id") == party_id:
            p["nombre"] = nombre
            meta["partes_op"] = partes
            return write_op_meta(op_folder, meta)

    partes.append({"role": role, "party_id": party_id, "nombre": nombre})
    meta["partes_op"] = partes
    return write_op_meta(op_folder, meta)


def remove_parte_from_op(op_folder: Path, role: str, party_id: str) -> Tuple[bool, str]:
    """Desvincula una parte de una operación."""
    meta = read_op_meta(op_folder)
    partes = meta.get("partes_op", [])
    if not isinstance(partes, list):
        partes = []

    meta["partes_op"] = [
        p for p in partes
        if not (p.get("role") == role and p.get("party_id") == party_id)
    ]
    return write_op_meta(op_folder, meta)


def get_ops_for_party(client_folder: Path, role: str, party_id: str) -> list[dict]:
    """
    Retorna lista de ops en las que aparece una parte (role + party_id).
    Cada entrada: {"op_folder": str, "op_id": str, "firma": str, "vence": str}
    Útil para mostrar en Gestión → Partes relacionadas → "aparece en estas ops".
    """
    ops_root = client_folder / "_OPERACIONES"
    if not ops_root.exists():
        return []

    result = []
    for opf in ops_root.iterdir():
        if not opf.is_dir():
            continue
        meta = read_op_meta(opf)
        for p in meta.get("partes_op", []):
            if p.get("role") == role and p.get("party_id") == party_id:
                result.append({
                    "op_folder": opf.name,
                    "op_id":     meta.get("op_id", ""),
                    "firma":     meta.get("firma", ""),
                    "vence":     meta.get("vence", ""),
                    "active":    meta.get("active", True),
                })
                break
    return sorted(result, key=lambda x: x["firma"], reverse=True)


def get_garantias_for_client(paths: dict, client_id: str) -> list[dict]:
    """
    Retorna todas las garantías registradas para un cliente.
    Cada entrada: {"party_id": str, "tipo": str, "descripcion": str, "folder": Path|None}
    """
    partes = load_partes(paths["PARTES_RELACIONADAS"])
    df     = partes[(partes["client_id"] == client_id) & (partes["role"].str.upper() == "GARANTIA")]
    if df.empty:
        return []

    clientes      = load_clientes(paths["CLIENTES_MASTER"])
    row           = clientes[clientes["client_id"] == client_id]
    client_folder = paths["DATA_DIR"] / row.iloc[0]["folder_name"] if not row.empty else None
    cliente_dir   = client_folder / "_CLIENTE" if client_folder else None

    result = []
    for _, prow in df.iterrows():
        pid  = str(prow["party_id"])
        tipo = str(prow.get("nombre", ""))
        desc = str(prow.get("rfc",    ""))  # descripcion guardada en campo rfc
        # Buscar carpeta física
        folder = None
        if cliente_dir and cliente_dir.exists():
            pref = f"GARANTIA__{str(pid).zfill(2)}__"
            for d in cliente_dir.iterdir():
                if d.is_dir() and d.name.upper().startswith(pref.upper()):
                    folder = d
                    break
        result.append({"party_id": pid, "tipo": tipo, "descripcion": desc, "folder": folder})
    return result



    clientes = load_clientes(paths["CLIENTES_MASTER"])
    row = clientes[clientes["client_id"] == client_id]
    if row.empty:
        return False, "Cliente no existe."
    folder_name = row.iloc[0]["folder_name"]
    client_folder = paths["DATA_DIR"] / folder_name

    op_id = op_id.strip().upper()
    if not re.match(r"^OP\d{3}$", op_id):
        return False, "op_id debe ser tipo OP001, OP002..."
    if not firma:
        return False, "firma (YYYY-MM-DD) es requerida."
    op_folder_name = build_op_folder(op_id, firma)
    op_folder = client_folder / "_OPERACIONES" / op_folder_name
    if op_folder.exists():
        return False, f"Ya existe {op_folder_name}."

    ensure_dir(op_folder / "_INBOX")
    meta = {**OP_META_DEFAULTS, **(meta_updates or {})}
    meta.update({"client_id": client_id, "op_id": op_id, "firma": firma, "vence": vence})
    ok, msg = write_op_meta(op_folder, meta)
    if ok:
        return True, f"Operación creada: {op_folder_name}"
    return False, msg

def create_operation(paths: dict, client_id: str, op_id: str, firma: str, vence: str, meta_updates: dict) -> Tuple[bool, str]:
    clientes = load_clientes(paths["CLIENTES_MASTER"])
    row = clientes[clientes["client_id"] == client_id]
    if row.empty:
        return False, "Cliente no existe."
    folder_name   = row.iloc[0]["folder_name"]
    client_folder = paths["DATA_DIR"] / folder_name

    op_id = op_id.strip().upper()
    if not re.match(r"^OP\d{3}$", op_id):
        return False, "op_id debe ser tipo OP001, OP002..."
    if not firma:
        return False, "firma (YYYY-MM-DD) es requerida."
    op_folder_name = build_op_folder(op_id, firma)
    op_folder = client_folder / "_OPERACIONES" / op_folder_name
    if op_folder.exists():
        return False, f"Ya existe {op_folder_name}."

    ensure_dir(op_folder / "_INBOX")
    meta = {**OP_META_DEFAULTS, **(meta_updates or {})}
    meta.update({"client_id": client_id, "op_id": op_id, "firma": firma, "vence": vence})
    ok, msg = write_op_meta(op_folder, meta)
    if ok:
        return True, f"Operación creada: {op_folder_name}"
    return False, msg


def update_operation(paths: dict, client_id: str, op_folder_name: str, updates: dict, rename_folder: bool = False) -> Tuple[bool, str]:
    clientes = load_clientes(paths["CLIENTES_MASTER"])
    row = clientes[clientes["client_id"] == client_id]
    if row.empty:
        return False, "Cliente no existe."
    client_folder = paths["DATA_DIR"] / row.iloc[0]["folder_name"]
    op_folder = client_folder / "_OPERACIONES" / op_folder_name
    if not op_folder.exists():
        return False, "No existe la carpeta de operación."

    meta = read_op_meta(op_folder)
    merged = {**meta, **(updates or {})}
    ok, msg = write_op_meta(op_folder, merged)
    if not ok:
        return False, msg

    if rename_folder:
        op_id = merged.get("op_id", meta.get("op_id", "")).strip().upper()
        firma = merged.get("firma", meta.get("firma", "")).strip()
        if not firma:
            return False, "No puedo renombrar sin firma."
        new_name = build_op_folder(op_id, firma)
        if new_name != op_folder.name:
            ok2, msg2 = safe_rename(op_folder, op_folder.parent / new_name)
            if ok2:
                return True, f"Actualizada y renombrada → {new_name}"
            return False, f"OP_META ok, pero rename falló: {msg2}"

    return True, "Operación actualizada."


# ─────────────────────────────────────────────────────────────────────────────
# Document save (determinístico)
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_dest(paths: dict, client_folder: Path, rule: DocRule, parte_folder: str = "", op_folder: str = "") -> Path:
    rel = rule.dest_rel.format(parte_folder=parte_folder, op_folder=op_folder)
    if not rel:
        return client_folder
    return client_folder / rel

def _find_existing_by_base(dest_dir: Path, base: str) -> list[Path]:
    if not dest_dir.exists():
        return []
    out = []
    for p in dest_dir.iterdir():
        if p.is_file() and p.stem.upper() == base.upper():
            out.append(p)
    return out

def save_text_field(paths: dict, client_id: str, field: str, value: str, *, role: str = "", party_id: str = "") -> Tuple[bool, str]:
    field = field.strip()
    value = str(value or "").strip()

    allowed = {"email", "phone", "tipo_persona", "rfc", "curp"}
    if field not in allowed:
        return False, "Campo inválido."

    # Normalizaciones básicas
    if field in {"tipo_persona", "rfc", "curp"}:
        value = value.upper()

    if role and party_id:
        partes = load_partes(paths["PARTES_RELACIONADAS"])
        mask = (partes["client_id"] == client_id) & (partes["role"].str.upper() == role.upper()) & (partes["party_id"].astype(str) == str(party_id))
        if not mask.any():
            return False, "Parte no existe."
        partes.loc[mask, field] = value
        ok, msg = write_partes(paths["PARTES_RELACIONADAS"], partes)
        if ok:
            _mark_needs_scan()
        return ok, msg

    clientes = load_clientes(paths["CLIENTES_MASTER"])
    mask = clientes["client_id"] == client_id
    if not mask.any():
        return False, "Cliente no existe."
    clientes.loc[mask, field] = value
    ok, msg = write_clientes(paths["CLIENTES_MASTER"], clientes)
    if ok:
        _mark_needs_scan()
    return ok, msg

def save_file_for_rule(
    paths: dict,
    client_id: str,
    rule_key: str,
    file_name: str,
    file_bytes: bytes,
    *,
    parte_folder: str = "",
    op_folder_name: str = "",
    op_meta: Optional[dict] = None,
    extra: str = "",
    overwrite: bool = True,
) -> Tuple[bool, str, Optional[Path]]:
    if rule_key not in DOC_RULES:
        return False, "Regla no existe.", None
    rule = DOC_RULES[rule_key]
    if rule.input_type != "file":
        return False, "Esta regla no es de archivo.", None

    clientes = load_clientes(paths["CLIENTES_MASTER"])
    crow = clientes[clientes["client_id"] == client_id]
    if crow.empty:
        return False, "Cliente no existe.", None
    folder_name = crow.iloc[0]["folder_name"]
    client_folder = paths["DATA_DIR"] / folder_name

    dest_dir = _resolve_dest(paths, client_folder, rule, parte_folder=parte_folder, op_folder=op_folder_name)

    # validar requires_fields
    if rule.scope == "OPERACION":
        meta = op_meta or {}
        for f in rule.requires_fields:
            if not str(meta.get(f, "")).strip():
                return False, f"Falta capturar '{f}' antes de guardar este documento.", None

    # construir filename base + ext
    ext = Path(file_name).suffix.lower() or ".pdf"
    base = rule.filename_base(op_meta=op_meta, client_row=crow.iloc[0].to_dict(), extra=extra)
    if not base:
        return False, "No pude construir nombre destino.", None
    dst = dest_dir / f"{base}{ext}"

    ensure_dir(dest_dir)
    # reemplazo seguro (mover existentes a _TRASH)
    if overwrite:
        for p in _find_existing_by_base(dest_dir, base):
            move_to_trash(p)

    try:
        safe_write_bytes(dst, file_bytes)
        return True, f"Guardado: {dst.name}", dst
    except Exception as e:
        return False, f"No pude guardar: {e}", None

def delete_doc_safe(path: Path) -> Tuple[bool, str]:
    ok, msg, _ = move_to_trash(path)
    return ok, msg
