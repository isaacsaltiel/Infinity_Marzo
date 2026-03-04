from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd
from slugify import slugify


# -----------------------------
# Utilidades base
# -----------------------------

def load_config(config_path: Path) -> dict:
    return json.loads(config_path.read_text(encoding="utf-8"))


def strip_accents(s: str) -> str:
    s = str(s or "")
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    return s


def clean_keep_accents(v) -> str:
    """Limpia valores pero conserva acentos."""
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() == "nan":
        return ""
    return s


def clean_ascii_safe(v) -> str:
    """Limpia y quita acentos (solo para slugs / normalización técnica)."""
    return strip_accents(clean_keep_accents(v)).strip()


def write_csv_robust(df: pd.DataFrame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_name(path.stem + f"__TMP__{datetime.now().strftime('%Y%m%d_%H%M%S')}" + path.suffix)
    df.to_csv(tmp, index=False, encoding="utf-8", lineterminator="\n")

    for _ in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.25)

    raise PermissionError(
        f"No pude reemplazar {path}. El CSV nuevo quedó en: {tmp} (lock de Excel/Defender/OneDrive)."
    )


def make_folder_name(client_id: str, display_name_with_accents: str) -> str:
    base = clean_ascii_safe(display_name_with_accents)
    slug = slugify(base, separator="_").upper()
    slug = re.sub(r"[^A-Z0-9_]", "", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return f"{client_id}_{slug}"


def ensure_client_tree(client_folder: Path):
    (client_folder / "_CLIENTE").mkdir(parents=True, exist_ok=True)
    (client_folder / "_PARTES").mkdir(parents=True, exist_ok=True)
    (client_folder / "_OPERACIONES").mkdir(parents=True, exist_ok=True)
    (client_folder / "_INBOX").mkdir(parents=True, exist_ok=True)


def ensure_operation_tree(op_folder: Path):
    op_folder.mkdir(parents=True, exist_ok=True)
    (op_folder / "_INBOX").mkdir(exist_ok=True)


def write_op_meta(op_folder: Path, meta: dict):
    (op_folder / "OP_META.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def parse_sheet_arg(sheet_arg: str):
    if isinstance(sheet_arg, str) and sheet_arg.isdigit():
        return int(sheet_arg)
    return sheet_arg


def to_intlike_string(v) -> str:
    """Convierte '327.0' -> '327'."""
    s = clean_keep_accents(v)
    if not s:
        return ""
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
        return s
    except:
        return s


def norm_colname(c: str) -> str:
    return clean_ascii_safe(c).lower().strip()


def parse_ops_count(v) -> int:
    """Solo para warnings/diagnóstico (ya NO manda)."""
    s = clean_keep_accents(v)
    if not s:
        return 0
    try:
        f = float(s)
        return int(f)
    except:
        m = re.search(r"\d+", s)
        return int(m.group(0)) if m else 0


def parse_pagare_list(v) -> List[str]:
    """'328, 414' -> ['328','414']"""
    s = clean_keep_accents(v)
    if not s or s == "-" or s.lower() == "nan":
        return []
    parts = [p.strip() for p in s.split(",")]
    out = []
    for p in parts:
        if not p or p == "-":
            continue
        out.append(to_intlike_string(p))
    return out


def sort_pagares(pagares: List[str]) -> List[str]:
    def key(x: str) -> Tuple[int, str]:
        try:
            return (int(re.sub(r"\D", "", x)), x)
        except:
            return (10**18, x)
    return sorted(pagares, key=key)


def cfg_path_under_data_dir(cfg: dict, data_dir: Path, keys: List[str], default_filename: str) -> Path:
    for k in keys:
        if k in cfg and str(cfg[k]).strip():
            return data_dir / str(cfg[k])
    return data_dir / default_filename


def wipe_generated(data_dir: Path, clientes_master_path: Path, operaciones_path: Path, partes_rel_path: Path):
    """
    Borra lo generado por migraciones anteriores:
    - CSVs principales
    - Carpetas de clientes CLTxxxxx_* que contengan _OPERACIONES
    """
    targets = [clientes_master_path, operaciones_path, partes_rel_path]
    for p in targets:
        try:
            if p.exists():
                p.unlink()
        except Exception as e:
            raise SystemExit(f"No pude borrar {p}: {e}")

    # Borra carpetas de clientes (solo las que parecen de este sistema)
    if not data_dir.exists():
        return

    pat = re.compile(r"^CLT\d+_.+")
    for child in data_dir.iterdir():
        if not child.is_dir():
            continue
        if not pat.match(child.name):
            continue
        # Confirmación interna: que tenga _OPERACIONES (evita borrar cosas ajenas)
        if (child / "_OPERACIONES").exists():
            try:
                shutil.rmtree(child)
            except Exception as e:
                raise SystemExit(f"No pude borrar carpeta {child}: {e}")


# -----------------------------
# Columnas (tu core/models.py)
# -----------------------------

CLIENTES_COLS = [
    "client_id", "display_name", "legal_name", "folder_name",
    "tipo_persona", "email", "phone", "created_date", "client_status",
]

PARTES_COLS = [
    "client_id", "role", "party_id", "nombre", "email", "phone",
]

OPERATIONS_COLS = [
    "client_id", "client_name", "tipo_persona",
    "op_folder", "op_id", "numero_pagare",
    "firma", "vence", "active",
    "garantia", "convenio_mediacion", "convenio_modificatorio", "recibo_efectivo_policy",
    "pagare_status", "mutuo_status",
    "has_pagare", "has_mutuo", "has_garantia",
    "has_conv_mediacion", "has_conv_modif",
    "has_recibo", "has_recibo_firmado",
    "missing_p1", "missing_p2",
    "anomalies_count",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--excel", default=r"C:\Users\Isaac\Desktop\ClientesActualesInfinity.xlsx")
    parser.add_argument("--sheet", default="Sheet1")
    parser.add_argument("--header_row", type=int, default=1)  # fila 2 (0-indexed)
    parser.add_argument("--usecols", default="B:E")
    parser.add_argument("--created_date", default=datetime.now().date().isoformat())
    parser.add_argument("--op_date", default=datetime.now().date().isoformat())
    parser.add_argument("--start_id", type=int, default=1001)  # CLT10001...
    parser.add_argument("--config", default="app_config.json")
    parser.add_argument("--reset", action="store_true",
                        help="Borra CSVs y carpetas de clientes generadas previamente antes de migrar.")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    data_dir = Path(cfg["DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)

    clientes_master_path = cfg_path_under_data_dir(cfg, data_dir, ["CLIENTES_MASTER"], "clientes_master.csv")
    partes_rel_path = cfg_path_under_data_dir(cfg, data_dir, ["PARTES_RELACIONADAS"], "partes_relacionadas.csv")
    operaciones_path = cfg_path_under_data_dir(
        cfg, data_dir,
        ["OPERACIONES", "OPERATIONS", "OPERATIONS_CSV", "OPERACIONES_CSV"],
        "operaciones.csv"
    )

    if args.reset:
        wipe_generated(data_dir, clientes_master_path, operaciones_path, partes_rel_path)

    # Asegura partes_relacionadas.csv (vacío)
    if not partes_rel_path.exists():
        partes_rel_df = pd.DataFrame(columns=PARTES_COLS)
        write_csv_robust(partes_rel_df, partes_rel_path)

    # Lee Excel
    sheet = parse_sheet_arg(args.sheet)
    df = pd.read_excel(
        args.excel,
        sheet_name=sheet,
        header=args.header_row,
        usecols=args.usecols,
        dtype=str,
        engine="openpyxl",
    )

    # Mapear columnas por nombre (robusto a acentos/espacios)
    col_map: Dict[str, str] = {}
    for c in df.columns:
        n = norm_colname(str(c))
        if n in ("cliente", "client", "nombre", "nombrecliente"):
            col_map["cliente"] = c
        elif n in ("operaciones", "operacion", "ops"):
            col_map["operaciones"] = c
        elif n in ("pagares", "pagare"):
            col_map["pagares"] = c
        elif n in ("mail", "correo", "email", "e-mail"):
            col_map["mail"] = c

    missing = [k for k in ("cliente", "operaciones", "pagares", "mail") if k not in col_map]
    if missing:
        raise SystemExit(f"Faltan columnas en el Excel: {missing}. Columnas detectadas: {list(df.columns)}")

    next_num = args.start_id
    clientes_rows: List[Dict[str, Any]] = []
    ops_rows: List[Dict[str, Any]] = []

    imported_clients = 0
    imported_ops = 0
    warnings: List[str] = []

    for _, r in df.iterrows():
        nombre = clean_keep_accents(r.get(col_map["cliente"], ""))
        if not nombre:
            continue

        correo = clean_keep_accents(r.get(col_map["mail"], ""))

        # Operaciones (solo para warning/diagnóstico)
        ops_count_excel = parse_ops_count(r.get(col_map["operaciones"], ""))

        # Pagarés manda
        pagare_raw_list = parse_pagare_list(r.get(col_map["pagares"], ""))
        pagare_list = sort_pagares(pagare_raw_list)

        n_ops = len(pagare_list)  # ✅ regla nueva: manda # de pagarés

        cid = f"CLT{next_num}"
        next_num += 1

        folder_name = make_folder_name(cid, nombre)

        clientes_rows.append({
            "client_id": cid,
            "display_name": nombre,  # con acentos
            "legal_name": nombre,    # placeholder con acentos
            "folder_name": folder_name,
            "tipo_persona": "",
            "email": correo,
            "phone": "",
            "created_date": args.created_date,
            "client_status": "ACTIVE",
        })

        # Carpetas cliente
        client_folder = data_dir / folder_name
        ensure_client_tree(client_folder)

        # Warnings de higiene de datos
        if ops_count_excel != n_ops:
            warnings.append(
                f"[WARN] {cid} '{nombre}': Excel Operaciones={ops_count_excel} pero Pagarés={n_ops} ({pagare_raw_list})"
            )

        # Crear operaciones 1..n según pagarés ordenados
        for i, num_pagare in enumerate(pagare_list, start=1):
            op_id = f"OP{i:03d}"
            op_folder_name = f"OP__{op_id}__{args.op_date}"
            op_folder_path = client_folder / "_OPERACIONES" / op_folder_name
            ensure_operation_tree(op_folder_path)

            op_meta = {
                "client_id": cid,
                "op_id": op_id,
                "numero_pagare": num_pagare,
                "firma": "",
                "vence": "",
                "active": True,
                "garantia": "UNKNOWN",
                "convenio_mediacion": "UNKNOWN",
                "convenio_modificatorio": "UNKNOWN",
                "recibo_efectivo_policy": "UNKNOWN",
            }
            write_op_meta(op_folder_path, op_meta)

            op_folder_rel = f"{folder_name}/_OPERACIONES/{op_folder_name}"

            ops_rows.append({
                "client_id": cid,
                "client_name": nombre,
                "tipo_persona": "",
                "op_folder": op_folder_rel,
                "op_id": op_id,
                "numero_pagare": num_pagare,
                "firma": "",
                "vence": "",
                "active": True,
                "garantia": "UNKNOWN",
                "convenio_mediacion": "UNKNOWN",
                "convenio_modificatorio": "UNKNOWN",
                "recibo_efectivo_policy": "UNKNOWN",
                "pagare_status": "DESCONOCIDO",
                "mutuo_status": "DESCONOCIDO",
                "has_pagare": 0,
                "has_mutuo": 0,
                "has_garantia": 0,
                "has_conv_mediacion": 0,
                "has_conv_modif": 0,
                "has_recibo": 0,
                "has_recibo_firmado": 0,
                "missing_p1": "",
                "missing_p2": "",
                "anomalies_count": 0,
            })
            imported_ops += 1

        imported_clients += 1

    if imported_clients == 0:
        raise SystemExit("Importó 0 clientes. Revisa que haya nombres en la columna 'Cliente'.")

    # DataFrames finales (orden y columnas exactas)
    clientes_master_df = (
        pd.DataFrame(clientes_rows, columns=CLIENTES_COLS)
        .sort_values("client_id")
        .reset_index(drop=True)
    )

    operaciones_df = (
        pd.DataFrame(ops_rows, columns=OPERATIONS_COLS)
        .sort_values(["client_id", "op_id"])
        .reset_index(drop=True)
    )

    write_csv_robust(clientes_master_df, clientes_master_path)
    write_csv_robust(operaciones_df, operaciones_path)

    print("✅ Migración terminada.")
    print(f"Clientes importados: {imported_clients}")
    print(f"Operaciones creadas:  {imported_ops}")
    print(f"clientes_master.csv   -> {clientes_master_path}")
    print(f"partes_relacionadas.csv -> {partes_rel_path}")
    print(f"operaciones.csv       -> {operaciones_path}")
    print(f"Carpetas creadas bajo -> {data_dir}")

    if warnings:
        print("\n⚠️ WARNINGS (Excel Operaciones vs # Pagarés):")
        for w in warnings[:200]:
            print(w)
        if len(warnings) > 200:
            print(f"... y {len(warnings) - 200} warnings más.")


if __name__ == "__main__":
    main()