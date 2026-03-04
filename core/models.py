"""core/models.py — Esquemas de columnas + constantes."""
from __future__ import annotations

CLIENTES_COLS = [
    "client_id", "display_name", "legal_name", "folder_name",
    "tipo_persona", "email", "phone", "rfc", "curp",
    "created_date", "client_status",
]

PARTES_COLS = [
    "client_id", "role", "party_id", "nombre", "email", "phone",
    "rfc", "curp",
]

CHECKLIST_COLS = [
    "client_id", "client_name", "tipo_persona", "client_status",
    "has_active_ops", "active_ops_count", "archived_ops_count",
    "semaforo", "pasable", "completado", "pct_ok",
    "missing_p1", "missing_p2", "missing_p3", "missing_p4",
    "next_due", "anomalies_count", "folder_name",
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

ANOMALIES_COLS = ["client_id", "where", "filename"]

PAGARE_MUTUO_STATUSES = [
    "DESCONOCIDO",
    "NO_HAY_DOC_FIRMADO",
    "FIRMADO_FISICO",
    "FIRMADO_DIGITAL",
]

SEMAFORO_EMOJI = {
    "ROJO": "🔴",
    "AMARILLO": "🟡",
    "VERDE": "🟢",
    "COMPLETADO": "✅",
    "GRIS": "⚫",
}
