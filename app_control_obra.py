"""
Sistema de Control de Ejecución de Obra - Residencia JE132
Versión 3.0:
  - ROLES DE USUARIO:
      * Administrador (ADMIN_PASSWORD): captura gastos/pagos, elimina registros,
        actualiza avance físico y adjunta comprobantes.
      * Cliente (CLIENTE_PASSWORD): solo consulta el dashboard, sin captura ni edición.
      * Compatibilidad: si solo existe APP_PASSWORD, funciona como contraseña de admin.
      * Modo local: sin variables de entorno, abre directo como admin.
  - COMPROBANTES: foto (JPG/PNG/WebP) o PDF adjunto a cada gasto, guardado en el
    volume persistente. Las imágenes se comprimen automáticamente (máx 1600px,
    JPEG 80%) para cuidar el espacio. Visor integrado en la bitácora.
  - Todo lo de la v2: SQLite persistente, pagos del cliente, avance físico vs
    financiero con semáforo, gráficas, exportación a CSV.

Variables de entorno en Railway:
  DB_DIR           = /data          (volume montado)
  ADMIN_PASSWORD   = contraseña del equipo DACAM/HOGAR 911
  CLIENTE_PASSWORD = contraseña de solo lectura para el cliente

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

DB_DIR = Path(os.environ.get("DB_DIR", Path(__file__).parent))
DB_PATH = DB_DIR / "control_obra_je132.db"
COMPROBANTES_DIR = DB_DIR / "comprobantes"
COMPROBANTES_DIR.mkdir(parents=True, exist_ok=True)

FASE_INDIRECTOS = "Gastos Indirectos"
TIPOS_DIRECTOS = ["Materiales", "Mano de Obra"]
EXTENSIONES_IMAGEN = {".jpg", ".jpeg", ".png", ".webp"}

# Compatibilidad de ancho entre versiones de Streamlit (>=1.46 usa width="stretch")
try:
    _ver = tuple(int(x) for x in st.__version__.split(".")[:2])
except Exception:
    _ver = (0, 0)
FULL_WIDTH = {"width": "stretch"} if _ver >= (1, 46) else {"use_container_width": True}


# ---------------------------------------------------------------
# CONTROL DE ACCESO Y ROLES
# ---------------------------------------------------------------
def determinar_rol(pwd: str) -> str | None:
    """Devuelve 'admin', 'cliente' o None según la contraseña ingresada."""
    admin_pwd = os.environ.get("ADMIN_PASSWORD") or os.environ.get("APP_PASSWORD", "")
    cliente_pwd = os.environ.get("CLIENTE_PASSWORD", "")
    if admin_pwd and pwd == admin_pwd:
        return "admin"
    if cliente_pwd and pwd == cliente_pwd:
        return "cliente"
    return None


def verificar_acceso() -> bool:
    hay_passwords = bool(
        os.environ.get("ADMIN_PASSWORD") or os.environ.get("APP_PASSWORD") or os.environ.get("CLIENTE_PASSWORD")
    )
    if not hay_passwords:
        st.session_state.rol = "admin"  # Modo local/desarrollo
        return True

    if st.session_state.get("rol"):
        return True

    st.title("🔒 Control de Obra - Residencia JE132")
    st.caption("DACAM & HOGAR 911 | Acceso restringido")
    with st.form("form_login"):
        pwd = st.text_input("Contraseña de acceso:", type="password")
        entrar = st.form_submit_button("Entrar")
    if entrar:
        rol = determinar_rol(pwd)
        if rol:
            st.session_state.rol = rol
            st.rerun()
        else:
            st.error("Contraseña incorrecta.")
    return False


if not verificar_acceso():
    st.stop()

ES_ADMIN = st.session_state.get("rol") == "admin"


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


INDIRECTOS = {
    "Proyecto Arquitectónico, Dirección y Supervisión": 125000.0,
    "Gestión Administrativa y Control": 112284.0,
}

df_presupuesto = obtener_presupuesto_base()
FASES = df_presupuesto["Fase"].tolist()

p_materiales = df_presupuesto["Materiales"].sum()
p_mano_obra = df_presupuesto["Mano de Obra"].sum()
p_indirectos = sum(INDIRECTOS.values())
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
        # Migración v3: columna de comprobante en gastos (si la BD viene de la v2)
        columnas = [c[1] for c in conn.execute("PRAGMA table_info(gastos)").fetchall()]
        if "comprobante" not in columnas:
            conn.execute("ALTER TABLE gastos ADD COLUMN comprobante TEXT")


def leer_gastos() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(
            "SELECT id, fecha, fase, tipo, monto, proveedor, descripcion, comprobante "
            "FROM gastos ORDER BY fecha DESC, id DESC",
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


def insertar_gasto(fecha: str, fase: str, tipo: str, monto: float, proveedor: str, descripcion: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO gastos (fecha, fase, tipo, monto, proveedor, descripcion) VALUES (?, ?, ?, ?, ?, ?)",
            (fecha, fase, tipo, monto, proveedor.strip(), descripcion.strip()),
        )
        return cur.lastrowid


def guardar_comprobante(gasto_id: int, archivo) -> str | None:
    """Guarda la foto o PDF del comprobante en el volume. Comprime imágenes grandes."""
    ext = Path(archivo.name).suffix.lower()
    if ext not in EXTENSIONES_IMAGEN and ext != ".pdf":
        return None
    nombre = f"gasto_{gasto_id}{ext}"
    destino = COMPROBANTES_DIR / nombre

    if ext in EXTENSIONES_IMAGEN:
        try:
            from PIL import Image

            img = Image.open(archivo)
            img.thumbnail((1600, 1600))  # Reduce fotos de celular (~4000px) sin perder legibilidad
            if ext in (".jpg", ".jpeg") and img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(destino, quality=80, optimize=True)
        except Exception:
            destino.write_bytes(archivo.getbuffer())  # Si falla la compresión, guarda original
    else:
        destino.write_bytes(archivo.getbuffer())

    with get_conn() as conn:
        conn.execute("UPDATE gastos SET comprobante = ? WHERE id = ?", (nombre, gasto_id))
    return nombre


def insertar_pago(fecha: str, concepto: str, monto: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO pagos_cliente (fecha, concepto, monto) VALUES (?, ?, ?)",
            (fecha, concepto.strip(), monto),
        )


def eliminar_gastos(ids: list[int]) -> None:
    """Elimina gastos y sus archivos de comprobante asociados."""
    if not ids:
        return
    with get_conn() as conn:
        for gid in ids:
            row = conn.execute("SELECT comprobante FROM gastos WHERE id = ?", (int(gid),)).fetchone()
            if row and row[0]:
                (COMPROBANTES_DIR / row[0]).unlink(missing_ok=True)
            conn.execute("DELETE FROM gastos WHERE id = ?", (int(gid),))


def eliminar_pagos(ids: list[int]) -> None:
    if not ids:
        return
    with get_conn() as conn:
        conn.executemany("DELETE FROM pagos_cliente WHERE id = ?", [(int(i),) for i in ids])


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
# PANEL LATERAL
# ---------------------------------------------------------------
rol_texto = "👷 Administrador" if ES_ADMIN else "👤 Cliente (solo consulta)"
st.sidebar.markdown(f"**Sesión:** {rol_texto}")
if os.environ.get("ADMIN_PASSWORD") or os.environ.get("APP_PASSWORD") or os.environ.get("CLIENTE_PASSWORD"):
    if st.sidebar.button("Cerrar sesión"):
        st.session_state.pop("rol", None)
        st.session_state.pop("autenticado", None)
        st.rerun()
st.sidebar.markdown("---")

if ES_ADMIN:
    st.sidebar.header("📝 Captura de movimientos")

    # --- Registro de gastos (con comprobante) ---
    with st.sidebar.expander("Registrar gasto de obra", expanded=True):
        fase_seleccionada = st.selectbox("Fase:", FASES + [FASE_INDIRECTOS], key="g_fase")

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
            archivo_comprobante = st.file_uploader(
                "Comprobante (foto de nota, factura o PDF):",
                type=["jpg", "jpeg", "png", "webp", "pdf"],
            )
            if st.form_submit_button("Guardar gasto"):
                if monto_gasto <= 0:
                    st.error("El monto debe ser mayor a 0.")
                elif not descripcion_gasto.strip():
                    st.error("La descripción es obligatoria para la bitácora.")
                else:
                    nuevo_id = insertar_gasto(
                        fecha_gasto.isoformat(), fase_seleccionada, tipo_gasto,
                        monto_gasto, proveedor, descripcion_gasto,
                    )
                    if archivo_comprobante is not None:
                        guardar_comprobante(nuevo_id, archivo_comprobante)
                        st.success(f"¡Gasto #{nuevo_id} registrado con comprobante! 📎")
                    else:
                        st.success(f"¡Gasto #{nuevo_id} registrado!")

    # --- Registro de pagos del cliente ---
    with st.sidebar.expander("Registrar pago del cliente"):
        st.caption("Anticipo, estimaciones y pagos parciales. El saldo en caja se calcula contra estos cobros.")
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
        avance_actual_form = leer_avance_fisico()
        with st.form("form_avance"):
            nuevos_avances = {}
            for fase in FASES:
                etiqueta = fase.split(":")[0]
                nuevos_avances[fase] = st.slider(
                    etiqueta, 0, 100, int(avance_actual_form.get(fase, 0)), key=f"av_{fase}"
                )
            if st.form_submit_button("Guardar avance"):
                guardar_avance_fisico({f: float(p) for f, p in nuevos_avances.items()})
                st.success("Avance actualizado.")
else:
    st.sidebar.info("Estás en modo consulta. Puedes revisar el avance financiero, físico y los comprobantes del proyecto.")

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

if saldo_caja < 0 and ES_ADMIN:
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

df_chart = pd.DataFrame({
    "Fase": [f.split(":")[0] for f in df_resumen_fases["Fase de Obra"]],
    "Presupuesto": df_resumen_fases["Total Presupuestado"].values,
    "Real": df_resumen_fases["Total Real Fase"].values,
}).set_index("Fase")
st.bar_chart(df_chart, **FULL_WIDTH)

st.markdown("---")

# ---------------------------------------------------------------
# VISTA 4: BITÁCORAS (GASTOS Y PAGOS)
# ---------------------------------------------------------------
st.subheader("📜 Bitácoras del Proyecto")

tab_gastos, tab_pagos = st.tabs(["Gastos y destajos", "Pagos del cliente"])

with tab_gastos:
    if df_gastos.empty:
        st.info("Aún no se han registrado gastos.")
    else:
        df_vista = df_gastos.copy()
        df_vista["comprobante"] = df_vista["comprobante"].apply(lambda c: "📎 Sí" if c else "—")

        if ES_ADMIN:
            df_vista.insert(0, "Eliminar", False)
            editado = st.data_editor(
                df_vista,
                column_config={
                    "Eliminar": st.column_config.CheckboxColumn("Eliminar", help="Marca los registros erróneos"),
                    "id": st.column_config.NumberColumn("Folio", disabled=True),
                    "monto": st.column_config.NumberColumn("Monto", format="$%.2f", disabled=True),
                    "comprobante": st.column_config.TextColumn("Comprobante", disabled=True),
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
                        eliminar_gastos(ids)
                        st.rerun()
                    else:
                        st.warning("No hay registros marcados.")
            with col_b:
                st.download_button(
                    "⬇️ Exportar bitácora a CSV",
                    df_gastos.drop(columns=["comprobante"]).to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"bitacora_gastos_JE132_{datetime.now():%Y%m%d}.csv",
                    mime="text/csv",
                )
        else:
            st.dataframe(
                df_vista.rename(columns={"id": "Folio", "monto": "Monto", "comprobante": "Comprobante"})
                .style.format({"Monto": "${:,.2f}"}),
                hide_index=True,
                **FULL_WIDTH,
            )

        # --- Visor de comprobantes (ambos roles) ---
        con_comprobante = df_gastos[df_gastos["comprobante"].notna() & (df_gastos["comprobante"] != "")]
        if not con_comprobante.empty:
            with st.expander("🔍 Ver comprobante de un gasto"):
                opciones = {
                    f"Folio {r['id']} | {r['fecha']} | ${r['monto']:,.2f} | {r['descripcion'][:40]}": r
                    for _, r in con_comprobante.iterrows()
                }
                seleccion = st.selectbox("Selecciona el gasto:", list(opciones.keys()))
                registro = opciones[seleccion]
                ruta = COMPROBANTES_DIR / registro["comprobante"]
                if not ruta.exists():
                    st.error("El archivo del comprobante no se encontró en el almacenamiento.")
                elif ruta.suffix.lower() in EXTENSIONES_IMAGEN:
                    st.image(str(ruta), caption=f"Comprobante del folio {registro['id']}", **FULL_WIDTH)
                    st.download_button(
                        "⬇️ Descargar imagen", ruta.read_bytes(),
                        file_name=ruta.name, key=f"dl_{registro['id']}",
                    )
                else:
                    st.download_button(
                        "⬇️ Descargar comprobante PDF", ruta.read_bytes(),
                        file_name=ruta.name, mime="application/pdf", key=f"dl_{registro['id']}",
                    )

with tab_pagos:
    if df_pagos.empty:
        st.info("Aún no hay pagos del cliente registrados.")
    else:
        if ES_ADMIN:
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
                    eliminar_pagos(ids)
                    st.rerun()
                else:
                    st.warning("No hay registros marcados.")
        else:
            st.dataframe(
                df_pagos.rename(columns={"id": "Folio", "monto": "Monto"})
                .style.format({"Monto": "${:,.2f}"}),
                hide_index=True,
                **FULL_WIDTH,
            )

# ---------------------------------------------------------------
# VISTA 5: GENERACIÓN DE INFORMES (PDF Y EXCEL)
# ---------------------------------------------------------------
st.markdown("---")
st.subheader("📄 Informes del Proyecto")


def _dinero(v: float) -> str:
    return f"${v:,.2f}"


def generar_informe_pdf() -> bytes:
    """Informe ejecutivo en PDF: resumen, desglose, fases y bitácoras."""
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        title="Informe de Control de Obra JE132",
    )
    styles = getSampleStyleSheet()
    titulo = ParagraphStyle("titulo", parent=styles["Title"], fontSize=16, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=10, textColor=colors.grey)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, spaceBefore=14, spaceAfter=6)
    chico = ParagraphStyle("chico", parent=styles["Normal"], fontSize=8)

    estilo_tabla = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5f9")]),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ])

    elementos = [
        Paragraph("Informe de Control de Ejecución de Obra", titulo),
        Paragraph("Proyecto: Construcción Vivienda Familiar Tres Niveles (JE132)", styles["Normal"]),
        Paragraph("Cliente: José Manuel Robles Miguel | Contratistas: DACAM & HOGAR 911", sub),
        Paragraph(f"Fecha de emisión: {datetime.now():%d/%m/%Y %H:%M}", sub),
        Spacer(1, 10),
    ]

    # --- Resumen financiero ---
    elementos.append(Paragraph("1. Resumen Financiero", h2))
    t_resumen = Table([
        ["Concepto", "Monto"],
        ["Presupuesto Total Contratado", _dinero(total_presupuestado)],
        ["Total Ejecutado (Real)", f"{_dinero(total_real)}  ({pct_ejercido:.1f}%)"],
        ["Cobrado al Cliente", f"{_dinero(total_cobrado)}  ({pct_cobrado:.1f}%)"],
        ["Saldo en Caja (Cobrado - Gastado)", _dinero(saldo_caja)],
    ], colWidths=[10 * cm, 7 * cm])
    t_resumen.setStyle(estilo_tabla)
    elementos.append(t_resumen)

    # --- Desglose ---
    elementos.append(Paragraph("2. Desglose: Presupuesto vs. Real", h2))
    filas_desglose = [["Desglose", "Presupuesto", "Real", "Desviación", "% Ejercido"]]
    for _, r in tabla_comparativa.iterrows():
        filas_desglose.append([
            r["Desglose"], _dinero(r["Presupuesto Base"]), _dinero(r["Gasto Real Realizado"]),
            _dinero(r["Diferencia / Desviación"]), f"{r['% Ejercido']:.1f}%",
        ])
    t_desglose = Table(filas_desglose, colWidths=[6 * cm, 3.2 * cm, 3.2 * cm, 3.2 * cm, 2 * cm])
    t_desglose.setStyle(estilo_tabla)
    elementos.append(t_desglose)

    # --- Fases ---
    elementos.append(Paragraph("3. Control por Fases", h2))
    filas_fases = [["Fase", "Presupuesto", "Real", "% Financ.", "% Físico", "Estado"]]
    for _, r in df_resumen_fases.iterrows():
        estado = "ATENCIÓN" if r["Alerta"] == "🔴" else "OK"
        filas_fases.append([
            Paragraph(r["Fase de Obra"], chico), _dinero(r["Total Presupuestado"]),
            _dinero(r["Total Real Fase"]), f"{r['% Avance Financiero']:.1f}%",
            f"{r['% Avance Físico']:.0f}%", estado,
        ])
    t_fases = Table(filas_fases, colWidths=[6.5 * cm, 3 * cm, 3 * cm, 1.9 * cm, 1.7 * cm, 1.7 * cm])
    t_fases.setStyle(estilo_tabla)
    elementos.append(t_fases)
    elementos.append(Paragraph(
        "ATENCIÓN = el avance financiero supera al físico por más de 10 puntos "
        "(posible sobrecosto o adelanto de compras).", chico))

    # --- Bitácora de gastos ---
    elementos.append(Paragraph("4. Bitácora de Gastos", h2))
    if df_gastos.empty:
        elementos.append(Paragraph("Sin gastos registrados.", styles["Normal"]))
    else:
        filas_g = [["Folio", "Fecha", "Fase", "Tipo", "Monto", "Proveedor", "Descripción"]]
        for _, r in df_gastos.sort_values(["fecha", "id"]).iterrows():
            filas_g.append([
                str(r["id"]), r["fecha"], Paragraph(r["fase"].split(":")[0], chico),
                Paragraph(r["tipo"], chico), _dinero(r["monto"]),
                Paragraph(str(r["proveedor"] or ""), chico), Paragraph(str(r["descripcion"]), chico),
            ])
        t_g = Table(filas_g, colWidths=[1.2 * cm, 2 * cm, 1.9 * cm, 2.4 * cm, 2.4 * cm, 3.3 * cm, 4.6 * cm], repeatRows=1)
        t_g.setStyle(estilo_tabla)
        elementos.append(t_g)

    # --- Pagos del cliente ---
    elementos.append(Paragraph("5. Pagos del Cliente", h2))
    if df_pagos.empty:
        elementos.append(Paragraph("Sin pagos registrados.", styles["Normal"]))
    else:
        filas_p = [["Folio", "Fecha", "Concepto", "Monto"]]
        for _, r in df_pagos.sort_values(["fecha", "id"]).iterrows():
            filas_p.append([str(r["id"]), r["fecha"], Paragraph(str(r["concepto"]), chico), _dinero(r["monto"])])
        filas_p.append(["", "", "TOTAL COBRADO", _dinero(total_cobrado)])
        t_p = Table(filas_p, colWidths=[1.5 * cm, 2.5 * cm, 9 * cm, 4 * cm], repeatRows=1)
        t_p.setStyle(estilo_tabla)
        elementos.append(t_p)

    elementos.append(Spacer(1, 16))
    elementos.append(Paragraph(
        f"Documento generado automáticamente por el Sistema de Control de Obra "
        f"(control.hogar911.com) el {datetime.now():%d/%m/%Y a las %H:%M}.", chico))

    doc.build(elementos)
    return buf.getvalue()


def generar_informe_excel() -> bytes:
    """Informe en Excel con hojas: Resumen, Desglose, Fases, Gastos y Pagos."""
    from io import BytesIO

    buf = BytesIO()
    df_resumen = pd.DataFrame({
        "Concepto": [
            "Presupuesto Total Contratado", "Total Ejecutado (Real)",
            "Cobrado al Cliente", "Saldo en Caja (Cobrado - Gastado)",
        ],
        "Monto": [total_presupuestado, total_real, total_cobrado, saldo_caja],
    })
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_resumen.to_excel(writer, sheet_name="Resumen", index=False)
        tabla_comparativa.to_excel(writer, sheet_name="Desglose", index=False)
        df_resumen_fases.drop(columns=["Alerta"]).to_excel(writer, sheet_name="Fases", index=False)
        df_gastos.drop(columns=["comprobante"]).to_excel(writer, sheet_name="Gastos", index=False)
        df_pagos.to_excel(writer, sheet_name="Pagos", index=False)
    return buf.getvalue()


col_pdf, col_xls = st.columns(2)
fecha_archivo = f"{datetime.now():%Y%m%d}"
with col_pdf:
    try:
        st.download_button(
            "📄 Descargar Informe Ejecutivo (PDF)",
            generar_informe_pdf(),
            file_name=f"informe_obra_JE132_{fecha_archivo}.pdf",
            mime="application/pdf",
            **FULL_WIDTH,
        )
        st.caption("Ideal para enviar al cliente o imprimir: resumen, fases y bitácoras.")
    except ImportError:
        st.error("Falta la librería reportlab. Agrega 'reportlab' al requirements.txt.")
with col_xls:
    try:
        st.download_button(
            "📊 Descargar Informe en Excel",
            generar_informe_excel(),
            file_name=f"informe_obra_JE132_{fecha_archivo}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            **FULL_WIDTH,
        )
        st.caption("Hojas separadas: Resumen, Desglose, Fases, Gastos y Pagos.")
    except ImportError:
        st.error("Falta la librería openpyxl. Agrega 'openpyxl' al requirements.txt.")

# ---------------------------------------------------------------
# VISTA 6: ADMINISTRACIÓN DE LA BASE DE DATOS (SOLO ADMIN)
# ---------------------------------------------------------------
if ES_ADMIN:
    st.markdown("---")
    st.subheader("🗄️ Administración de la Base de Datos")
    st.caption(
        "La base de datos vive en el volume persistente de Railway. Desde aquí puedes "
        "respaldarla, explorarla, consultarla con SQL y restaurarla."
    )

    tab_resp, tab_expl, tab_sql, tab_rest = st.tabs(
        ["💾 Respaldo", "🔎 Explorador", "⌨️ Consola SQL", "♻️ Restaurar"]
    )

    # --- Respaldo ---
    with tab_resp:
        # Consolidar el WAL para que el archivo .db contenga todos los datos al copiarse
        with get_conn() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            n_gastos = conn.execute("SELECT COUNT(*) FROM gastos").fetchone()[0]
            n_pagos = conn.execute("SELECT COUNT(*) FROM pagos_cliente").fetchone()[0]

        tam_db = DB_PATH.stat().st_size / 1024
        archivos_comp = list(COMPROBANTES_DIR.glob("*")) if COMPROBANTES_DIR.exists() else []
        tam_comp = sum(f.stat().st_size for f in archivos_comp) / 1024

        c1, c2, c3 = st.columns(3)
        c1.metric("Registros", f"{n_gastos} gastos / {n_pagos} pagos")
        c2.metric("Base de datos", f"{tam_db:,.0f} KB")
        c3.metric("Comprobantes", f"{len(archivos_comp)} archivos ({tam_comp:,.0f} KB)")

        col_r1, col_r2 = st.columns(2)
        with col_r1:
            st.download_button(
                "💾 Descargar base de datos (.db)",
                DB_PATH.read_bytes(),
                file_name=f"respaldo_JE132_{datetime.now():%Y%m%d_%H%M}.db",
                mime="application/octet-stream",
                **FULL_WIDTH,
            )
            st.caption("Solo los datos. Se abre con cualquier visor de SQLite.")
        with col_r2:
            import io
            import zipfile

            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(DB_PATH, DB_PATH.name)
                for f in archivos_comp:
                    zf.write(f, f"comprobantes/{f.name}")
            st.download_button(
                "📦 Descargar respaldo completo (.zip)",
                zip_buf.getvalue(),
                file_name=f"respaldo_completo_JE132_{datetime.now():%Y%m%d_%H%M}.zip",
                mime="application/zip",
                **FULL_WIDTH,
            )
            st.caption("Datos + comprobantes. Recomendado como respaldo mensual.")

    # --- Explorador de tablas ---
    with tab_expl:
        with get_conn() as conn:
            tablas = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
        tabla_sel = st.selectbox("Tabla:", tablas)
        with get_conn() as conn:
            df_tabla = pd.read_sql_query(f"SELECT * FROM {tabla_sel}", conn)  # noqa: S608 (nombre validado contra sqlite_master)
        st.dataframe(df_tabla, hide_index=True, **FULL_WIDTH)
        st.caption(f"{len(df_tabla)} registros en '{tabla_sel}'.")

    # --- Consola SQL (solo lectura) ---
    with tab_sql:
        st.caption("Solo consultas SELECT. La conexión se abre en modo lectura, así que no puede modificar datos.")
        consulta = st.text_area(
            "Consulta SQL:",
            value="SELECT fase, tipo, SUM(monto) AS total\nFROM gastos\nGROUP BY fase, tipo\nORDER BY total DESC;",
            height=120,
        )
        if st.button("▶️ Ejecutar consulta"):
            limpia = consulta.strip().rstrip(";").strip()
            if not limpia.lower().startswith("select"):
                st.error("Solo se permiten consultas SELECT.")
            elif ";" in limpia:
                st.error("Solo se permite una consulta a la vez.")
            else:
                try:
                    conn_ro = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
                    df_q = pd.read_sql_query(limpia, conn_ro)
                    conn_ro.close()
                    st.dataframe(df_q, hide_index=True, **FULL_WIDTH)
                    st.caption(f"{len(df_q)} resultados.")
                except Exception as e:
                    st.error(f"Error en la consulta: {e}")

    # --- Restaurar desde respaldo ---
    with tab_rest:
        st.warning(
            "⚠️ Restaurar reemplaza TODOS los datos actuales por los del respaldo. "
            "Antes de reemplazar, se guarda automáticamente una copia de seguridad de la base actual."
        )
        archivo_db = st.file_uploader("Archivo de respaldo (.db):", type=["db"])
        confirmar = st.checkbox("Entiendo que los datos actuales serán reemplazados.")
        if st.button("♻️ Restaurar base de datos", disabled=archivo_db is None or not confirmar):
            contenido = archivo_db.getvalue()
            if not contenido.startswith(b"SQLite format 3\x00"):
                st.error("El archivo no es una base de datos SQLite válida.")
            else:
                # Copia de seguridad de la base actual antes de reemplazar
                with get_conn() as conn:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                copia = DB_DIR / f"pre_restauracion_{datetime.now():%Y%m%d_%H%M%S}.db"
                copia.write_bytes(DB_PATH.read_bytes())
                # Eliminar archivos WAL/SHM viejos y escribir la nueva base
                for sufijo in ("-wal", "-shm"):
                    Path(str(DB_PATH) + sufijo).unlink(missing_ok=True)
                DB_PATH.write_bytes(contenido)
                st.success(f"Base restaurada. Copia de seguridad previa guardada como {copia.name}.")
                st.rerun()
