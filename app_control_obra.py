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

# --- Catálogo de materiales (compras) ---
UNIDADES = ["pza", "m", "m2", "m3", "ml", "kg", "ton", "saco", "bulto", "lt", "cubeta",
            "rollo", "hoja", "tramo", "caja", "juego", "lote", "viaje", "día"]
CATEGORIAS = ["Material de construcción", "Renta de madera y cimbra", "Instalaciones", "Acarreos y otros"]

# Catálogo precargado. Se guarda en la base de datos la primera vez y desde la app
# se pueden agregar materiales nuevos que quedan disponibles para futuras requisiciones.
CATALOGO_BASE = [
    # --- Material de construcción ---
    ("Arena", "m3", "Material de construcción"),
    ("Grava 3/4\"", "m3", "Material de construcción"),
    ("Grava 1/2\"", "m3", "Material de construcción"),
    ("Piedra braza", "m3", "Material de construcción"),
    ("Block de cemento macizo 12x20x40 cm", "pza", "Material de construcción"),
    ("Block de cemento hueco 15x20x40 cm", "pza", "Material de construcción"),
    ("Tabique rojo recocido", "pza", "Material de construcción"),
    ("Varilla 3/8\" AR-42", "pza", "Material de construcción"),
    ("Varilla 1/2\" AR-42", "pza", "Material de construcción"),
    ("Varilla 5/8\" AR-42", "pza", "Material de construcción"),
    ("Anillos de 1/4\" (alambrón) 20x20", "kg", "Material de construcción"),
    ("Anillos de 1/4\" (alambrón) 15x15", "kg", "Material de construcción"),
    ("Castillo armado 10x10 (Armex)", "pza", "Material de construcción"),
    ("Castillo armado 10x15 (Armex)", "pza", "Material de construcción"),
    ("Castillo armado 15x15 (Armex)", "pza", "Material de construcción"),
    ("Alambre recocido", "kg", "Material de construcción"),
    ("Clavo de 2 1/2\"", "kg", "Material de construcción"),
    ("Clavo de 4\"", "kg", "Material de construcción"),
    ("Cemento gris (bulto 50 kg)", "bulto", "Material de construcción"),
    ("Cemento blanco (bulto)", "bulto", "Material de construcción"),
    ("Cal hidratada (bulto 25 kg)", "bulto", "Material de construcción"),
    ("Mortero (bulto 50 kg)", "bulto", "Material de construcción"),
    ("Yeso (bulto 40 kg)", "bulto", "Material de construcción"),
    ("Malla electrosoldada 6-6/10-10", "rollo", "Material de construcción"),
    ("Impermeabilizante acrílico", "cubeta", "Material de construcción"),
    ("Adhesivo para block / pegapiso", "bulto", "Material de construcción"),
    ("Aditivo / acelerante para concreto", "lt", "Material de construcción"),
    # --- Renta de madera y cimbra ---
    ("Tarima de 0.50 x 1.00 m", "pza", "Renta de madera y cimbra"),
    ("Tramo de polín (0.80 a 1.00 m)", "pza", "Renta de madera y cimbra"),
    ("Polín de 2.50 m de largo", "pza", "Renta de madera y cimbra"),
    ("Tabla de 30 cm x 3/4\"", "pza", "Renta de madera y cimbra"),
    ("Barrote", "pza", "Renta de madera y cimbra"),
    ("Triplay 16 mm", "hoja", "Renta de madera y cimbra"),
    ("Puntal metálico", "pza", "Renta de madera y cimbra"),
    # --- Instalaciones ---
    ("Tubo PVC sanitario 4\"", "tramo", "Instalaciones"),
    ("Tubo PVC sanitario 2\"", "tramo", "Instalaciones"),
    ("Tubo CPVC 1/2\"", "tramo", "Instalaciones"),
    ("Poliducto 1/2\"", "rollo", "Instalaciones"),
    ("Cable THW calibre 12", "rollo", "Instalaciones"),
    ("Chalupa / caja eléctrica", "pza", "Instalaciones"),
    ("Conexiones y codos (juego)", "juego", "Instalaciones"),
    # --- Acarreos y otros ---
    ("Flete / acarreo de material", "viaje", "Acarreos y otros"),
    ("Retiro de escombro", "viaje", "Acarreos y otros"),
    ("Renta de revolvedora", "día", "Acarreos y otros"),
    ("Renta de vibrador para concreto", "día", "Acarreos y otros"),
]

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
    """Devuelve 'admin', 'residente', 'cliente' o None según la contraseña ingresada."""
    admin_pwd = os.environ.get("ADMIN_PASSWORD") or os.environ.get("APP_PASSWORD", "")
    residente_pwd = os.environ.get("RESIDENTE_PASSWORD", "")
    cliente_pwd = os.environ.get("CLIENTE_PASSWORD", "")
    if admin_pwd and pwd == admin_pwd:
        return "admin"
    if residente_pwd and pwd == residente_pwd:
        return "residente"
    if cliente_pwd and pwd == cliente_pwd:
        return "cliente"
    return None


def verificar_acceso() -> bool:
    hay_passwords = bool(
        os.environ.get("ADMIN_PASSWORD") or os.environ.get("APP_PASSWORD")
        or os.environ.get("RESIDENTE_PASSWORD") or os.environ.get("CLIENTE_PASSWORD")
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
ES_RESIDENTE = st.session_state.get("rol") == "residente"


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
            CREATE TABLE IF NOT EXISTS requisiciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                fase TEXT NOT NULL,
                solicitante TEXT NOT NULL,
                fecha_requerida TEXT,
                notas TEXT
            );
            CREATE TABLE IF NOT EXISTS requisicion_partidas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requisicion_id INTEGER NOT NULL,
                cantidad REAL NOT NULL,
                unidad TEXT NOT NULL,
                descripcion TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cotizaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requisicion_id INTEGER NOT NULL,
                proveedor TEXT NOT NULL,
                fecha TEXT NOT NULL,
                tiempo_entrega TEXT,
                condiciones_pago TEXT
            );
            CREATE TABLE IF NOT EXISTS cotizacion_precios (
                cotizacion_id INTEGER NOT NULL,
                partida_id INTEGER NOT NULL,
                precio_unitario REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (cotizacion_id, partida_id)
            );
            CREATE TABLE IF NOT EXISTS ordenes_compra (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requisicion_id INTEGER NOT NULL,
                cotizacion_id INTEGER NOT NULL,
                fecha TEXT NOT NULL,
                subtotal REAL NOT NULL,
                iva REAL NOT NULL DEFAULT 0,
                total REAL NOT NULL,
                gasto_id INTEGER
            );
            """
        )
        # Migración v3: columna de comprobante en gastos (si la BD viene de la v2)
        columnas = [c[1] for c in conn.execute("PRAGMA table_info(gastos)").fetchall()]
        if "comprobante" not in columnas:
            conn.execute("ALTER TABLE gastos ADD COLUMN comprobante TEXT")
        # Migración v5: formato de requisición (obra/ubicación, categoría y observaciones por partida)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS catalogo_materiales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                descripcion TEXT NOT NULL UNIQUE,
                unidad TEXT NOT NULL,
                categoria TEXT NOT NULL
            )
            """
        )
        cols_req = [c[1] for c in conn.execute("PRAGMA table_info(requisiciones)").fetchall()]
        if "obra" not in cols_req:
            conn.execute("ALTER TABLE requisiciones ADD COLUMN obra TEXT")
            conn.execute("ALTER TABLE requisiciones ADD COLUMN ubicacion TEXT")
        cols_part = [c[1] for c in conn.execute("PRAGMA table_info(requisicion_partidas)").fetchall()]
        if "categoria" not in cols_part:
            conn.execute("ALTER TABLE requisicion_partidas ADD COLUMN categoria TEXT DEFAULT 'Material de construcción'")
            conn.execute("ALTER TABLE requisicion_partidas ADD COLUMN observaciones TEXT")
        # Sembrar el catálogo la primera vez
        if conn.execute("SELECT COUNT(*) FROM catalogo_materiales").fetchone()[0] == 0:
            conn.executemany(
                "INSERT OR IGNORE INTO catalogo_materiales (descripcion, unidad, categoria) VALUES (?, ?, ?)",
                CATALOGO_BASE,
            )


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


def actualizar_gastos(cambios: list[tuple]) -> None:
    """Cada cambio: (fecha, fase, tipo, monto, proveedor, descripcion, id)."""
    if not cambios:
        return
    with get_conn() as conn:
        conn.executemany(
            "UPDATE gastos SET fecha=?, fase=?, tipo=?, monto=?, proveedor=?, descripcion=? WHERE id=?",
            cambios,
        )


def actualizar_pagos(cambios: list[tuple]) -> None:
    """Cada cambio: (fecha, concepto, monto, id)."""
    if not cambios:
        return
    with get_conn() as conn:
        conn.executemany(
            "UPDATE pagos_cliente SET fecha=?, concepto=?, monto=? WHERE id=?",
            cambios,
        )


# --- Requisiciones, cotizaciones y órdenes de compra ---
def crear_requisicion(fecha: str, fase: str, solicitante: str, fecha_requerida: str, notas: str,
                      partidas: list[dict], obra: str = "", ubicacion: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO requisiciones (fecha, fase, solicitante, fecha_requerida, notas, obra, ubicacion) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fecha, fase, solicitante.strip(), fecha_requerida, notas.strip(), obra.strip(), ubicacion.strip()),
        )
        rid = cur.lastrowid
        conn.executemany(
            "INSERT INTO requisicion_partidas (requisicion_id, cantidad, unidad, descripcion, categoria, observaciones) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(rid, p["cantidad"], p["unidad"], p["descripcion"], p["categoria"], p.get("observaciones", ""))
             for p in partidas],
        )
        return rid


def leer_catalogo() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(
            "SELECT id, descripcion, unidad, categoria FROM catalogo_materiales ORDER BY categoria, descripcion", conn
        )


def agregar_material_catalogo(descripcion: str, unidad: str, categoria: str) -> bool:
    """Agrega un material al catálogo. Devuelve False si ya existía."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO catalogo_materiales (descripcion, unidad, categoria) VALUES (?, ?, ?)",
                (descripcion.strip(), unidad, categoria),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def leer_requisiciones() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(
            """
            SELECT r.id, r.fecha, r.fase, r.solicitante, r.fecha_requerida, r.notas, r.obra, r.ubicacion,
                   (SELECT COUNT(*) FROM requisicion_partidas p WHERE p.requisicion_id = r.id) AS partidas,
                   (SELECT COUNT(*) FROM cotizaciones c WHERE c.requisicion_id = r.id) AS cotizaciones,
                   (SELECT COUNT(*) FROM ordenes_compra o WHERE o.requisicion_id = r.id) AS ocs
            FROM requisiciones r ORDER BY r.id DESC
            """,
            conn,
        )


def leer_partidas(req_id: int) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(
            "SELECT id, cantidad, unidad, descripcion, "
            "COALESCE(categoria, 'Material de construcción') AS categoria, "
            "COALESCE(observaciones, '') AS observaciones "
            "FROM requisicion_partidas WHERE requisicion_id = ? ORDER BY id",
            conn, params=(int(req_id),),
        )


def eliminar_requisicion(req_id: int) -> None:
    with get_conn() as conn:
        cot_ids = [r[0] for r in conn.execute("SELECT id FROM cotizaciones WHERE requisicion_id = ?", (int(req_id),))]
        if cot_ids:
            conn.executemany("DELETE FROM cotizacion_precios WHERE cotizacion_id = ?", [(c,) for c in cot_ids])
        conn.execute("DELETE FROM cotizaciones WHERE requisicion_id = ?", (int(req_id),))
        conn.execute("DELETE FROM requisicion_partidas WHERE requisicion_id = ?", (int(req_id),))
        conn.execute("DELETE FROM requisiciones WHERE id = ?", (int(req_id),))


def crear_cotizacion(req_id: int, proveedor: str, fecha: str, entrega: str, pago: str, precios: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO cotizaciones (requisicion_id, proveedor, fecha, tiempo_entrega, condiciones_pago) VALUES (?, ?, ?, ?, ?)",
            (int(req_id), proveedor.strip(), fecha, entrega.strip(), pago.strip()),
        )
        cid = cur.lastrowid
        conn.executemany(
            "INSERT INTO cotizacion_precios (cotizacion_id, partida_id, precio_unitario) VALUES (?, ?, ?)",
            [(cid, int(pid), float(pu)) for pid, pu in precios.items()],
        )
        return cid


def leer_cotizaciones(req_id: int) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(
            "SELECT id, proveedor, fecha, tiempo_entrega, condiciones_pago FROM cotizaciones WHERE requisicion_id = ? ORDER BY id",
            conn, params=(int(req_id),),
        )


def leer_precios_requisicion(req_id: int) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(
            """
            SELECT cp.cotizacion_id, cp.partida_id, cp.precio_unitario
            FROM cotizacion_precios cp
            JOIN cotizaciones c ON c.id = cp.cotizacion_id
            WHERE c.requisicion_id = ?
            """,
            conn, params=(int(req_id),),
        )


def crear_orden_compra(req_id: int, cot_id: int, fecha: str, subtotal: float, iva: float, total: float) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO ordenes_compra (requisicion_id, cotizacion_id, fecha, subtotal, iva, total) VALUES (?, ?, ?, ?, ?, ?)",
            (int(req_id), int(cot_id), fecha, subtotal, iva, total),
        )
        return cur.lastrowid


def leer_orden_compra(req_id: int):
    with get_conn() as conn:
        df = pd.read_sql_query(
            "SELECT id, requisicion_id, cotizacion_id, fecha, subtotal, iva, total, gasto_id FROM ordenes_compra WHERE requisicion_id = ?",
            conn, params=(int(req_id),),
        )
    return df.iloc[0] if not df.empty else None


def vincular_gasto_oc(oc_id: int, gasto_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE ordenes_compra SET gasto_id = ? WHERE id = ?", (int(gasto_id), int(oc_id)))


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
rol_etiquetas = {"admin": "👷 Administrador", "residente": "📐 Residente de Obra", "cliente": "👤 Cliente (solo consulta)"}
rol_texto = rol_etiquetas.get(st.session_state.get("rol", "admin"), "👷 Administrador")
st.sidebar.markdown(f"**Sesión:** {rol_texto}")
if (os.environ.get("ADMIN_PASSWORD") or os.environ.get("APP_PASSWORD")
        or os.environ.get("RESIDENTE_PASSWORD") or os.environ.get("CLIENTE_PASSWORD")):
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
elif ES_RESIDENTE:
    st.sidebar.info("Modo obra: levanta requisiciones de material, genera solicitudes de cotización y captura las cotizaciones de los proveedores.")
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
# MÓDULO: REQUISICIONES, COTIZACIONES Y ÓRDENES DE COMPRA
# ---------------------------------------------------------------


def _dinero(v: float) -> str:
    return f"${v:,.2f}"


def folio_req(rid: int) -> str:
    return f"REQ-{int(rid):03d}"


def folio_oc_num(oid: int) -> str:
    return f"OC-JE132-{int(oid):03d}"


def _pdf_estilos():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import TableStyle

    styles = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle("titulo", parent=styles["Title"], fontSize=15, spaceAfter=2),
        "normal": styles["Normal"],
        "sub": ParagraphStyle("sub", parent=styles["Normal"], fontSize=9, textColor=colors.grey),
        "h2": ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11, spaceBefore=12, spaceAfter=4),
        "chico": ParagraphStyle("chico", parent=styles["Normal"], fontSize=8),
        "tabla": TableStyle([
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
        ]),
    }


def _tabla_partidas_pdf(partidas: pd.DataFrame, estilos: dict, precios_map=None,
                        subtotal: float | None = None, iva: float | None = None, total: float | None = None):
    """Tabla estilo cuantificación: CONCEPTO|UNIDAD|CANTIDAD|P.U.|IMPORTE|OBSERVACIONES,
    agrupada por categoría. Si precios_map es None, P.U. e IMPORTE van en blanco (para cotizar)."""
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, Table

    e = estilos
    filas = [["CONCEPTO", "UNIDAD", "CANTIDAD", "P.U.", "IMPORTE", "OBSERVACIONES"]]
    estilos_extra = []
    for categoria in CATEGORIAS:
        grupo = partidas[partidas["categoria"] == categoria]
        if grupo.empty:
            continue
        fila_seccion = len(filas)
        filas.append([categoria.upper(), "", "", "", "", ""])
        estilos_extra += [
            ("SPAN", (0, fila_seccion), (-1, fila_seccion)),
            ("BACKGROUND", (0, fila_seccion), (-1, fila_seccion), colors.HexColor("#dbe4ef")),
            ("FONTNAME", (0, fila_seccion), (-1, fila_seccion), "Helvetica-Bold"),
            ("ALIGN", (0, fila_seccion), (0, fila_seccion), "LEFT"),
        ]
        for _, p in grupo.iterrows():
            if precios_map is not None:
                pu = float(precios_map.get(p["id"], 0))
                pu_txt, imp_txt = _dinero(pu), _dinero(pu * p["cantidad"])
            else:
                pu_txt, imp_txt = "$", "$"
            filas.append([
                Paragraph(p["descripcion"], e["chico"]), p["unidad"], f"{p['cantidad']:g}",
                pu_txt, imp_txt, Paragraph(str(p["observaciones"] or ""), e["chico"]),
            ])
    if precios_map is not None and subtotal is not None:
        filas.append(["", "", "", "SUBTOTAL", _dinero(subtotal), ""])
        filas.append(["", "", "", "IVA", _dinero(iva or 0), ""])
        filas.append(["", "", "", "TOTAL", _dinero(total or subtotal), ""])
    else:
        filas.append(["", "", "", "TOTAL", "$", ""])

    t = Table(filas, colWidths=[6.6 * cm, 1.6 * cm, 1.9 * cm, 2.4 * cm, 2.6 * cm, 3.5 * cm], repeatRows=1)
    t.setStyle(e["tabla"])
    from reportlab.platypus import TableStyle
    t.setStyle(TableStyle(estilos_extra + [("ALIGN", (0, 1), (0, -1), "LEFT")]))
    return t


def generar_pdf_solicitud_cotizacion(req: pd.Series) -> bytes:
    """Formato de Relación de Materiales / Solicitud de Cotización, estilo cuantificación de obra."""
    from io import BytesIO

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    partidas = leer_partidas(req["id"])
    e = _pdf_estilos()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=1.3 * cm, bottomMargin=1.3 * cm,
                            leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                            title=f"Solicitud de Cotización {folio_req(req['id'])}")

    obra_txt = str(req.get("obra") or "").strip() or "Construcción Vivienda Familiar Tres Niveles (JE132)"
    ubicacion_txt = str(req.get("ubicacion") or "").strip() or "—"

    encabezado = Table([
        ["RELACIÓN DE MATERIALES — SOLICITUD DE COTIZACIÓN", f"Folio: {folio_req(req['id'])}"],
        [f"Obra: {obra_txt}", f"Fecha: {req['fecha']}"],
        ["Propietario: José Manuel Robles Miguel", f"Requerido en obra: {req['fecha_requerida']}"],
        [f"Ubicación: {ubicacion_txt}", f"Solicita: {req['solicitante']}"],
        ["Contratistas: DACAM & HOGAR 911 | control.hogar911.com", f"Etapa: {req['fase'].split(':')[0]}"],
    ], colWidths=[12.2 * cm, 6.4 * cm])
    encabezado.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (0, 0), 12),
        ("FONTSIZE", (1, 0), (1, 0), 10),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
    ]))
    elems = [encabezado, Spacer(1, 6)]
    if str(req.get("notas") or "").strip():
        elems.append(Paragraph(f"Notas: {req['notas']}", e["normal"]))
        elems.append(Spacer(1, 4))

    elems.append(_tabla_partidas_pdf(partidas, e))
    elems.append(Spacer(1, 10))
    elems.append(Paragraph(
        "Favor de cotizar indicando: precio unitario por partida, tiempo de entrega, "
        "vigencia de la cotización, condiciones de pago y si los precios incluyen IVA y flete a obra.", e["normal"]))
    elems.append(Spacer(1, 6))
    elems.append(Paragraph(f"Documento generado el {datetime.now():%d/%m/%Y %H:%M}.", e["chico"]))
    doc.build(elems)
    return buf.getvalue()


def generar_pdf_orden_compra(oc, req: pd.Series) -> bytes:
    """Orden de Compra formal para el proveedor seleccionado."""
    from io import BytesIO

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table

    partidas = leer_partidas(req["id"])
    cots = leer_cotizaciones(req["id"])
    cot = cots[cots["id"] == oc["cotizacion_id"]].iloc[0]
    precios = leer_precios_requisicion(req["id"])
    precios_cot = precios[precios["cotizacion_id"] == oc["cotizacion_id"]].set_index("partida_id")["precio_unitario"]

    e = _pdf_estilos()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=1.5 * cm, bottomMargin=1.5 * cm,
                            leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            title=f"Orden de Compra {folio_oc_num(oc['id'])}")
    elems = [
        Paragraph("ORDEN DE COMPRA", e["titulo"]),
        Paragraph(f"Folio: {folio_oc_num(oc['id'])} | Fecha: {oc['fecha']} | Ref: {folio_req(req['id'])}", e["normal"]),
        Paragraph("Proyecto: Construcción Vivienda Familiar Tres Niveles (JE132)", e["sub"]),
        Paragraph("Emite: DACAM & HOGAR 911 | control.hogar911.com", e["sub"]),
        Spacer(1, 8),
    ]
    filas_datos = [
        Paragraph(f"Proveedor: {cot['proveedor']}", e["h2"]),
        Paragraph(f"Tiempo de entrega: {cot['tiempo_entrega'] or 'Por confirmar'} | "
                  f"Condiciones de pago: {cot['condiciones_pago'] or 'Por confirmar'}", e["normal"]),
        Paragraph(f"Entregar en obra. Etapa: {req['fase']} | Fecha requerida: {req['fecha_requerida']}", e["normal"]),
        Paragraph("Partidas", e["h2"]),
    ]
    elems.extend(filas_datos)
    elems.append(_tabla_partidas_pdf(partidas, e, precios_map=precios_cot,
                                     subtotal=float(oc["subtotal"]), iva=float(oc["iva"]), total=float(oc["total"])))
    elems.append(Spacer(1, 24))
    firmas = Table([["_______________________", "_______________________"],
                    ["Autoriza\nDACAM & HOGAR 911", f"Acepta\n{cot['proveedor']}"]],
                   colWidths=[8.5 * cm, 8.5 * cm])
    elems.append(firmas)
    elems.append(Spacer(1, 8))
    elems.append(Paragraph(f"Documento generado el {datetime.now():%d/%m/%Y %H:%M}.", e["chico"]))
    doc.build(elems)
    return buf.getvalue()


def seccion_requisiciones():
    st.markdown("---")
    st.subheader("🧾 Requisiciones de Material y Compras")

    t_nueva, t_seg, t_cot, t_comp = st.tabs([
        "➕ Nueva requisición", "📋 Seguimiento", "💰 Capturar cotización", "⚖️ Comparativo y Orden de Compra",
    ])

    # ------ NUEVA REQUISICIÓN ------
    with t_nueva:
        if st.session_state.pop("msg_req", None):
            st.success(f"Requisición {st.session_state.pop('msg_req_folio', '')} guardada. "
                       "Genera el formato para proveedores desde la pestaña Seguimiento.")
        if "req_items" not in st.session_state:
            st.session_state.req_items = []
        if "req_ver" not in st.session_state:
            st.session_state.req_ver = 0

        c1, c2 = st.columns(2)
        obra_req = c1.text_input("Obra:", value="Construcción Vivienda Familiar Tres Niveles (JE132)", key="req_obra")
        ubicacion_req = c2.text_input("Ubicación (opcional):", key="req_ubicacion")
        c3, c4 = st.columns(2)
        fase_req = c3.selectbox("Fase de obra:", FASES, key="req_fase")
        fecha_requerida = c4.date_input("Fecha requerida en obra:", value=datetime.now().date(), key="req_fecha_req")
        solicitante = st.text_input("Solicita (arquitecto / encargado de obra):", key="req_solicitante")
        notas_req = st.text_input("Notas para los proveedores (opcional):", key="req_notas")

        st.markdown("**Partidas de material:**")
        hueco_catalogo = st.container()  # el selector del catálogo se muestra arriba de la tabla

        columnas_partidas = ["Cantidad", "Unidad", "Descripción", "Categoría", "Observaciones"]
        df_items = pd.DataFrame(st.session_state.req_items, columns=columnas_partidas)
        partidas_edit = st.data_editor(
            df_items,
            num_rows="dynamic",
            column_config={
                "Cantidad": st.column_config.NumberColumn("Cantidad", min_value=0.01, required=True),
                "Unidad": st.column_config.SelectboxColumn("Unidad", options=UNIDADES, required=True),
                "Descripción": st.column_config.TextColumn("Descripción", required=True),
                "Categoría": st.column_config.SelectboxColumn("Categoría", options=CATEGORIAS, required=True),
                "Observaciones": st.column_config.TextColumn("Observaciones", help="Ej. P/ cimentación, P/ castillos y cadena"),
            },
            hide_index=True,
            key=f"req_partidas_{st.session_state.req_ver}",
            **FULL_WIDTH,
        )
        st.caption("También puedes escribir partidas directamente en la tabla (➕ al final) si el material no está en el catálogo.")

        def _filas_del_editor(df: pd.DataFrame) -> list[list]:
            filas = []
            for _, p in df.iterrows():
                filas.append([
                    float(p["Cantidad"]) if pd.notna(p["Cantidad"]) else None,
                    p["Unidad"], p["Descripción"],
                    p["Categoría"] if pd.notna(p["Categoría"]) else CATEGORIAS[0],
                    p["Observaciones"] if pd.notna(p["Observaciones"]) else "",
                ])
            return filas

        with hueco_catalogo:
            df_catalogo = leer_catalogo()
            cc1, cc2, cc3 = st.columns([3, 1, 1])
            mat_sel = cc1.selectbox(
                "Agregar material del catálogo:",
                df_catalogo["id"].tolist(),
                format_func=lambda i: f"{df_catalogo.set_index('id').loc[i, 'descripcion']} "
                                      f"({df_catalogo.set_index('id').loc[i, 'unidad']})",
                key="cat_sel",
            )
            cant_sel = cc2.number_input("Cantidad:", min_value=0.0, step=1.0, key="cat_cant")
            cc3.markdown("<br>", unsafe_allow_html=True)
            if cc3.button("➕ Agregar", key="btn_cat_add", **FULL_WIDTH):
                if cant_sel <= 0:
                    st.warning("Indica la cantidad.")
                else:
                    m = df_catalogo.set_index("id").loc[mat_sel]
                    st.session_state.req_items = _filas_del_editor(partidas_edit) + [
                        [float(cant_sel), m["unidad"], m["descripcion"], m["categoria"], ""]
                    ]
                    st.session_state.req_ver += 1
                    st.rerun()

            with st.expander("📚 Agregar un material nuevo al catálogo (queda guardado para siempre)"):
                nc1, nc2, nc3 = st.columns([3, 1, 2])
                nuevo_desc = nc1.text_input("Descripción del material:", key="nuevo_mat_desc")
                nueva_unidad = nc2.selectbox("Unidad:", UNIDADES, key="nuevo_mat_unidad")
                nueva_cat = nc3.selectbox("Categoría:", CATEGORIAS, key="nuevo_mat_cat")
                if st.button("Guardar en el catálogo", key="btn_nuevo_mat"):
                    if not nuevo_desc.strip():
                        st.error("Escribe la descripción del material.")
                    elif agregar_material_catalogo(nuevo_desc, nueva_unidad, nueva_cat):
                        st.session_state.req_items = _filas_del_editor(partidas_edit)
                        st.session_state.req_ver += 1
                        st.success(f"'{nuevo_desc.strip()}' agregado al catálogo.")
                        st.rerun()
                    else:
                        st.warning("Ese material ya existe en el catálogo.")

        if st.button("💾 Guardar requisición", key="btn_guardar_req"):
            partidas_validas = []
            for fila in _filas_del_editor(partidas_edit):
                cant = float(fila[0] or 0)
                unidad = str(fila[1] or "").strip()
                desc = str(fila[2] or "").strip()
                if cant > 0 and unidad and desc:
                    partidas_validas.append({
                        "cantidad": cant, "unidad": unidad, "descripcion": desc,
                        "categoria": str(fila[3] or CATEGORIAS[0]),
                        "observaciones": str(fila[4] or "").strip(),
                    })
            if not solicitante.strip():
                st.error("Indica quién solicita el material.")
            elif not partidas_validas:
                st.error("Agrega al menos una partida completa (cantidad, unidad y descripción).")
            else:
                rid = crear_requisicion(
                    datetime.now().date().isoformat(), fase_req, solicitante,
                    fecha_requerida.isoformat(), notas_req, partidas_validas,
                    obra=obra_req, ubicacion=ubicacion_req,
                )
                st.session_state["msg_req"] = True
                st.session_state["msg_req_folio"] = folio_req(rid)
                st.session_state.req_items = []
                st.session_state.req_ver += 1
                st.rerun()

    df_reqs = leer_requisiciones()

    # ------ SEGUIMIENTO ------
    with t_seg:
        if df_reqs.empty:
            st.info("Aún no hay requisiciones registradas.")
        else:
            df_seg = df_reqs.copy()
            df_seg["Folio"] = df_seg["id"].apply(folio_req)
            df_seg["Estatus"] = df_seg.apply(
                lambda r: "🟣 OC generada" if r["ocs"] > 0
                else ("🔵 Cotizada" if r["cotizaciones"] > 0 else "🟠 Pendiente de cotizar"), axis=1)
            st.dataframe(
                df_seg[["Folio", "fecha", "fase", "solicitante", "fecha_requerida", "partidas", "cotizaciones", "Estatus"]]
                .rename(columns={"fecha": "Fecha", "fase": "Fase", "solicitante": "Solicita",
                                 "fecha_requerida": "Requerido", "partidas": "Partidas", "cotizaciones": "Cotizaciones"}),
                hide_index=True, **FULL_WIDTH,
            )
            sel_seg = st.selectbox(
                "Ver detalle de la requisición:",
                df_reqs["id"].tolist(),
                format_func=lambda i: f"{folio_req(i)} | {df_reqs.set_index('id').loc[i, 'fase']}",
                key="seg_sel",
            )
            req_sel = df_reqs.set_index("id").loc[sel_seg]
            req_sel = pd.concat([pd.Series({"id": sel_seg}), req_sel])
            st.dataframe(
                leer_partidas(sel_seg).rename(columns={
                    "cantidad": "Cantidad", "unidad": "Unidad", "descripcion": "Descripción",
                    "categoria": "Categoría", "observaciones": "Observaciones"})
                .drop(columns=["id"]),
                hide_index=True, **FULL_WIDTH,
            )
            col_s1, col_s2 = st.columns(2)
            with col_s1:
                try:
                    st.download_button(
                        "📄 Formato de Solicitud de Cotización (PDF)",
                        generar_pdf_solicitud_cotizacion(req_sel),
                        file_name=f"solicitud_cotizacion_{folio_req(sel_seg)}.pdf",
                        mime="application/pdf",
                        key=f"rfq_{sel_seg}",
                        **FULL_WIDTH,
                    )
                    st.caption("Envíaselo a cada proveedor para que coticen sobre las mismas partidas.")
                except ImportError:
                    st.error("Falta la librería reportlab en requirements.txt.")
            with col_s2:
                if ES_ADMIN and req_sel["ocs"] == 0:
                    if st.button("🗑️ Eliminar requisición", key=f"del_req_{sel_seg}"):
                        eliminar_requisicion(sel_seg)
                        st.rerun()

    # ------ CAPTURAR COTIZACIÓN ------
    with t_cot:
        if df_reqs.empty:
            st.info("Primero levanta una requisición.")
        else:
            if st.session_state.pop("msg_cot", None):
                st.success("Cotización guardada. Revísala en la pestaña de Comparativo.")
            sel_cot = st.selectbox(
                "Requisición a cotizar:",
                df_reqs["id"].tolist(),
                format_func=lambda i: f"{folio_req(i)} | {df_reqs.set_index('id').loc[i, 'fase']}",
                key="cot_sel",
            )
            c1, c2 = st.columns(2)
            proveedor_cot = c1.text_input("Proveedor:", key="cot_proveedor")
            fecha_cot = c2.date_input("Fecha de la cotización:", value=datetime.now().date(), key="cot_fecha")
            c3, c4 = st.columns(2)
            entrega_cot = c3.text_input("Tiempo de entrega (Ej. 3 días hábiles):", key="cot_entrega")
            pago_cot = c4.text_input("Condiciones de pago (Ej. Contado, 50% anticipo):", key="cot_pago")

            partidas_cot = leer_partidas(sel_cot)
            df_precios = partidas_cot.rename(
                columns={"cantidad": "Cantidad", "unidad": "Unidad", "descripcion": "Descripción",
                         "observaciones": "Observaciones"}).drop(columns=["categoria"])
            df_precios["Precio Unitario"] = 0.0
            precios_edit = st.data_editor(
                df_precios,
                column_config={
                    "id": None,
                    "Cantidad": st.column_config.NumberColumn(disabled=True),
                    "Unidad": st.column_config.TextColumn(disabled=True),
                    "Descripción": st.column_config.TextColumn(disabled=True),
                    "Observaciones": st.column_config.TextColumn(disabled=True),
                    "Precio Unitario": st.column_config.NumberColumn("Precio Unitario", min_value=0.0, format="$%.2f"),
                },
                hide_index=True,
                key=f"cot_precios_{sel_cot}",
                **FULL_WIDTH,
            )
            if st.button("💾 Guardar cotización", key="btn_guardar_cot"):
                precios = {int(r["id"]): float(r["Precio Unitario"] or 0) for _, r in precios_edit.iterrows()}
                if not proveedor_cot.strip():
                    st.error("Indica el nombre del proveedor.")
                elif not any(v > 0 for v in precios.values()):
                    st.error("Captura al menos un precio unitario mayor a 0.")
                else:
                    crear_cotizacion(sel_cot, proveedor_cot, fecha_cot.isoformat(), entrega_cot, pago_cot, precios)
                    st.session_state["msg_cot"] = True
                    st.session_state.pop(f"cot_precios_{sel_cot}", None)
                    st.rerun()

    # ------ COMPARATIVO Y ORDEN DE COMPRA ------
    with t_comp:
        reqs_con_cot = df_reqs[df_reqs["cotizaciones"] > 0] if not df_reqs.empty else pd.DataFrame()
        if reqs_con_cot.empty:
            st.info("Aún no hay requisiciones con cotizaciones capturadas.")
        else:
            sel_comp = st.selectbox(
                "Requisición:",
                reqs_con_cot["id"].tolist(),
                format_func=lambda i: f"{folio_req(i)} | {reqs_con_cot.set_index('id').loc[i, 'fase']} "
                                      f"({reqs_con_cot.set_index('id').loc[i, 'cotizaciones']} cotizaciones)",
                key="comp_sel",
            )
            req_comp = df_reqs.set_index("id").loc[sel_comp]
            req_comp = pd.concat([pd.Series({"id": sel_comp}), req_comp])
            partidas_comp = leer_partidas(sel_comp)
            cots_comp = leer_cotizaciones(sel_comp)
            precios_comp = leer_precios_requisicion(sel_comp)

            # Tabla comparativa: partidas x proveedores (importes)
            df_cmp = (partidas_comp.rename(columns={"cantidad": "Cant.", "unidad": "Unidad", "descripcion": "Descripción"})
                      .drop(columns=["categoria", "observaciones"]).copy())
            columnas_prov = []
            totales = {}
            for _, c in cots_comp.iterrows():
                pu_map = precios_comp[precios_comp["cotizacion_id"] == c["id"]].set_index("partida_id")["precio_unitario"]
                col = c["proveedor"]
                df_cmp[col] = df_cmp.apply(lambda r: float(pu_map.get(r["id"], 0)) * r["Cant."], axis=1)
                columnas_prov.append(col)
                totales[col] = df_cmp[col].sum()
            df_cmp = df_cmp.drop(columns=["id"])

            st.markdown("**Comparativo de importes por proveedor** (verde = mejor precio por partida):")
            fila_total = {"Cant.": None, "Unidad": "", "Descripción": "TOTAL"}
            fila_total.update(totales)
            df_cmp_total = pd.concat([df_cmp, pd.DataFrame([fila_total])], ignore_index=True)
            st.dataframe(
                df_cmp_total.style
                .format({c: "${:,.2f}" for c in columnas_prov} | {"Cant.": "{:g}"}, na_rep="")
                .highlight_min(axis=1, subset=columnas_prov, props="background-color: #d4efdf; font-weight: bold;"),
                hide_index=True, **FULL_WIDTH,
            )
            st.dataframe(
                cots_comp.rename(columns={"proveedor": "Proveedor", "fecha": "Fecha",
                                          "tiempo_entrega": "Entrega", "condiciones_pago": "Cond. de pago"})
                .drop(columns=["id"]),
                hide_index=True, **FULL_WIDTH,
            )

            oc_existente = leer_orden_compra(sel_comp)
            if oc_existente is not None:
                cot_ganadora = cots_comp[cots_comp["id"] == oc_existente["cotizacion_id"]].iloc[0]
                st.success(
                    f"✅ Orden de compra {folio_oc_num(oc_existente['id'])} generada para "
                    f"**{cot_ganadora['proveedor']}** por {_dinero(oc_existente['total'])}"
                    + (f" — ligada al gasto folio {int(oc_existente['gasto_id'])}." if pd.notna(oc_existente["gasto_id"]) else ".")
                )
                try:
                    st.download_button(
                        "📄 Descargar Orden de Compra (PDF)",
                        generar_pdf_orden_compra(oc_existente, req_comp),
                        file_name=f"{folio_oc_num(oc_existente['id'])}.pdf",
                        mime="application/pdf",
                        key=f"oc_pdf_{sel_comp}",
                    )
                except ImportError:
                    st.error("Falta la librería reportlab en requirements.txt.")
            elif ES_ADMIN:
                st.markdown("**Generar Orden de Compra:**")
                c1, c2, c3 = st.columns([2, 1, 1])
                ganador = c1.selectbox(
                    "Proveedor seleccionado:",
                    cots_comp["id"].tolist(),
                    format_func=lambda i: f"{cots_comp.set_index('id').loc[i, 'proveedor']} ({_dinero(totales.get(cots_comp.set_index('id').loc[i, 'proveedor'], 0))})",
                    key="oc_ganador",
                )
                con_iva = c2.checkbox("Agregar IVA 16%", value=True, key="oc_iva")
                registrar_gasto = c3.checkbox("Registrar como gasto", value=True, key="oc_gasto",
                                              help="Crea automáticamente el gasto de Materiales en la fase de la requisición.")
                if st.button("🧾 Generar Orden de Compra", key="btn_oc"):
                    prov_nombre = cots_comp.set_index("id").loc[ganador, "proveedor"]
                    subtotal = float(totales.get(prov_nombre, 0))
                    iva = round(subtotal * 0.16, 2) if con_iva else 0.0
                    total_oc = round(subtotal + iva, 2)
                    oc_id = crear_orden_compra(sel_comp, ganador, datetime.now().date().isoformat(), subtotal, iva, total_oc)
                    if registrar_gasto:
                        gid = insertar_gasto(
                            datetime.now().date().isoformat(), req_comp["fase"], "Materiales",
                            total_oc, prov_nombre,
                            f"{folio_oc_num(oc_id)} — Materiales {folio_req(sel_comp)} ({len(partidas_comp)} partidas)",
                        )
                        vincular_gasto_oc(oc_id, gid)
                    st.rerun()
            else:
                st.info("La orden de compra la genera el administrador a partir de este comparativo.")


if ES_ADMIN or ES_RESIDENTE:
    seccion_requisiciones()
if ES_RESIDENTE:
    st.stop()

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
            if st.session_state.pop("msg_gastos", None):
                st.success("Cambios guardados en la bitácora de gastos.")

            df_edicion = df_gastos.copy()
            df_edicion["fecha"] = pd.to_datetime(df_edicion["fecha"]).dt.date
            df_edicion["proveedor"] = df_edicion["proveedor"].fillna("")
            df_edicion["comprobante"] = df_edicion["comprobante"].apply(lambda c: "📎 Sí" if c else "—")
            df_edicion.insert(0, "Eliminar", False)

            editado = st.data_editor(
                df_edicion,
                column_config={
                    "Eliminar": st.column_config.CheckboxColumn("Eliminar", help="Marca los registros a borrar"),
                    "id": st.column_config.NumberColumn("Folio", disabled=True),
                    "fecha": st.column_config.DateColumn("Fecha", required=True, format="YYYY-MM-DD"),
                    "fase": st.column_config.SelectboxColumn("Fase", options=FASES + [FASE_INDIRECTOS], required=True),
                    "tipo": st.column_config.SelectboxColumn("Tipo", options=TIPOS_DIRECTOS + [FASE_INDIRECTOS], required=True),
                    "monto": st.column_config.NumberColumn("Monto", format="$%.2f", min_value=0.01, required=True),
                    "proveedor": st.column_config.TextColumn("Proveedor"),
                    "descripcion": st.column_config.TextColumn("Descripción", required=True),
                    "comprobante": st.column_config.TextColumn("Comprobante", disabled=True),
                },
                disabled=["id", "comprobante"],
                num_rows="fixed",
                hide_index=True,
                key="editor_gastos",
                **FULL_WIDTH,
            )
            st.caption("✏️ Haz doble clic en cualquier celda para corregirla y luego pulsa «Guardar cambios». Los gastos nuevos se capturan en el panel lateral.")

            col_a, col_b, col_c = st.columns([1, 1, 2])
            with col_a:
                if st.button("💾 Guardar cambios", key="save_gastos"):
                    errores, cambios = [], []
                    originales = df_gastos.set_index("id")
                    for _, r in editado.iterrows():
                        gid = int(r["id"])
                        fase_n, tipo_n = r["fase"], r["tipo"]
                        desc_n = str(r["descripcion"] or "").strip()
                        prov_n = str(r["proveedor"] or "").strip()
                        monto_n = float(r["monto"] or 0)
                        if fase_n == FASE_INDIRECTOS and tipo_n != FASE_INDIRECTOS:
                            errores.append(f"Folio {gid}: si la fase es '{FASE_INDIRECTOS}', el tipo debe ser '{FASE_INDIRECTOS}'.")
                            continue
                        if fase_n != FASE_INDIRECTOS and tipo_n == FASE_INDIRECTOS:
                            errores.append(f"Folio {gid}: el tipo '{FASE_INDIRECTOS}' solo aplica a la fase '{FASE_INDIRECTOS}'.")
                            continue
                        if monto_n <= 0:
                            errores.append(f"Folio {gid}: el monto debe ser mayor a 0.")
                            continue
                        if not desc_n:
                            errores.append(f"Folio {gid}: la descripción no puede quedar vacía.")
                            continue
                        o = originales.loc[gid]
                        nuevo = (r["fecha"].isoformat(), fase_n, tipo_n, monto_n, prov_n, desc_n)
                        original = (str(o["fecha"]), o["fase"], o["tipo"], float(o["monto"]), str(o["proveedor"] or "").strip(), str(o["descripcion"]))
                        if nuevo != original:
                            cambios.append((*nuevo, gid))
                    if errores:
                        st.error("No se guardó nada. Corrige lo siguiente:\n\n- " + "\n- ".join(errores))
                    elif cambios:
                        actualizar_gastos(cambios)
                        st.session_state["msg_gastos"] = True
                        st.rerun()
                    else:
                        st.info("No hay cambios que guardar.")
            with col_b:
                if st.button("🗑️ Eliminar seleccionados", key="del_gastos"):
                    ids = editado.loc[editado["Eliminar"], "id"].tolist()
                    if ids:
                        eliminar_gastos(ids)
                        st.rerun()
                    else:
                        st.warning("No hay registros marcados.")
            with col_c:
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
            if st.session_state.pop("msg_pagos", None):
                st.success("Cambios guardados en los pagos del cliente.")

            df_editor_p = df_pagos.copy()
            df_editor_p["fecha"] = pd.to_datetime(df_editor_p["fecha"]).dt.date
            df_editor_p.insert(0, "Eliminar", False)
            editado_p = st.data_editor(
                df_editor_p,
                column_config={
                    "Eliminar": st.column_config.CheckboxColumn("Eliminar"),
                    "id": st.column_config.NumberColumn("Folio", disabled=True),
                    "fecha": st.column_config.DateColumn("Fecha", required=True, format="YYYY-MM-DD"),
                    "concepto": st.column_config.TextColumn("Concepto", required=True),
                    "monto": st.column_config.NumberColumn("Monto", format="$%.2f", min_value=0.01, required=True),
                },
                disabled=["id"],
                num_rows="fixed",
                hide_index=True,
                key="editor_pagos",
                **FULL_WIDTH,
            )
            st.caption("✏️ Doble clic en una celda para corregirla y luego «Guardar cambios».")

            col_p1, col_p2 = st.columns([1, 3])
            with col_p1:
                if st.button("💾 Guardar cambios", key="save_pagos"):
                    errores_p, cambios_p = [], []
                    originales_p = df_pagos.set_index("id")
                    for _, r in editado_p.iterrows():
                        pid = int(r["id"])
                        concepto_n = str(r["concepto"] or "").strip()
                        monto_n = float(r["monto"] or 0)
                        if monto_n <= 0:
                            errores_p.append(f"Folio {pid}: el monto debe ser mayor a 0.")
                            continue
                        if not concepto_n:
                            errores_p.append(f"Folio {pid}: el concepto no puede quedar vacío.")
                            continue
                        o = originales_p.loc[pid]
                        nuevo = (r["fecha"].isoformat(), concepto_n, monto_n)
                        original = (str(o["fecha"]), str(o["concepto"]), float(o["monto"]))
                        if nuevo != original:
                            cambios_p.append((*nuevo, pid))
                    if errores_p:
                        st.error("No se guardó nada. Corrige lo siguiente:\n\n- " + "\n- ".join(errores_p))
                    elif cambios_p:
                        actualizar_pagos(cambios_p)
                        st.session_state["msg_pagos"] = True
                        st.rerun()
                    else:
                        st.info("No hay cambios que guardar.")
            with col_p2:
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
