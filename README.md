# DocDashApp — Dashboard + Gestión de expedientes (Streamlit)

## Qué hace
- **Dashboard:** semáforo por cliente, avance, faltantes por prioridad (chips), operaciones, anomalías y notas.
- **Gestión:** crear/editar/eliminar clientes, partes (REP_LEGAL / AVAL), operaciones, y administrar documentos (abrir, reemplazar, borrar seguro).

✅ 100% Streamlit (sin HTML, sin unsafe_allow_html, sin st.components.html).

---

## Estructura del proyecto

```
DocDashApp/
├── app.py
├── app_config.json
├── requirements.txt
├── core/
│   ├── config.py
│   ├── storage.py
│   ├── models.py
│   ├── rules.py
│   ├── actions.py
│   └── scan.py
└── views/
    ├── dashboard.py
    └── gestion.py
```

---

## Estructura de datos (OneDrive compartido)

```
Clientes_V2/
  Data/
    clientes_master.csv
    partes_relacionadas.csv
    CLT1001_NOMBRE_SLUG/
      _CLIENTE/
      _PARTES/
        REP_LEGAL__01__NOMBRE/
        AVAL__01__NOMBRE/
      _OPERACIONES/
        OP__OP001__YYYY-MM-DD/
          OP_META.json
      _INBOX/   (opcional)
  Output/
    checklist.csv
    operations.csv
    anomalies.csv
    notes.json
```

---

## Instalación (Windows)

```powershell
cd C:\Proyectos\DocDashApp
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

---

## Notas importantes
- `CLIENT_ID_START = 1001` (sin padding CLT001).
- IDs: `CLT1001`, `CLT1002`, ...
- Regex clientes acepta `CLT\d+_`.
- Roles de partes: `REP_LEGAL` y `AVAL`.
- Teléfono/email/tipo_persona NO son archivos: son inputs que se guardan en CSV.
- Para recalcular checklist/anomalías, usa el botón **Recalcular** (no se escanea en cada click).
