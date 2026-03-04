"""core/scan.py - Escaneo (solo nombres/existencia) -> Output/checklist.csv + operations.csv + anomalies.csv"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

from core.models import ANOMALIES_COLS, CHECKLIST_COLS, OPERATIONS_COLS
from core.models import CLIENTES_COLS, PARTES_COLS
from core.rules import required_rules_for_client, required_rules_for_op
from core.storage import atomic_write_csv, read_csv, read_json


def _is_valid_email(s: str) -> bool:
    s = str(s or "").strip()
    if not s or s in {"-", "--"}:
        return False
    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", s) is not None

def _is_valid_phone(s: str) -> bool:
    raw = str(s or "").strip()
    if not raw or raw in {"-", "--"}:
        return False
    digits = re.sub(r"\D", "", raw)
    return len(digits) >= 10

def _is_valid_tipo_persona(s: str) -> bool:
    v = str(s or "").strip().upper()
    return v in {"PF", "PM"}

def _norm_rfc(s: str) -> str:
    v = str(s or "").strip().upper()
    if not v or v in {"-", "--"}:
        return ""
    # permite que usuario meta guiones/espacios
    v = re.sub(r"[^A-Z0-9&Ñ]", "", v)
    return v

def _is_valid_rfc(s: str) -> bool:
    v = _norm_rfc(s)
    if not v:
        return False
    if len(v) not in (12, 13):
        return False
    return re.match(r"^[A-Z&Ñ]{3,4}\d{6}[A-Z0-9]{3}$", v) is not None

def _norm_curp(s: str) -> str:
    v = str(s or "").strip().upper()
    if not v or v in {"-", "--"}:
        return ""
    v = re.sub(r"[^A-Z0-9]", "", v)
    return v

def _is_valid_curp(s: str) -> bool:
    v = _norm_curp(s)
    if not v:
        return False
    if len(v) != 18:
        return False
    # validación tolerante (no hiper-estricta para no castigar casos raros)
    return re.match(r"^[A-Z]{4}\d{6}[A-Z0-9]{8}$", v) is not None

def _dump(xs: list[str]) -> str:
    return json.dumps(xs, ensure_ascii=False)

def _parse_date(s: str) -> Optional[date]:
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()
    except Exception:
        return None

def _exists_by_stem(dir_path: Path, stem: str) -> bool:
    if not dir_path.exists():
        return False
    for p in dir_path.iterdir():
        if p.is_file() and p.stem.upper() == stem.upper():
            return True
    return False

def _any_startswith(dir_path: Path, prefix: str) -> bool:
    if not dir_path.exists():
        return False
    pref = prefix.upper()
    for p in dir_path.iterdir():
        if p.is_file() and p.name.upper().startswith(pref):
            return True
    return False

def _any_exists(dir_path: Path, stems: list[str]) -> bool:
    return any(_exists_by_stem(dir_path, s) for s in stems)
def _prefer_pending_csv(path: Path) -> Path:
    """
    Si existe un __PENDING__ más nuevo que el archivo base, usarlo.
    Esto es clave en OneDrive/Sync para leer el estado más reciente.
    """
    try:
        pending = sorted(
            path.parent.glob(f"{path.stem}__PENDING__*{path.suffix}"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if pending:
            base_mtime = path.stat().st_mtime if path.exists() else 0.0
            if (not path.exists()) or (pending[0].stat().st_mtime > base_mtime):
                return pending[0]
    except Exception:
        pass
    return path
def scan(paths: dict) -> Tuple[bool, str]:
    data_dir: Path = paths["DATA_DIR"]
    out_dir: Path = paths["OUTPUT_DIR"]
    out_dir.mkdir(parents=True, exist_ok=True)

    clientes_path = _prefer_pending_csv(Path(paths["CLIENTES_MASTER"]))
    partes_path = _prefer_pending_csv(Path(paths["PARTES_RELACIONADAS"]))

    clientes = read_csv(clientes_path, CLIENTES_COLS)
    partes = read_csv(partes_path, PARTES_COLS)

    checklist_rows = []
    op_rows = []
    anom_rows = []

    for _, crow in clientes.iterrows():
        client_id = str(crow["client_id"]).strip()
        folder_name = str(crow["folder_name"]).strip()
        client_folder = data_dir / folder_name

        required_total = 0
        missing_p1, missing_p2, missing_p3, missing_p4 = [], [], [], []

        def _add_missing(priority: str, key: str):
            if priority == "P1":
                missing_p1.append(key)
            elif priority == "P2":
                missing_p2.append(key)
            elif priority == "P3":
                missing_p3.append(key)
            else:
                missing_p4.append(key)

        # ---------------- CLIENTE (text) ----------------
        tipo_persona = str(crow.get("tipo_persona", "")).strip().upper()

        # email + phone + tipo + rfc (siempre), curp solo si PF
        required_total += 4 + (1 if tipo_persona == "PF" else 0)

        if not _is_valid_email(crow.get("email", "")):
            _add_missing("P3", "CLIENTE_EMAIL")

        if not _is_valid_phone(crow.get("phone", "")):
            _add_missing("P3", "CLIENTE_PHONE")

        if not _is_valid_tipo_persona(tipo_persona):
            _add_missing("P3", "CLIENTE_TIPO_PERSONA")

        if not _is_valid_rfc(crow.get("rfc", "")):
            _add_missing("P3", "CLIENTE_RFC")

        if tipo_persona == "PF" and (not _is_valid_curp(crow.get("curp", ""))):
            _add_missing("P3", "CLIENTE_CURP")

        # ---------------- CLIENTE (docs) ----------------
        cliente_dir = client_folder / "_CLIENTE"
        for rule in required_rules_for_client(tipo_persona, crow.to_dict()):
            if rule.input_type != "file":
                continue
            if rule.key in {"INE_FRENTE_CLIENTE", "INE_REVERSO_CLIENTE"}:
                continue  # group

            required_total += 1
            stem = rule.filename_base(client_row=crow.to_dict(), op_meta=None, extra="")
            if not _exists_by_stem(cliente_dir, stem):
                _add_missing(rule.priority, rule.key)

        # INE group (cliente) counts as 1
        required_total += 1
        if not _any_exists(cliente_dir, ["INE__FRENTE", "INE__REVERSO"]):
            _add_missing("P3", "INE_CLIENTE")

        # anomalies cliente_dir
        allowed_stems = {
            "CSF__CLIENTE",
            "INE__FRENTE",
            "INE__REVERSO",
            "ACTA_CONSTITUTIVA",
            "PODERES_REPRESENTANTE",
            "COMPROBANTE_DOMICILIO",
            "ESTADO_BANCARIO",
            "ESTADOS_FINANCIEROS",
        }
        if cliente_dir.exists():
            for p in cliente_dir.iterdir():
                if p.is_file() and p.stem.upper() not in allowed_stems:
                    anom_rows.append({"client_id": client_id, "where": str(cliente_dir), "filename": p.name})

        # ---------------- PARTES ----------------
        partes_client = partes[partes["client_id"] == client_id].copy()
        partes_root = client_folder / "_PARTES"

        for _, prow in partes_client.iterrows():
            role = str(prow.get("role", "")).strip().upper()
            pid = str(prow.get("party_id", "")).strip()
            prefix = f"{role}__{str(pid).zfill(2)}__".upper()

            parte_folder = None
            if partes_root.exists():
                for d in partes_root.iterdir():
                    if d.is_dir() and d.name.upper().startswith(prefix):
                        parte_folder = d
                        break

            # required text (4): email/phone/rfc/curp
            required_total += 4
            if not _is_valid_email(prow.get("email", "")):
                _add_missing("P3", f"PARTE::{role}::{pid}::PARTE_EMAIL")
            if not _is_valid_phone(prow.get("phone", "")):
                _add_missing("P3", f"PARTE::{role}::{pid}::PARTE_PHONE")
            if not _is_valid_rfc(prow.get("rfc", "")):
                _add_missing("P3", f"PARTE::{role}::{pid}::PARTE_RFC")
            if not _is_valid_curp(prow.get("curp", "")):
                _add_missing("P3", f"PARTE::{role}::{pid}::PARTE_CURP")

            # folder missing => anomaly + skip docs
            if parte_folder is None:
                anom_rows.append(
                    {"client_id": client_id, "where": str(partes_root), "filename": f"MISSING_FOLDER:{role}__{pid}"}
                )
                _add_missing("P3", f"PARTE::{role}::{pid}::FOLDER")
                continue

            # required docs (CSF + INE group + comp domicilio)
            required_total += 1
            if not _exists_by_stem(parte_folder, "CSF__CLIENTE"):
                _add_missing("P3", f"PARTE::{role}::{pid}::CSF_PARTE")

            required_total += 1
            if not _any_exists(parte_folder, ["INE__FRENTE", "INE__REVERSO"]):
                _add_missing("P3", f"PARTE::{role}::{pid}::INE_PARTE")

            required_total += 1
            if not _exists_by_stem(parte_folder, "COMPROBANTE_DOMICILIO"):
                _add_missing("P4", f"PARTE::{role}::{pid}::COMPROBANTE_DOMICILIO_PARTE")

            # anomalies parte_folder
            allowed_part_stems = {"CSF__CLIENTE", "INE__FRENTE", "INE__REVERSO", "COMPROBANTE_DOMICILIO"}
            for p in parte_folder.iterdir():
                if p.is_dir() and p.name.upper() in {"_INBOX", "_TRASH"}:
                    continue
                if p.is_file() and p.stem.upper() not in allowed_part_stems:
                    anom_rows.append({"client_id": client_id, "where": str(parte_folder), "filename": p.name})

        # ---------------- OPERACIONES ----------------
        ops_root = client_folder / "_OPERACIONES"
        active_ops, archived_ops = 0, 0
        next_due: Optional[date] = None

        if ops_root.exists():
            for opf in ops_root.iterdir():
                if not opf.is_dir():
                    continue
                if opf.name.upper().startswith("XX_"):
                    archived_ops += 1
                    continue

                meta = read_json(opf / "OP_META.json", default={})
                if not bool(meta.get("active", True)):
                    archived_ops += 1
                    continue

                active_ops += 1

                op_id = str(meta.get("op_id", "")).strip().upper()
                firma = str(meta.get("firma", "")).strip()
                vence = str(meta.get("vence", "")).strip()
                due = _parse_date(vence)
                if due and (next_due is None or due < next_due):
                    next_due = due

                op_missing_p1, op_missing_p2 = [], []

                for rule in required_rules_for_op(meta):
                    if rule.input_type != "file":
                        continue
                    required_total += 1

                    if rule.key in {"PAGARE", "MUTUO"}:
                        # Detección robusta:
                        # 1) Si hay firma Y vence válidos → busca stem exacto
                        # 2) Fallback: busca cualquier archivo que empiece con PAGARE__{op_id}__ o MUTUO__{op_id}__
                        firma_ok = bool(_parse_date(firma))
                        vence_ok = bool(_parse_date(vence))
                        found = False
                        if firma_ok and vence_ok:
                            stem = rule.filename_base(op_meta=meta)
                            found = _exists_by_stem(opf, stem)
                        if not found:
                            # fallback por prefijo (funciona aunque las fechas estén vacías o cambien)
                            found = _any_startswith(opf, f"{rule.key}__{op_id}__")
                        if not found:
                            op_missing_p1.append(rule.key)
                            _add_missing("P1", f"OP::{opf.name}::{rule.key}")
                    elif rule.key == "GARANTIA":
                        if not _any_startswith(opf, f"GARANTIA__{op_id}__"):
                            op_missing_p1.append(rule.key)
                            _add_missing("P1", f"OP::{opf.name}::{rule.key}")
                    else:
                        prefix = f"{rule.key}__{op_id}__"
                        if not _any_startswith(opf, prefix):
                            op_missing_p2.append(rule.key)
                            _add_missing(rule.priority, f"OP::{opf.name}::{rule.key}")

                # anomalies op folder
                allowed_prefixes = {
                    "OP_META",
                    "PAGARE__",
                    "MUTUO__",
                    "GARANTIA__",
                    "CONVENIO_MEDIACION__",
                    "CONVENIO_MODIFICATORIO__",
                    "RECIBO_EFECTIVO__",
                    "RECIBO_EFECTIVO_FIRMADO__",
                }
                for p in opf.iterdir():
                    if p.is_dir() and p.name.upper() in {"_INBOX", "_TRASH"}:
                        continue
                    if p.is_file():
                        if p.name == "OP_META.json":
                            continue
                        up = p.name.upper()
                        if not any(up.startswith(pref) for pref in allowed_prefixes):
                            anom_rows.append({"client_id": client_id, "where": str(opf), "filename": p.name})

                op_rows.append(
                    {
                        "client_id": client_id,
                        "client_name": crow.get("display_name", ""),
                        "tipo_persona": crow.get("tipo_persona", ""),
                        "op_folder": opf.name,
                        "op_id": op_id,
                        "numero_pagare": str(meta.get("numero_pagare", "")),
                        "firma": firma,
                        "vence": vence,
                        "active": True,
                        "garantia": str(meta.get("garantia", "")),
                        "convenio_mediacion": str(meta.get("convenio_mediacion", "")),
                        "convenio_modificatorio": str(meta.get("convenio_modificatorio", "")),
                        "recibo_efectivo_policy": str(meta.get("recibo_efectivo_policy", "")),
                        "pagare_status": str(meta.get("pagare_status", "DESCONOCIDO")),
                        "mutuo_status": str(meta.get("mutuo_status", "DESCONOCIDO")),
                        "has_pagare": "PAGARE" not in op_missing_p1,
                        "has_mutuo": "MUTUO" not in op_missing_p1,
                        "has_garantia": "GARANTIA" not in op_missing_p1,
                        "has_conv_mediacion": "CONVENIO_MEDIACION" not in op_missing_p2,
                        "has_conv_modif": "CONVENIO_MODIFICATORIO" not in op_missing_p2,
                        "has_recibo": "RECIBO_EFECTIVO" not in op_missing_p2,
                        "has_recibo_firmado": "RECIBO_EFECTIVO_FIRMADO" not in op_missing_p2,
                        "missing_p1": _dump(op_missing_p1),
                        "missing_p2": _dump(op_missing_p2),
                        "anomalies_count": 0,
                    }
                )

        has_active_ops = active_ops > 0

        # ---------------- Semaforo ----------------
        if not has_active_ops:
            sem = "GRIS"
        elif missing_p1:
            sem = "ROJO"
        elif missing_p2 or missing_p3:
            sem = "AMARILLO"
        elif missing_p4:
            sem = "VERDE"
        else:
            sem = "COMPLETADO"

        pasable = has_active_ops and (not missing_p1) and (not missing_p2) and (not missing_p3)
        completado = pasable and (not missing_p4)

        miss_count = len(missing_p1) + len(missing_p2) + len(missing_p3) + len(missing_p4)
        pct_ok = 0.0 if required_total <= 0 else max(0.0, min(1.0, (required_total - miss_count) / required_total))

        checklist_rows.append(
            {
                "client_id": client_id,
                "client_name": crow.get("display_name", ""),
                "tipo_persona": crow.get("tipo_persona", ""),
                "client_status": crow.get("client_status", ""),
                "has_active_ops": has_active_ops,
                "active_ops_count": active_ops,
                "archived_ops_count": archived_ops,
                "semaforo": sem,
                "pasable": pasable,
                "completado": completado,
                "pct_ok": round(pct_ok, 3),
                "missing_p1": _dump(missing_p1),
                "missing_p2": _dump(missing_p2),
                "missing_p3": _dump(missing_p3),
                "missing_p4": _dump(missing_p4),
                "next_due": str(next_due) if next_due else "",
                "anomalies_count": 0,
                "folder_name": folder_name,
            }
        )

    anom_df = pd.DataFrame(anom_rows, columns=ANOMALIES_COLS).fillna("")
    checklist_df = pd.DataFrame(checklist_rows, columns=CHECKLIST_COLS).fillna("")
    op_df = pd.DataFrame(op_rows, columns=OPERATIONS_COLS).fillna("")

    # fill anomalies_count per client
    counts = anom_df.groupby("client_id").size().to_dict() if not anom_df.empty else {}
    checklist_df["anomalies_count"] = checklist_df["client_id"].map(lambda x: int(counts.get(x, 0)))

    ok1, msg1 = atomic_write_csv(checklist_df[CHECKLIST_COLS], paths["CHECKLIST_CSV"])
    ok2, msg2 = atomic_write_csv(op_df[OPERATIONS_COLS], paths["OPERATIONS_CSV"])
    ok3, msg3 = atomic_write_csv(anom_df[ANOMALIES_COLS], paths["ANOMALIES_CSV"])

    ok = ok1 and ok2 and ok3
    return ok, " | ".join([msg1, msg2, msg3])