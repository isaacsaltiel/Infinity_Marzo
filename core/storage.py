"""core/storage.py - Lectura/escritura atomica de CSV y JSON (con soporte __PENDING__)."""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Helpers: resolve __PENDING__
# ---------------------------------------------------------------------------

def _resolve_pending_path(path: Path) -> Path:
    """
    Si existe un archivo __PENDING__ mas nuevo que el original, lo usa.
    Esto ayuda cuando Windows/Excel/OneDrive bloquea reemplazos atomicos.
    """
    pending = sorted(
        path.parent.glob(f"{path.stem}__PENDING__*{path.suffix}"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not pending:
        return path

    if (not path.exists()) or (pending[0].stat().st_mtime > path.stat().st_mtime):
        return pending[0]
    return path


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def ensure_csv(path: Path, columns: list[str]) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(columns=columns)
        _write_df(df, path)

def read_csv(path: Path, columns: list[str]) -> pd.DataFrame:
    ensure_csv(path, columns)
    actual_path = _resolve_pending_path(path)

    df = pd.read_csv(actual_path, dtype=str).fillna("")
    for c in columns:
        if c not in df.columns:
            df[c] = ""
    return df[columns]

def atomic_write_csv(df: pd.DataFrame, path: Path) -> tuple[bool, str]:
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        delete=False,
        dir=str(path.parent),
        newline="",
        encoding="utf-8-sig",
    ) as tmp:
        df.to_csv(tmp, index=False, lineterminator="\n")
        tmp_path = Path(tmp.name)

    for _ in range(25):
        try:
            os.replace(tmp_path, path)
            return True, f"OK: {path}"
        except PermissionError:
            time.sleep(0.25)

    pending = path.with_name(
        f"{path.stem}__PENDING__{datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}"
    )
    try:
        os.replace(tmp_path, pending)
        return True, f"Guardado en PENDING (cierra Excel/visor para unificar): {pending.name}"
    except Exception as e:
        return False, f"Error al escribir CSV: {e}"


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _write_df(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, lineterminator="\n", encoding="utf-8-sig")

def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default if default is not None else {}

    actual_path = _resolve_pending_path(path)
    try:
        return json.loads(actual_path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}

def atomic_write_json(data: Any, path: Path) -> tuple[bool, str]:
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        delete=False,
        dir=str(path.parent),
        suffix=path.suffix or ".json",
        encoding="utf-8",
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp_path = Path(tmp.name)

    for _ in range(25):
        try:
            os.replace(tmp_path, path)
            return True, f"OK: {path}"
        except PermissionError:
            time.sleep(0.25)

    pending = path.with_name(
        f"{path.stem}__PENDING__{datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}"
    )
    try:
        os.replace(tmp_path, pending)
        return True, f"Guardado en PENDING: {pending.name}"
    except Exception as e:
        return False, f"Error al escribir JSON: {e}"