"""
Tablero de Cumplimiento de Metas · Zona Coppel
Registro de avances en tiempo real (compartido entre tiendas) + reporte PDF
------------------------------------------------------------------------
Ejecutar:  streamlit run app_mejorado.py
Requiere:  pip install streamlit pandas numpy plotly openpyxl reportlab
"""

import io
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

try:  # El PDF es opcional: si falta reportlab, el tablero funciona igual
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    PDF_DISPONIBLE = True
except ImportError:
    PDF_DISPONIBLE = False

st.set_page_config(page_title="Cumplimiento Coppel · Zona", page_icon="📊", layout="wide")

# ------------------------------------------------------------------ Configuración
DB_PATH = Path(__file__).with_name("tablero_zona.db")

TIENDAS = ["77", "110", "203", "668", "851", "6678", "6806"]
INDICADORES = [
    "Apps Coppel",
    "Cliente Digital Avanzado (CDA)",
    "1a. Compra en Canal Digital",
    "Ventas en Coppel.com",
]
VENTAS = INDICADORES[3]  # único indicador en pesos
COLUMNAS = ["Tienda", "Indicador", "Meta", "Real"]
TODA_LA_ZONA = "Toda la Zona"
TODOS_IND = "Todos los indicadores"

# nivel: (etiqueta, fondo de fila, texto de fila, color de barra)
SEMAFORO = {
    "Verde": ("🟢 Verde", "#C6EFCE", "#0B5D1E", "#2E9E4F"),
    "Amarillo": ("🟡 Amarillo", "#FFF2B3", "#6B5300", "#F2C230"),
    "Naranja": ("🟠 Naranja", "#FFD9B3", "#7A3B00", "#F28C28"),
    "Rojo": ("🔴 Rojo", "#FFC7CE", "#8C1018", "#D64541"),
}


def nivel(pct: float) -> str:
    pct = round(pct, 2)
    if pct >= 100:
        return "Verde"
    if pct >= 85:
        return "Amarillo"
    if pct >= 60:
        return "Naranja"
    return "Rojo"


def fmt(valor: float, indicador: str) -> str:
    return f"${valor:,.0f}" if indicador == VENTAS else f"{valor:,.0f}"


def ordenar(valores, canonicos):
    """Orden canónico primero; lo que traiga el archivo y no esté, al final."""
    valores = list(dict.fromkeys(valores))
    otros = sorted(
        (v for v in valores if v not in canonicos),
        key=lambda v: (0, int(v), v) if v.isdigit() else (1, 0, v),
    )
    return [v for v in canonicos if v in valores] + otros


def ordenar_df(df: pd.DataFrame) -> pd.DataFrame:
    orden = {
        "Tienda": {v: i for i, v in enumerate(ordenar(df["Tienda"], TIENDAS))},
        "Indicador": {v: i for i, v in enumerate(ordenar(df["Indicador"], INDICADORES))},
    }
    return df.sort_values(
        ["Tienda", "Indicador"], key=lambda s: s.map(orden[s.name])
    ).reset_index(drop=True)


# ------------------------------------------------------------------ Datos de entrada
@st.cache_data
def generar_demo() -> pd.DataFrame:
    rng = np.random.default_rng(2026)
    tamano = {"77": 1.00, "110": 1.60, "203": 0.70, "668": 1.30,
              "851": 0.90, "6678": 0.55, "6806": 1.90}
    base = {INDICADORES[0]: 1_200, INDICADORES[1]: 800, INDICADORES[2]: 450, VENTAS: 1_500_000}
    filas = []
    for tienda in TIENDAS:
        for ind in INDICADORES:
            redondeo = -3 if ind == VENTAS else 0
            meta = int(round(base[ind] * tamano[tienda] * rng.uniform(0.92, 1.08), redondeo))
            real = int(meta * rng.uniform(0.50, 1.15))
            filas.append({"Tienda": tienda, "Indicador": ind, "Meta": meta, "Real": real})
    return pd.DataFrame(filas)


def leer_archivo(archivo) -> pd.DataFrame:
    if archivo.name.lower().endswith(".csv"):
        for enc in ("utf-8-sig", "latin-1"):
            try:
                archivo.seek(0)
                return pd.read_csv(archivo, sep=None, engine="python", encoding=enc)
            except UnicodeDecodeError:
                continue
        raise ValueError("No se pudo leer la codificación del CSV.")
    archivo.seek(0)
    return pd.read_excel(archivo)


def preparar(df: pd.DataFrame) -> pd.DataFrame:
    """Valida y limpia el archivo. Devuelve Tienda, Indicador, Meta, Real."""
    df = df.copy()
    df.columns = [str(c).strip().capitalize() for c in df.columns]
    faltan = [c for c in COLUMNAS if c not in df.columns]
    if faltan:
        raise ValueError(f"Faltan las columnas: {', '.join(faltan)}. Se requieren: {', '.join(COLUMNAS)}.")
    df = df[COLUMNAS].copy()

    df["Tienda"] = df["Tienda"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    canon = {i.lower(): i for i in INDICADORES}
    df["Indicador"] = df["Indicador"].astype(str).str.strip().map(lambda x: canon.get(x.lower(), x))

    for col in ("Meta", "Real"):
        if not pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].astype(str).str.replace(r"[^\d.\-]", "", regex=True)
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Meta", "Real"])
    df = df[df["Meta"] > 0]  # evita división entre cero
    if df.empty:
        raise ValueError("No hay filas válidas (Meta y Real numéricas, con Meta mayor a 0).")

    df = df.groupby(["Tienda", "Indicador"], as_index=False)[["Meta", "Real"]].sum()
    return ordenar_df(df)


# ------------------------------------------------------------------ Base compartida (SQLite)
# Todas las sesiones (tiendas, gerente de zona) leen y escriben el mismo archivo .db,
# por eso un registro hecho por una tienda lo ve toda la zona al actualizar.
def db() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, timeout=10)


def guardar_base(df: pd.DataFrame, fuente: str) -> None:
    """Reemplaza la base del mes y reinicia los registros en tiempo real."""
    filas = [(str(t), str(i), float(m), float(r))
             for t, i, m, r in df[COLUMNAS].itertuples(index=False, name=None)]
    with closing(db()) as con, con:
        con.execute("DELETE FROM base")
        con.execute("DELETE FROM registros")
        con.executemany("INSERT INTO base VALUES (?, ?, ?, ?)", filas)
        con.execute("INSERT OR REPLACE INTO config VALUES ('fuente', ?)", (fuente,))


def init_db() -> None:
    with closing(db()) as con, con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""CREATE TABLE IF NOT EXISTS base (
            tienda TEXT, indicador TEXT, meta REAL, real_base REAL,
            PRIMARY KEY (tienda, indicador))""")
        con.execute("""CREATE TABLE IF NOT EXISTS registros (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,
            tienda TEXT, indicador TEXT, cantidad REAL)""")
        con.execute("CREATE TABLE IF NOT EXISTS config (clave TEXT PRIMARY KEY, valor TEXT)")
        vacia = con.execute("SELECT COUNT(*) FROM base").fetchone()[0] == 0
    if vacia:
        guardar_base(generar_demo(), "demo")


def cargar_datos():
    """Real = Real de la base + suma de los registros en tiempo real."""
    with closing(db()) as con:
        df = pd.read_sql_query(
            """SELECT b.tienda AS Tienda, b.indicador AS Indicador, b.meta AS Meta,
                      b.real_base + COALESCE(SUM(r.cantidad), 0) AS Real
               FROM base b
               LEFT JOIN registros r ON r.tienda = b.tienda AND r.indicador = b.indicador
               GROUP BY b.tienda, b.indicador, b.meta, b.real_base""",
            con,
        )
        fuente = con.execute("SELECT valor FROM config WHERE clave = 'fuente'").fetchone()
    df = ordenar_df(df)
    df["Cumplimiento (%)"] = df["Real"] / df["Meta"] * 100
    return df, (fuente[0] if fuente else "demo")


def registrar(tienda: str, indicador: str, cantidad: float) -> None:
    with closing(db()) as con, con:
        con.execute(
            "INSERT INTO registros (ts, tienda, indicador, cantidad) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), tienda, indicador, float(cantidad)),
        )


def deshacer_ultimo() -> bool:
    with closing(db()) as con, con:
        fila = con.execute("SELECT id FROM registros ORDER BY id DESC LIMIT 1").fetchone()
        if fila:
            con.execute("DELETE FROM registros WHERE id = ?", fila)
        return fila is not None


def registros_recientes(n: int = 8) -> pd.DataFrame:
    with closing(db()) as con:
        return pd.read_sql_query(
            """SELECT ts AS Fecha, tienda AS Tienda, indicador AS Indicador, cantidad AS Cantidad
               FROM registros ORDER BY id DESC LIMIT ?""",
            con, params=(n,),
        )


# ------------------------------------------------------------------ Reporte PDF
def _tabla_pdf(filas, anchos, niveles, col_texto):
    t = Table(filas, colWidths=anchos, repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F6FEB")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (col_texto, 1), (col_texto, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B0B7C3")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, n in enumerate(niveles, start=1):
        estilo.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor(SEMAFORO[n][1])))
        estilo.append(("TEXTCOLOR", (0, i), (-1, i), colors.HexColor(SEMAFORO[n][2])))
    t.setStyle(TableStyle(estilo))
    return t


@st.cache_data(show_spinner=False)
def generar_pdf(df: pd.DataFrame, generado: str) -> bytes:
    """PDF de la Zona completa. Se regenera solo cuando cambian los datos (o cada minuto)."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, title="Reporte Zona Coppel")
    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle("Titulo", parent=estilos["Heading1"], fontSize=18, leading=22,
                            textColor=colors.HexColor("#1F6FEB"), spaceAfter=6)
    h2 = ParagraphStyle("H2", parent=estilos["Heading2"], fontSize=12, spaceBefore=14, spaceAfter=6)
    texto = ParagraphStyle("Texto", parent=estilos["Normal"], fontSize=10, leading=14)

    story = [
        Paragraph("<b>Reporte de Cumplimiento de Metas — Zona Coppel</b>", titulo),
        Paragraph(f"Generado el {generado}", texto),
        Paragraph("Resumen de la Zona", h2),
    ]

    resumen, niveles = [["Indicador", "Meta", "Real", "% Cumpl.", "Estatus"]], []
    for ind in ordenar(df["Indicador"], INDICADORES):
        d = df[df["Indicador"] == ind]
        meta, real = d["Meta"].sum(), d["Real"].sum()
        pct = real / meta * 100
        niveles.append(nivel(pct))
        resumen.append([ind, fmt(meta, ind), fmt(real, ind), f"{pct:.1f}%", niveles[-1]])
    story.append(_tabla_pdf(resumen, [170, 80, 80, 60, 70], niveles, 0))

    story.append(Paragraph("Detalle por tienda", h2))
    detalle, niveles = [["Tienda", "Indicador", "Meta", "Real", "% Cumpl.", "Estatus"]], []
    for _, f in df.iterrows():
        niveles.append(nivel(f["Cumplimiento (%)"]))
        detalle.append([f["Tienda"], f["Indicador"], fmt(f["Meta"], f["Indicador"]),
                        fmt(f["Real"], f["Indicador"]), f"{f['Cumplimiento (%)']:.1f}%", niveles[-1]])
    story.append(_tabla_pdf(detalle, [38, 160, 72, 72, 58, 60], niveles, 1))

    doc.build(story)
    return buffer.getvalue()


# ------------------------------------------------------------------ Gráficas
def graficas(df: pd.DataFrame, ind: str, tienda_sel: str) -> None:
    d = df[df["Indicador"] == ind]
    col1, col2 = st.columns(2)

    op = [1.0 if tienda_sel in (TODA_LA_ZONA, t) else 0.3 for t in d["Tienda"]]
    fig1 = go.Figure()
    fig1.add_bar(x=d["Tienda"], y=d["Meta"], name="Meta", marker=dict(color="#9AA5B1", opacity=op))
    fig1.add_bar(x=d["Tienda"], y=d["Real"], name="Real", marker=dict(color="#1F6FEB", opacity=op))
    fig1.update_layout(
        title=f"Meta vs. Real · {ind}", barmode="group", height=340,
        xaxis=dict(type="category", title="Tienda"), yaxis=dict(tickformat=",.0f"),
        legend=dict(orientation="h", y=1.15, x=0), margin=dict(t=70, b=10, l=10, r=10),
    )
    with col1:
        st.plotly_chart(fig1, key=f"meta_real_{ind}")

    r = d.sort_values("Cumplimiento (%)", ascending=False)
    op_r = [1.0 if tienda_sel in (TODA_LA_ZONA, t) else 0.3 for t in r["Tienda"]]
    fig2 = go.Figure(go.Bar(
        x=r["Tienda"], y=r["Cumplimiento (%)"],
        text=[f"{p:.1f}%" for p in r["Cumplimiento (%)"]], textposition="outside",
        marker=dict(color=[SEMAFORO[nivel(p)][3] for p in r["Cumplimiento (%)"]], opacity=op_r),
        hovertemplate="Tienda %{x}<br>Cumplimiento: %{y:.2f}%<extra></extra>",
    ))
    fig2.add_hline(y=100, line_dash="dash", line_color="#6B7280",
                   annotation_text="Meta 100%", annotation_position="top left")
    fig2.update_layout(
        title=f"Ranking de cumplimiento (%) · {ind}", height=340,
        xaxis=dict(type="category", title="Tienda"),
        yaxis=dict(title="% de cumplimiento", range=[0, max(115, r["Cumplimiento (%)"].max() * 1.15)]),
        margin=dict(t=70, b=10, l=10, r=10), showlegend=False,
    )
    with col2:
        st.plotly_chart(fig2, key=f"ranking_{ind}")


# ------------------------------------------------------------------ Interfaz
init_db()
df, fuente = cargar_datos()
tiendas = ordenar(df["Tienda"], TIENDAS)
indicadores = ordenar(df["Indicador"], INDICADORES)

st.title("📊 Tablero Zona Coppel — En Tiempo Real")

if "aviso" in st.session_state:  # mensaje de la acción anterior (tras st.rerun)
    st.success(st.session_state.pop("aviso"))

if fuente == "demo":
    st.info("🧪 Modo Demo: datos ficticios. Sube el archivo del mes para trabajar con datos reales.")
else:
    st.caption(f"📄 Base del mes: {fuente}")

col_carga, col_captura = st.columns(2)

with col_carga:
    with st.expander("📁 Cargar Excel / CSV del mes", expanded=False):
        archivo = st.file_uploader("Columnas: Tienda, Indicador, Meta, Real", type=["xlsx", "xlsm", "csv"])
        st.download_button(
            "⬇️ Plantilla de ejemplo (CSV)",
            data=generar_demo().to_csv(index=False).encode("utf-8-sig"),
            file_name="plantilla_metas.csv", mime="text/csv",
        )
        if archivo is not None:
            try:
                nuevo = preparar(leer_archivo(archivo))
            except Exception as e:
                st.error(f"No se pudo procesar el archivo: {e}")
            else:
                st.success(f"Archivo válido: {len(nuevo)} filas, {nuevo['Tienda'].nunique()} tiendas.")
                st.caption("⚠️ Al aplicarlo se reemplaza la base actual y se reinician los registros en tiempo real.")
                if st.button("✅ Aplicar como base del mes"):
                    guardar_base(nuevo, archivo.name)
                    st.session_state["aviso"] = "Base del mes actualizada."
                    st.rerun()

with col_captura:
    with st.expander("➕ Registro de avances en tiempo real", expanded=True):
        st.caption("Cada registro se suma al Real y lo ve toda la zona al actualizar.")
        t_form = st.selectbox("Tienda que reporta:", tiendas)
        i_form = st.selectbox("Indicador:", indicadores)
        etiqueta = "Monto en pesos ($):" if i_form == VENTAS else "Cantidad (unidades):"
        with st.form("form_registro"):
            cantidad = st.number_input(etiqueta, min_value=1, value=1, step=1)
            enviar = st.form_submit_button("⚡ Registrar impacto inmediato")
        if enviar:
            existe = ((df["Tienda"] == t_form) & (df["Indicador"] == i_form)).any()
            if existe:
                registrar(t_form, i_form, cantidad)
                st.session_state["aviso"] = f"Se sumaron {fmt(cantidad, i_form)} a {i_form} · Tienda {t_form}."
                st.rerun()
            else:
                st.error("Esa combinación de tienda e indicador no existe en la base del mes.")

        if st.button("↩️ Deshacer último registro"):
            if deshacer_ultimo():
                st.session_state["aviso"] = "Se deshizo el último registro."
                st.rerun()
            else:
                st.warning("No hay registros que deshacer.")

        recientes = registros_recientes()
        if not recientes.empty:
            st.caption("Últimos registros")
            st.dataframe(recientes, hide_index=True)

# ------------------------------------------------------------------ Filtros laterales
st.sidebar.header("Filtros de Zona")
tienda_sel = st.sidebar.selectbox("Tienda:", [TODA_LA_ZONA] + tiendas)
ind_sel = st.sidebar.selectbox("Indicador:", [TODOS_IND] + indicadores)
st.sidebar.button("🔄 Actualizar datos", help="Vuelve a leer los registros de todas las tiendas.")
st.sidebar.divider()

if PDF_DISPONIBLE:
    st.sidebar.download_button(
        "📥 Descargar Reporte PDF",
        data=generar_pdf(df, datetime.now().strftime("%d/%m/%Y %H:%M")),
        file_name="Reporte_Zona_Coppel.pdf", mime="application/pdf",
    )
else:
    st.sidebar.warning("Para habilitar el PDF instala reportlab:\n\n`pip install reportlab`")

# ------------------------------------------------------------------ Resumen (KPIs)
df_alcance = df if tienda_sel == TODA_LA_ZONA else df[df["Tienda"] == tienda_sel]
st.subheader(f"📈 Resumen · {'Toda la Zona' if tienda_sel == TODA_LA_ZONA else 'Tienda ' + tienda_sel}")

cols = st.columns(len(indicadores))
for col, ind in zip(cols, indicadores):
    d = df_alcance[df_alcance["Indicador"] == ind]
    if d.empty:
        continue
    meta, real = d["Meta"].sum(), d["Real"].sum()
    pct = real / meta * 100
    icono = SEMAFORO[nivel(pct)][0].split()[0]
    col.metric(
        label=f"{icono} {ind}", value=f"{pct:.1f}%",
        help=(f"Σ Real / Σ Meta de {len(d)} tienda(s). "
              f"Promedio simple de tiendas: {d['Cumplimiento (%)'].mean():.1f}%."),
    )
    col.caption(f"Real {fmt(real, ind)} de {fmt(meta, ind)}")

st.divider()

# ------------------------------------------------------------------ Gráficas
st.subheader("Visualización")
if ind_sel == TODOS_IND:
    for tab, ind in zip(st.tabs(indicadores), indicadores):
        with tab:
            graficas(df, ind, tienda_sel)
else:
    graficas(df, ind_sel, tienda_sel)

st.divider()

# ------------------------------------------------------------------ Tabla de semáforo
st.subheader("📋 Semáforo de cumplimiento")
st.markdown(
    "🟢 **≥ 100%** &nbsp;&nbsp; 🟡 **85% – 99.99%** &nbsp;&nbsp; "
    "🟠 **60% – 84.99%** &nbsp;&nbsp; 🔴 **< 60%**"
)

tabla = df_alcance if ind_sel == TODOS_IND else df_alcance[df_alcance["Indicador"] == ind_sel]
tabla = tabla.copy()
tabla["Semáforo"] = tabla["Cumplimiento (%)"].map(lambda p: SEMAFORO[nivel(p)][0])


def pintar_fila(fila):
    _, fondo, texto, _ = SEMAFORO[nivel(fila["Cumplimiento (%)"])]
    return [f"background-color: {fondo}; color: {texto}"] * len(fila)


estilo = tabla.style.apply(pintar_fila, axis=1).format(
    {"Meta": "{:,.0f}", "Real": "{:,.0f}", "Cumplimiento (%)": "{:.2f}%"}
)
filas_ventas = tabla.index[tabla["Indicador"] == VENTAS]
if len(filas_ventas):
    estilo = estilo.format("${:,.0f}", subset=pd.IndexSlice[filas_ventas, ["Meta", "Real"]])

st.dataframe(estilo, hide_index=True, height=min(38 * (len(tabla) + 1) + 3, 800))
