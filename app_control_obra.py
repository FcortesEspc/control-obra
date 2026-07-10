"""
Sistema de Control de Ejecución de Obra - Residencia JE132
Versión 2.0 mejorada:
  - Persistencia real con SQLite (sobrevive refrescos y reinicios)
  - Total presupuestado calculado, no hardcodeado
  - Lógica de indirectos corregida (sin combinaciones incoherentes)
  - Fecha, proveedor y folio en cada registro
  - Eliminación de registros erróneos desde la bitácora
  - Control separado de pagos del cliente (anticipo y estimaciones)
  - Avance físico por fase vs avance financiero
  - Gráfica comparativa presupuesto vs real
  - Exportación de la bitácora a CSV

Ejecutar con:  streamlit run app_control_obra.py
"""

import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------
# CONFIGURACIÓN GENERAL
# ---------------------------------------------------------------
st.set_page_config(page_title="Control de Obra - Residencia JE132", layout="wide")

# En Railway define la variable DB_DIR=/data (con un Volume montado en /data)
# para que la base de datos sobreviva a los redespliegues.
# En local, sin la variable, usa la carpeta del script.
DB_PATH = Path(os.environ.get("DB_DIR", Path(__file__).parent)) / "control_obra_je132.db"


# ---------------------------------------------------------------
# CONTROL DE ACCESO
# ---------------------------------------------------------------
# En Railway define la variable APP_PASSWORD con la contraseña de acceso.
# En local, si la variable no existe, la app abre sin pedir contraseña.
def verificar_acceso() -> bool:
    password_configurada = os.environ.get("APP_PASSWORD", "")
    if not password_configurada:
        return True  # Sin contraseña configurada (modo local/desarrollo)

    if st.session_state.get("autenticado"):
        return True

    st.title("🔒 Control de Obra - Residencia JE132")
    st.caption("DACAM & HOGAR 911 | Acceso restringido")
    with st.form("form_login"):
        pwd = st.text_input("Contraseña de acceso:", type="password")
        entrar = st.form_submit_button("Entrar")
    if entrar:
        if pwd == password_configurada:
            st.session_state.autenticado = True
            st.rerun()
        else:
            st.error("Contraseña incorrecta.")
    return False


if not verificar_acceso():
    st.stop()

FASE_INDIRECTOS = "Gastos Indirectos"
TIPOS_DIRECTOS = ["Materiales", "Mano de Obra"]

# Compatibilidad de ancho entre versiones de Streamlit (>=1.46 usa width="stretch")
try:
    _ver = tuple(int(x) for x in st.__version__.split(".")[:2])
except Exception:
    _ver = (0, 0)
FULL_WIDTH = {"width": "stretch"} if _ver >= (1, 46) else {"use_container_width": True}


# ---------------------------------------------------------------
# PRESUPUESTO BASE (datos de la cotización)
# ---------------------------------------------------------------
@st.cache_data
def obtener_presupuesto_base() -> pd.DataFrame:
    fases_data = [
        {"Fase": "Fase 1: Terracerías y Cimentación", "Semanas": "1-4", "Materiales": 290000.0, "Mano de Obra": 298600.0},
        {"Fase": "Fase 2: Estructura Principal y Muros PB", "Semanas": "5-9", "Materiales": 275000.0, "Mano de Obra": 267000.0},
        {"Fase": "Fase 3: Losas de Entrepiso y Albañilería PA", "Semanas": "10-13", "Materiales": 255000.0, "Mano de Obra": 365000.0},
        {"Fase": "Fase 4: Losa de Azotea y Pérgola", "Semanas": "14-16", "Materiales": 183000.0, "Mano de Obra": 195000.0},
        {"Fase": "Fase 5: Instalaciones Hidrosanitarias y Eléctricas", "Semanas": "16-19", "Materiales": 122500.0, "Mano de Obra": 186000.0},
        {"Fase": "Fase 6: Repellados y Yesos", "Semanas": "17-23", "Materiales": 175000.0, "Mano de Obra": 195000.0},
    ]
    df = pd.DataFrame(fases_data)
    df["Subtotal Costo Directo"] = df["Materiales"] + df["Mano de Obra"]
    return df


# Indirectos fijos según cotización
INDIRECTOS = {
    "Proyecto Arquitectónico, Dirección y Supervisión": 125000.0,
    "Gestión Administrativa y Control": 112284.0,
}

df_presupuesto = obtener_presupuesto_base()
FASES = df_presupuesto["Fase"].tolist()

p_materiales = df_presupuesto["Materiales"].sum()
p_mano_obra = df_presupuesto["Mano de Obra"].sum()
p_indirectos = sum(INDIRECTOS.values())
# Total CALCULADO: si mañana cambia una fase, el total se ajusta solo.
total_presupuestado = p_materiales + p_mano_obra + p_indirectos


# ---------------------------------------------------------------
# BASE DE DATOS (SQLite)
# ---------------------------------------------------------------
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS gastos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                fase TEXT NOT NULL,
                tipo TEXT NOT NULL,
                monto REAL NOT NULL CHECK (monto > 0),
                proveedor TEXT,
                descripcion TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pagos_cliente (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                concepto TEXT NOT NULL,
                monto REAL NOT NULL CHECK (monto > 0)
            );
            CREATE TABLE IF NOT EXISTS avance_fisico (
                fase TEXT PRIMARY KEY,
                porcentaje REAL NOT NULL DEFAULT 0,
                actualizado TEXT
            );
            """
        )


def leer_gastos() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(
            "SELECT id, fecha, fase, tipo, monto, proveedor, descripcion FROM gastos ORDER BY fecha DESC, id DESC",
            conn,
        )


def leer_pagos() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(
            "SELECT id, fecha, concepto, monto FROM pagos_cliente ORDER BY fecha DESC, id DESC", conn
        )


def leer_avance_fisico() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT fase, porcentaje FROM avance_fisico").fetchall()
    return {fase: pct for fase, pct in rows}


def insertar_gasto(fecha: str, fase: str, tipo: str, monto: float, proveedor: str, descripcion: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO gastos (fecha, fase, tipo, monto, proveedor, descripcion) VALUES (?, ?, ?, ?, ?, ?)",
            (fecha, fase, tipo, monto, proveedor.strip(), descripcion.strip()),
        )


def insertar_pago(fecha: str, concepto: str, monto: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO pagos_cliente (fecha, concepto, monto) VALUES (?, ?, ?)",
            (fecha, concepto.strip(), monto),
        )


def eliminar_registros(tabla: str, ids: list[int]) -> None:
    if not ids:
        return
    assert tabla in ("gastos", "pagos_cliente")
    with get_conn() as conn:
        conn.executemany(f"DELETE FROM {tabla} WHERE id = ?", [(int(i),) for i in ids])


def guardar_avance_fisico(avances: dict) -> None:
    ahora = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO avance_fisico (fase, porcentaje, actualizado) VALUES (?, ?, ?)
            ON CONFLICT(fase) DO UPDATE SET porcentaje = excluded.porcentaje, actualizado = excluded.actualizado
            """,
            [(fase, pct, ahora) for fase, pct in avances.items()],
        )


init_db()

# ---------------------------------------------------------------
# ENCABEZADO
# ---------------------------------------------------------------
st.title("🏗️ Sistema de Control de Ejecución de Obra")
st.subheader("Proyecto: Construcción Vivienda Familiar Tres Niveles (JE132)")
st.caption("Cliente: José Manuel Robles Miguel | Contratistas: DACAM & HOGAR 911")

# ---------------------------------------------------------------
# PANEL LATERAL: CAPTURA
# ---------------------------------------------------------------
st.sidebar.header("📝 Captura de movimientos")

# --- Registro de gastos ---
with st.sidebar.expander("Registrar gasto de obra", expanded=True):
    fase_seleccionada = st.selectbox("Fase:", FASES + [FASE_INDIRECTOS], key="g_fase")

    # Lógica corregida: los indirectos NO se mezclan con las fases de obra.
    if fase_seleccionada == FASE_INDIRECTOS:
        tipo_gasto = FASE_INDIRECTOS
        st.caption("Los indirectos se registran fuera de las fases de obra.")
    else:
        tipo_gasto = st.selectbox("Tipo de desglose:", TIPOS_DIRECTOS, key="g_tipo")

    with st.form("form_gasto", clear_on_submit=True):
        fecha_gasto = st.date_input("Fecha del gasto:", value=datetime.now().date())
        monto_gasto = st.number_input("Monto ($ MXN):", min_value=0.0, step=500.0)
        proveedor = st.text_input("Proveedor / Beneficiario:")
        descripcion_gasto = st.text_input("Descripción (Ej. Compra de varilla, Pago de destajo):")
        if st.form_submit_button("Guardar gasto"):
            if monto_gasto <= 0:
                st.error("El monto debe ser mayor a 0.")
            elif not descripcion_gasto.strip():
                st.error("La descripción es obligatoria para la bitácora.")
            else:
                insertar_gasto(
                    fecha_gasto.isoformat(), fase_seleccionada, tipo_gasto,
                    monto_gasto, proveedor, descripcion_gasto,
                )
                st.success("¡Gasto registrado!")

# --- Registro de pagos del cliente ---
with st.sidebar.expander("Registrar pago del cliente"):
    st.caption("Anticipo, estimaciones y pagos parciales. El saldo en caja se calcula contra estos cobros, no contra el presupuesto.")
    with st.form("form_pago", clear_on_submit=True):
        fecha_pago = st.date_input("Fecha del pago:", value=datetime.now().date(), key="p_fecha")
        concepto_pago = st.text_input("Concepto (Ej. Anticipo inicial, Estimación 1):")
        monto_pago = st.number_input("Monto ($ MXN):", min_value=0.0, step=1000.0, key="p_monto")
        if st.form_submit_button("Guardar pago"):
            if monto_pago <= 0:
                st.error("El monto debe ser mayor a 0.")
            elif not concepto_pago.strip():
                st.error("El concepto es obligatorio.")
            else:
                insertar_pago(fecha_pago.isoformat(), concepto_pago, monto_pago)
                st.success("¡Pago registrado!")

# --- Avance físico ---
with st.sidebar.expander("Actualizar avance físico"):
    st.caption("Porcentaje real de ejecución en campo por fase.")
    avance_actual = leer_avance_fisico()
    with st.form("form_avance"):
        nuevos_avances = {}
        for fase in FASES:
            etiqueta = fase.split(":")[0]  # "Fase 1", "Fase 2"...
            nuevos_avances[fase] = st.slider(
                etiqueta, 0, 100, int(avance_actual.get(fase, 0)), key=f"av_{fase}"
            )
        if st.form_submit_button("Guardar avance"):
            guardar_avance_fisico({f: float(p) for f, p in nuevos_avances.items()})
            st.success("Avance actualizado.")

# ---------------------------------------------------------------
# LECTURA DE DATOS
# ---------------------------------------------------------------
df_gastos = leer_gastos()
df_pagos = leer_pagos()
avance_fisico = leer_avance_fisico()

real_materiales = df_gastos.loc[df_gastos["tipo"] == "Materiales", "monto"].sum()
real_mano_obra = df_gastos.loc[df_gastos["tipo"] == "Mano de Obra", "monto"].sum()
real_indirectos = df_gastos.loc[df_gastos["tipo"] == FASE_INDIRECTOS, "monto"].sum()
total_real = real_materiales + real_mano_obra + real_indirectos

total_cobrado = df_pagos["monto"].sum() if not df_pagos.empty else 0.0
saldo_caja = total_cobrado - total_real

# ---------------------------------------------------------------
# VISTA 1: DASHBOARD GENERAL
# ---------------------------------------------------------------
st.header("📊 Resumen Financiero del Proyecto")
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("Presupuesto Total Contratado", f"${total_presupuestado:,.2f}")
with col2:
    pct_ejercido = (total_real / total_presupuestado * 100) if total_presupuestado else 0
    st.metric("Total Ejecutado (Real)", f"${total_real:,.2f}", f"{pct_ejercido:.1f}% del presupuesto", delta_color="off")
with col3:
    pct_cobrado = (total_cobrado / total_presupuestado * 100) if total_presupuestado else 0
    st.metric("Cobrado al Cliente", f"${total_cobrado:,.2f}", f"{pct_cobrado:.1f}% del contrato", delta_color="off")
with col4:
    st.metric(
        "Saldo en Caja (Cobrado − Gastado)",
        f"${saldo_caja:,.2f}",
        delta=f"${saldo_caja:,.2f}",
        delta_color="normal",
    )

if saldo_caja < 0:
    st.warning("⚠️ El gasto ejecutado supera lo cobrado al cliente. Considera solicitar la siguiente estimación.")

st.markdown("---")

# ---------------------------------------------------------------
# VISTA 2: COMPARATIVO POR DESGLOSE PRINCIPAL
# ---------------------------------------------------------------
st.subheader("🔍 Análisis Desglosado: Presupuesto vs. Real")

tabla_comparativa = pd.DataFrame({
    "Desglose": ["Materiales (Suministros)", "Mano de Obra", "Gastos Indirectos", "TOTAL"],
    "Presupuesto Base": [p_materiales, p_mano_obra, p_indirectos, total_presupuestado],
    "Gasto Real Realizado": [real_materiales, real_mano_obra, real_indirectos, total_real],
})
tabla_comparativa["Diferencia / Desviación"] = (
    tabla_comparativa["Gasto Real Realizado"] - tabla_comparativa["Presupuesto Base"]
)
tabla_comparativa["% Ejercido"] = (
    tabla_comparativa["Gasto Real Realizado"] / tabla_comparativa["Presupuesto Base"] * 100
).fillna(0)


def _color_desviacion(v):
    return "color: #d62728; font-weight: bold" if v > 0 else "color: #2ca02c"


st.dataframe(
    tabla_comparativa.style
    .format({
        "Presupuesto Base": "${:,.2f}",
        "Gasto Real Realizado": "${:,.2f}",
        "Diferencia / Desviación": "${:,.2f}",
        "% Ejercido": "{:.1f}%",
    })
    .map(_color_desviacion, subset=["Diferencia / Desviación"]),
    **FULL_WIDTH,
)

st.markdown("---")

# ---------------------------------------------------------------
# VISTA 3: CONTROL POR FASES (FINANCIERO + FÍSICO)
# ---------------------------------------------------------------
st.subheader("📋 Control por Fases Cronológicas")

resumen_fases = []
for _, row in df_presupuesto.iterrows():
    fase_name = row["Fase"]
    en_fase = df_gastos["fase"] == fase_name
    r_mat = df_gastos.loc[en_fase & (df_gastos["tipo"] == "Materiales"), "monto"].sum()
    r_mo = df_gastos.loc[en_fase & (df_gastos["tipo"] == "Mano de Obra"), "monto"].sum()
    total_fase_real = r_mat + r_mo
    presup_fase = row["Subtotal Costo Directo"]

    resumen_fases.append({
        "Fase de Obra": fase_name,
        "Semanas Est.": row["Semanas"],
        "Presup. Materiales": row["Materiales"],
        "Real Materiales": r_mat,
        "Presup. Mano Obra": row["Mano de Obra"],
        "Real Mano Obra": r_mo,
        "Total Presupuestado": presup_fase,
        "Total Real Fase": total_fase_real,
        "% Avance Financiero": (total_fase_real / presup_fase * 100) if presup_fase else 0,
        "% Avance Físico": avance_fisico.get(fase_name, 0),
    })

df_resumen_fases = pd.DataFrame(resumen_fases)
# Si el avance financiero supera al físico por más de 10 pts, la fase gasta más rápido de lo que avanza.
df_resumen_fases["Alerta"] = df_resumen_fases.apply(
    lambda r: "🔴" if r["% Avance Financiero"] - r["% Avance Físico"] > 10 else "🟢", axis=1
)

st.dataframe(
    df_resumen_fases.style.format({
        "Presup. Materiales": "${:,.2f}",
        "Real Materiales": "${:,.2f}",
        "Presup. Mano Obra": "${:,.2f}",
        "Real Mano Obra": "${:,.2f}",
        "Total Presupuestado": "${:,.2f}",
        "Total Real Fase": "${:,.2f}",
        "% Avance Financiero": "{:.1f}%",
        "% Avance Físico": "{:.0f}%",
    }),
    **FULL_WIDTH,
)
st.caption("🔴 = el gasto avanza más de 10 puntos por encima del avance físico (posible sobrecosto o adelanto de compras).")

# Gráfica comparativa por fase
df_chart = pd.DataFrame({
    "Fase": [f.split(":")[0] for f in df_resumen_fases["Fase de Obra"]],
    "Presupuesto": df_resumen_fases["Total Presupuestado"].values,
    "Real": df_resumen_fases["Total Real Fase"].values,
}).set_index("Fase")
st.bar_chart(df_chart, **({"width": "stretch"} if _ver >= (1, 46) else {"use_container_width": True}))

st.markdown("---")

# ---------------------------------------------------------------
# VISTA 4: BITÁCORAS (GASTOS Y PAGOS) CON ELIMINACIÓN Y EXPORTACIÓN
# ---------------------------------------------------------------
st.subheader("📜 Bitácoras del Proyecto")

tab_gastos, tab_pagos = st.tabs(["Gastos y destajos", "Pagos del cliente"])

with tab_gastos:
    if df_gastos.empty:
        st.info("Aún no se han registrado gastos en el panel lateral.")
    else:
        df_editor = df_gastos.copy()
        df_editor.insert(0, "Eliminar", False)
        editado = st.data_editor(
            df_editor,
            column_config={
                "Eliminar": st.column_config.CheckboxColumn("Eliminar", help="Marca los registros erróneos"),
                "id": st.column_config.NumberColumn("Folio", disabled=True),
                "monto": st.column_config.NumberColumn("Monto", format="$%.2f", disabled=True),
            },
            disabled=["fecha", "fase", "tipo", "proveedor", "descripcion"],
            hide_index=True,
            key="editor_gastos",
            **FULL_WIDTH,
        )
        col_a, col_b = st.columns([1, 3])
        with col_a:
            if st.button("🗑️ Eliminar seleccionados", key="del_gastos"):
                ids = editado.loc[editado["Eliminar"], "id"].tolist()
                if ids:
                    eliminar_registros("gastos", ids)
                    st.rerun()
                else:
                    st.warning("No hay registros marcados.")
        with col_b:
            st.download_button(
                "⬇️ Exportar bitácora a CSV",
                df_gastos.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"bitacora_gastos_JE132_{datetime.now():%Y%m%d}.csv",
                mime="text/csv",
            )

with tab_pagos:
    if df_pagos.empty:
        st.info("Registra el anticipo y las estimaciones cobradas en el panel lateral.")
    else:
        df_editor_p = df_pagos.copy()
        df_editor_p.insert(0, "Eliminar", False)
        editado_p = st.data_editor(
            df_editor_p,
            column_config={
                "Eliminar": st.column_config.CheckboxColumn("Eliminar"),
                "id": st.column_config.NumberColumn("Folio", disabled=True),
                "monto": st.column_config.NumberColumn("Monto", format="$%.2f", disabled=True),
            },
            disabled=["fecha", "concepto"],
            hide_index=True,
            key="editor_pagos",
            **FULL_WIDTH,
        )
        if st.button("🗑️ Eliminar seleccionados", key="del_pagos"):
            ids = editado_p.loc[editado_p["Eliminar"], "id"].tolist()
            if ids:
                eliminar_registros("pagos_cliente", ids)
                st.rerun()
            else:
                st.warning("No hay registros marcados.")
