"""core/config.py — Carga app_config.json y resuelve paths."""
from __future__ import annotations

import json
from pathlib import Path

def load_config(cfg_path: Path | None = None) -> dict:
    if cfg_path is None:
        cfg_path = Path(__file__).resolve().parents[1] / "app_config.json"
    if not cfg_path.exists():
        raise FileNotFoundError(f"No existe {cfg_path}")
    return json.loads(cfg_path.read_text(encoding="utf-8"))

def resolve_paths(cfg: dict) -> dict:
    data_dir = Path(cfg["DATA_DIR"])
    output_dir = Path(cfg.get("OUTPUT_DIR", str(data_dir.parent / "Output")))
    new_db_root = Path(cfg.get("NEW_DB_ROOT", str(data_dir)))

    return {
        "DATA_DIR": data_dir,
        "NEW_DB_ROOT": new_db_root,
        "OUTPUT_DIR": output_dir,
        "CLIENTES_MASTER": data_dir / cfg.get("CLIENTES_MASTER", "clientes_master.csv"),
        "PARTES_RELACIONADAS": data_dir / cfg.get("PARTES_RELACIONADAS", "partes_relacionadas.csv"),
        "CHECKLIST_CSV": output_dir / "checklist.csv",
        "OPERATIONS_CSV": output_dir / "operations.csv",
        "ANOMALIES_CSV": output_dir / "anomalies.csv",
        "NOTES_JSON": output_dir / "notes.json",
        "DELETED_DIR": new_db_root / "_DELETED",
        "ID_PREFIX": str(cfg.get("ID_PREFIX", "CLT")).upper(),
        "CLIENT_ID_START": int(cfg.get("CLIENT_ID_START", 1001)),  # ✅ default correcto
        "ID_PAD": int(cfg.get("ID_PAD", 0)),
    }
