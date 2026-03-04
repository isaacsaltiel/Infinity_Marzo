# Changelog (release)

✅ Cambios aplicados:

- **CLIENT_ID_START = 1001** (sin padding, IDs CLT1001, CLT1002, …).
- Regex de clientes: `CLT\d+_` (acepta CLT1001_...).
- `DOC_RULES` es la **única fuente de verdad** para naming y rutas.
- `save_file_for_rule()` guarda con naming exacto y reemplazo seguro (mueve el viejo a `_TRASH/`).
- `scan.py`:
  - agrega faltantes de **operaciones** al checklist (keys `OP::<op_folder>::<DOC>`),
  - progreso real `pct_ok = (requeridos - faltantes) / requeridos`,
  - match flexible por prefijo para docs con sufijo variable (garantía / convenios / recibos),
  - anomalías en `_CLIENTE`, `_PARTES` y `_OPERACIONES`.
- Dashboard:
  - chips editables (cliente, parte y operación) con `st.dialog`,
  - NO usa HTML (solo Streamlit),
  - scan se ejecuta únicamente con botón **Recalcular**.
- Gestión:
  - CRUD clientes/partes/operaciones,
  - administrar documentos (abrir, reemplazar, borrar seguro).

