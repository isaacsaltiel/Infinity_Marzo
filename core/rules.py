"""core/rules.py — Única fuente de verdad de reglas de documentos (DOC_RULES)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Literal, Optional

Scope = Literal["CLIENTE", "PARTE", "OPERACION"]
InputType = Literal["file", "text"]
Priority = Literal["P1", "P2", "P3", "P4"]

@dataclass(frozen=True)
class DocRule:
    key: str
    label: str
    scope: Scope
    priority: Priority
    input_type: InputType
    # destino relativo (sin cliente root): ej "_CLIENTE" o "_PARTES/{parte_folder}" o "_OPERACIONES/{op_folder}"
    dest_rel: str
    # builder del filename BASE (sin extensión)
    filename_base: Callable[..., str]
    # campos requeridos (para operaciones) antes de permitir guardar
    requires_fields: tuple[str, ...] = ()
    # regla opcional según tipo persona o flags de op
    is_required: Optional[Callable[..., bool]] = None

def _always(*args, **kwargs) -> bool:
    return True

def _is_pm(client_row: dict) -> bool:
    return str(client_row.get("tipo_persona", "")).strip().upper() == "PM"

def _flag_truthy(val: str) -> bool:
    v = str(val or "").strip().upper()
    return v in {"SI", "S", "YES", "Y", "TRUE", "1", "REQUIERE", "REQUIRED"}

def _requires_garantia(op_meta: dict) -> bool:
    return _flag_truthy(op_meta.get("garantia"))

def _requires_conv_mediacion(op_meta: dict) -> bool:
    return _flag_truthy(op_meta.get("convenio_mediacion"))

def _requires_conv_modif(op_meta: dict) -> bool:
    return _flag_truthy(op_meta.get("convenio_modificatorio"))

def _requires_recibo(op_meta: dict) -> bool:
    return _flag_truthy(op_meta.get("recibo_efectivo_policy"))

def base_cliente(name: str) -> str:
    return name

def base_parte(name: str) -> str:
    return name

def base_op(doc_key: str, op_id: str, firma: str, vence: str, extra: str = "") -> str:
    firma = (firma or "").strip()
    vence = (vence or "").strip()
    if doc_key == "PAGARE":
        return f"PAGARE__{op_id}__FIRMA{firma}__VENCE{vence}"
    if doc_key == "MUTUO":
        return f"MUTUO__{op_id}__FIRMA{firma}__VENCE{vence}"
    if doc_key == "GARANTIA":
        tipo = (extra or "SIN_TIPO").strip().upper().replace(" ", "_")
        return f"GARANTIA__{op_id}__{tipo}"
    if doc_key == "CONVENIO_MEDIACION":
        return f"CONVENIO_MEDIACION__{op_id}__{firma}"
    if doc_key == "CONVENIO_MODIFICATORIO":
        fecha = (extra or firma).strip() or firma
        return f"CONVENIO_MODIFICATORIO__{op_id}__{fecha}"
    if doc_key == "RECIBO_EFECTIVO":
        return f"RECIBO_EFECTIVO__{op_id}__{firma}"
    if doc_key == "RECIBO_EFECTIVO_FIRMADO":
        return f"RECIBO_EFECTIVO_FIRMADO__{op_id}__{firma}"
    return f"OTRO__{op_id}__{firma}"

# DOC_RULES: claves estables (usadas para chips, scan, guardado)
DOC_RULES: dict[str, DocRule] = {
    # ── CLIENTE (text)
    "CLIENTE_EMAIL": DocRule(
        key="CLIENTE_EMAIL", label="Email", scope="CLIENTE", priority="P3",
        input_type="text", dest_rel="", filename_base=lambda **_: ""
    ),
    "CLIENTE_PHONE": DocRule(
        key="CLIENTE_PHONE", label="Teléfono", scope="CLIENTE", priority="P3",
        input_type="text", dest_rel="", filename_base=lambda **_: ""
    ),
    "CLIENTE_TIPO_PERSONA": DocRule(
        key="CLIENTE_TIPO_PERSONA", label="Tipo (PF/PM)", scope="CLIENTE", priority="P3",
        input_type="text", dest_rel="", filename_base=lambda **_: ""
    ),

    # ── CLIENTE (files)
    "CSF_CLIENTE": DocRule(
        key="CSF_CLIENTE", label="CSF", scope="CLIENTE", priority="P3",
        input_type="file", dest_rel="_CLIENTE",
        filename_base=lambda **_: "CSF__CLIENTE",
        is_required=_always,
    ),
    # INE es grupo (FRENTE/REVERSO). Se maneja en scan como "INE_CLIENTE".
    "INE_FRENTE_CLIENTE": DocRule(
        key="INE_FRENTE_CLIENTE", label="INE (Frente)", scope="CLIENTE", priority="P3",
        input_type="file", dest_rel="_CLIENTE",
        filename_base=lambda **_: "INE__FRENTE",
        is_required=_always,
    ),
    "INE_REVERSO_CLIENTE": DocRule(
        key="INE_REVERSO_CLIENTE", label="INE (Reverso)", scope="CLIENTE", priority="P3",
        input_type="file", dest_rel="_CLIENTE",
        filename_base=lambda **_: "INE__REVERSO",
        is_required=_always,
    ),
    "ACTA_CONSTITUTIVA": DocRule(
        key="ACTA_CONSTITUTIVA", label="Acta constitutiva", scope="CLIENTE", priority="P3",
        input_type="file", dest_rel="_CLIENTE",
        filename_base=lambda **_: "ACTA_CONSTITUTIVA",
        is_required=lambda client_row=None, **_: _is_pm(client_row or {}),
    ),
    "PODERES_REPRESENTANTE": DocRule(
        key="PODERES_REPRESENTANTE", label="Poderes rep.", scope="CLIENTE", priority="P3",
        input_type="file", dest_rel="_CLIENTE",
        filename_base=lambda **_: "PODERES_REPRESENTANTE",
        is_required=lambda client_row=None, **_: _is_pm(client_row or {}),
    ),
    "COMPROBANTE_DOMICILIO": DocRule(
        key="COMPROBANTE_DOMICILIO", label="Comprobante domicilio", scope="CLIENTE", priority="P4",
        input_type="file", dest_rel="_CLIENTE",
        filename_base=lambda **_: "COMPROBANTE_DOMICILIO",
        is_required=_always,
    ),
    "ESTADO_BANCARIO": DocRule(
        key="ESTADO_BANCARIO", label="Estado bancario", scope="CLIENTE", priority="P4",
        input_type="file", dest_rel="_CLIENTE",
        filename_base=lambda **_: "ESTADO_BANCARIO",
        is_required=_always,
    ),
    "ESTADOS_FINANCIEROS": DocRule(
        key="ESTADOS_FINANCIEROS", label="Estados financieros", scope="CLIENTE", priority="P4",
        input_type="file", dest_rel="_CLIENTE",
        filename_base=lambda **_: "ESTADOS_FINANCIEROS",
        is_required=_always,
    ),

    # ── PARTE (text)
    "PARTE_EMAIL": DocRule(
        key="PARTE_EMAIL", label="Email", scope="PARTE", priority="P3",
        input_type="text", dest_rel="", filename_base=lambda **_: ""
    ),
    "PARTE_PHONE": DocRule(
        key="PARTE_PHONE", label="Teléfono", scope="PARTE", priority="P3",
        input_type="text", dest_rel="", filename_base=lambda **_: ""
    ),

    # ── PARTE (files)
    "CSF_PARTE": DocRule(
        key="CSF_PARTE", label="CSF", scope="PARTE", priority="P3",
        input_type="file", dest_rel="_PARTES/{parte_folder}",
        filename_base=lambda **_: "CSF__CLIENTE",
        is_required=_always,
    ),
    "INE_FRENTE_PARTE": DocRule(
        key="INE_FRENTE_PARTE", label="INE (Frente)", scope="PARTE", priority="P3",
        input_type="file", dest_rel="_PARTES/{parte_folder}",
        filename_base=lambda **_: "INE__FRENTE",
        is_required=_always,
    ),
    "INE_REVERSO_PARTE": DocRule(
        key="INE_REVERSO_PARTE", label="INE (Reverso)", scope="PARTE", priority="P3",
        input_type="file", dest_rel="_PARTES/{parte_folder}",
        filename_base=lambda **_: "INE__REVERSO",
        is_required=_always,
    ),
    "COMPROBANTE_DOMICILIO_PARTE": DocRule(
        key="COMPROBANTE_DOMICILIO_PARTE", label="Comp. domicilio", scope="PARTE", priority="P4",
        input_type="file", dest_rel="_PARTES/{parte_folder}",
        filename_base=lambda **_: "COMPROBANTE_DOMICILIO",
        is_required=_always,
    ),

    # ── OPERACION
    "PAGARE": DocRule(
        key="PAGARE", label="Pagaré", scope="OPERACION", priority="P1",
        input_type="file", dest_rel="_OPERACIONES/{op_folder}",
        filename_base=lambda op_meta=None, **_: base_op("PAGARE", op_meta["op_id"], op_meta["firma"], op_meta["vence"]),
        requires_fields=("firma", "vence", "op_id"),
        is_required=_always,
    ),
    "MUTUO": DocRule(
        key="MUTUO", label="Mutuo", scope="OPERACION", priority="P1",
        input_type="file", dest_rel="_OPERACIONES/{op_folder}",
        filename_base=lambda op_meta=None, **_: base_op("MUTUO", op_meta["op_id"], op_meta["firma"], op_meta["vence"]),
        requires_fields=("firma", "vence", "op_id"),
        is_required=_always,
    ),
    "GARANTIA": DocRule(
        key="GARANTIA", label="Garantía", scope="OPERACION", priority="P1",
        input_type="file", dest_rel="_OPERACIONES/{op_folder}",
        filename_base=lambda op_meta=None, extra="", **_: base_op("GARANTIA", op_meta["op_id"], op_meta["firma"], op_meta["vence"], extra=extra),
        requires_fields=("op_id",),
        is_required=lambda op_meta=None, **_: _requires_garantia(op_meta or {}),
    ),
    "CONVENIO_MEDIACION": DocRule(
        key="CONVENIO_MEDIACION", label="Conv. mediación", scope="OPERACION", priority="P2",
        input_type="file", dest_rel="_OPERACIONES/{op_folder}",
        filename_base=lambda op_meta=None, **_: base_op("CONVENIO_MEDIACION", op_meta["op_id"], op_meta["firma"], op_meta["vence"]),
        requires_fields=("op_id", "firma"),
        is_required=lambda op_meta=None, **_: _requires_conv_mediacion(op_meta or {}),
    ),
    "CONVENIO_MODIFICATORIO": DocRule(
        key="CONVENIO_MODIFICATORIO", label="Conv. modificatorio", scope="OPERACION", priority="P3",
        input_type="file", dest_rel="_OPERACIONES/{op_folder}",
        filename_base=lambda op_meta=None, extra="", **_: base_op("CONVENIO_MODIFICATORIO", op_meta["op_id"], op_meta["firma"], op_meta["vence"], extra=extra),
        requires_fields=("op_id",),
        is_required=lambda op_meta=None, **_: _requires_conv_modif(op_meta or {}),
    ),
    "RECIBO_EFECTIVO": DocRule(
        key="RECIBO_EFECTIVO", label="Recibo efectivo", scope="OPERACION", priority="P2",
        input_type="file", dest_rel="_OPERACIONES/{op_folder}",
        filename_base=lambda op_meta=None, **_: base_op("RECIBO_EFECTIVO", op_meta["op_id"], op_meta["firma"], op_meta["vence"]),
        requires_fields=("op_id", "firma"),
        is_required=lambda op_meta=None, **_: _requires_recibo(op_meta or {}),
    ),
    "RECIBO_EFECTIVO_FIRMADO": DocRule(
        key="RECIBO_EFECTIVO_FIRMADO", label="Recibo firmado", scope="OPERACION", priority="P2",
        input_type="file", dest_rel="_OPERACIONES/{op_folder}",
        filename_base=lambda op_meta=None, **_: base_op("RECIBO_EFECTIVO_FIRMADO", op_meta["op_id"], op_meta["firma"], op_meta["vence"]),
        requires_fields=("op_id", "firma"),
        is_required=lambda op_meta=None, **_: _requires_recibo(op_meta or {}),
    ),
}

def required_rules_for_client(tipo_persona: str, client_row: dict) -> Iterable[DocRule]:
    for rule in DOC_RULES.values():
        if rule.scope != "CLIENTE":
            continue
        if rule.input_type == "file" and rule.is_required:
            if rule.is_required(client_row=client_row):
                yield rule
        elif rule.input_type == "text":
            yield rule

def required_rules_for_party() -> Iterable[DocRule]:
    for rule in DOC_RULES.values():
        if rule.scope == "PARTE":
            if rule.input_type == "file" and rule.is_required:
                if rule.is_required():
                    yield rule
            else:
                yield rule

def required_rules_for_op(op_meta: dict) -> Iterable[DocRule]:
    for rule in DOC_RULES.values():
        if rule.scope != "OPERACION":
            continue
        if rule.is_required and not rule.is_required(op_meta=op_meta):
            continue
        yield rule
