"""
Tablero de Cumplimiento de Metas · Zona Coppel
Histórico mensual + registro de avances en tiempo real + reporte PDF
------------------------------------------------------------------
Ejecutar:  streamlit run app_mejorado.py
Requiere:  pip install streamlit pandas plotly openpyxl reportlab tzdata

Cómo funciona el calendario:
  * El mes en curso se calcula solo con la fecha de hoy (zona horaria de ZONA_HORARIA).
  * El día 1 de cada mes no hay metas cargadas para el mes nuevo: la pantalla muestra
    únicamente el espacio para subir el archivo de metas. Al aplicarlo, ese mes se abre
    para registrar avances.
  * Los meses anteriores se conservan en la base y se consultan (solo lectura) desde el
    menú lateral "Mes a consultar".
"""

import calendar
import io
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

try:  # El PDF es opcional: si falta reportlab, el tablero funciona igual
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    PDF_DISPONIBLE = True
except ImportError:
    PDF_DISPONIBLE = False

st.set_page_config(page_title="Cumplimiento Coppel · Zona", page_icon="📊", layout="wide")

# ------------------------------------------------------------------ Configuración
DB_PATH = Path(__file__).with_name("tablero_zona.db")
ZONA_HORARIA = "America/Mexico_City"  # define cuándo cambia el día/mes (importante en la nube)

TIENDAS = ["77", "110", "203", "668", "851", "6678", "6806"]
INDICADORES = [
    "Apps Coppel",
    "Cliente Digital Avanzado (CDA)",
    "1a. Compra en Canal Digital",
    "Ventas en Coppel.com",
]
VENTAS = INDICADORES[3]  # único indicador en pesos
COLUMNAS = ["Tienda", "Indicador", "Meta", "Real"]
REQUERIDAS = ["Tienda", "Indicador", "Meta"]  # "Real" es opcional (arranca en 0)
TODA_LA_ZONA = "Toda la Zona"
TODOS_IND = "Todos los indicadores"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

# nivel: (etiqueta, fondo de fila, texto de fila, color de barra)
SEMAFORO = {
    "Verde": ("🟢 Verde", "#C6EFCE", "#0B5D1E", "#2E9E4F"),
    "Amarillo": ("🟡 Amarillo", "#FFF2B3", "#6B5300", "#F2C230"),
    "Naranja": ("🟠 Naranja", "#FFD9B3", "#7A3B00", "#F28C28"),
    "Rojo": ("🔴 Rojo", "#FFC7CE", "#8C1018", "#D64541"),
}


# ------------------------------------------------------------------ Utilidades
def ahora() -> datetime:
    try:
        return datetime.now(ZoneInfo(ZONA_HORARIA))
    except Exception:  # sin base de zonas horarias (p. ej. Windows sin tzdata)
        return datetime.now()


def hoy() -> date:
    return ahora().date()


def nombre_periodo(periodo: str) -> str:
    """'2026-11' -> 'Noviembre 2026'"""
    anio, mes = periodo.split("-")
    return f"{MESES[int(mes) - 1].capitalize()} {anio}"


def etiqueta_corta(periodo: str, actual: str) -> str:
    """'2026-11' -> 'Nov 26' (con * si es el mes en curso)"""
    anio, mes = periodo.split("-")
    return f"{MESES[int(mes) - 1][:3].capitalize()} {anio[2:]}" + ("*" if periodo == actual else "")


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


def con_avance_diario(df: pd.DataFrame, fecha: date):
    """Prorratea la meta mensual a la fecha de corte.

    Meta al día = Meta mensual × día de corte ÷ días del mes
    Cumplimiento al día (%) = Real ÷ Meta al día × 100
    """
    dias_mes = calendar.monthrange(fecha.year, fecha.month)[1]
    out = df.copy()
    out["Meta al día"] = out["Meta"] * fecha.day / dias_mes
    out["Cumplimiento al día (%)"] = out["Real"] / out["Meta al día"] * 100
    return out, dias_mes


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


# ------------------------------------------------------------------ Archivo de metas
def plantilla_metas() -> pd.DataFrame:
    """Archivo de ejemplo: metas de muestra (cámbialas por las reales del mes) y Real en 0."""
    tamano = {"77": 1.00, "110": 1.60, "203": 0.70, "668": 1.30,
              "851": 0.90, "6678": 0.55, "6806": 1.90}
    base = {INDICADORES[0]: 1_200, INDICADORES[1]: 800, INDICADORES[2]: 450, VENTAS: 1_500_000}
    filas = [{"Tienda": t, "Indicador": i, "Meta": int(round(base[i] * tamano[t], -1)), "Real": 0}
             for t in TIENDAS for i in INDICADORES]
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
    """Valida y limpia el archivo. Devuelve Tienda, Indicador, Meta, Real (Real opcional, por defecto 0)."""
    df = df.copy()
    df.columns = [str(c).strip().capitalize() for c in df.columns]
    faltan = [c for c in REQUERIDAS if c not in df.columns]
    if faltan:
        raise ValueError(
            f"Faltan las columnas: {', '.join(faltan)}. "
            f"Se requieren: {', '.join(REQUERIDAS)} (Real es opcional)."
        )
    if "Real" not in df.columns:
        df["Real"] = 0
    df = df[COLUMNAS].copy()

    df["Tienda"] = df["Tienda"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    canon = {i.lower(): i for i in INDICADORES}
    df["Indicador"] = df["Indicador"].astype(str).str.strip().map(lambda x: canon.get(x.lower(), x))

    for col in ("Meta", "Real"):
        if not pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].astype(str).str.replace(r"[^\d.\-]", "", regex=True)
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Meta"])
    df["Real"] = df["Real"].fillna(0)
    df = df[df["Meta"] > 0]  # evita división entre cero
    if df.empty:
        raise ValueError("No hay filas válidas (Meta numérica mayor a 0).")

    df = df.groupby(["Tienda", "Indicador"], as_index=False)[["Meta", "Real"]].sum()
    return ordenar_df(df)


# ------------------------------------------------------------------ Base compartida (SQLite)
# Todas las sesiones (tiendas, gerente de zona) leen y escriben el mismo archivo .db.
# Cada mes se guarda con su propio periodo ('AAAA-MM') y nunca se borra al cambiar de mes.
def db() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, timeout=10)


def _crear_tablas(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS periodos (
        periodo TEXT PRIMARY KEY, fuente TEXT, cargado TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS base (
        periodo TEXT, tienda TEXT, indicador TEXT, meta REAL, real_base REAL,
        PRIMARY KEY (periodo, tienda, indicador))""")
    con.execute("""CREATE TABLE IF NOT EXISTS registros (
        id INTEGER PRIMARY KEY AUTOINCREMENT, periodo TEXT, ts TEXT,
        tienda TEXT, indicador TEXT, cantidad REAL)""")


def _migrar_esquema_anterior(con: sqlite3.Connection) -> None:
    """Versión anterior (sin periodos): conserva los datos reales en el mes en curso y descarta la demo."""
    columnas = [r[1] for r in con.execute("PRAGMA table_info(base)")]
    if not columnas or "periodo" in columnas:
        return
    try:
        fuente = con.execute("SELECT valor FROM config WHERE clave = 'fuente'").fetchone()
    except sqlite3.OperationalError:
        fuente = None
    fuente = fuente[0] if fuente else "demo"

    con.execute("ALTER TABLE base RENAME TO base_vieja")
    con.execute("ALTER TABLE registros RENAME TO registros_vieja")
    _crear_tablas(con)
    if fuente != "demo":
        periodo = hoy().strftime("%Y-%m")
        con.execute("INSERT INTO periodos VALUES (?, ?, ?)",
                    (periodo, fuente, ahora().isoformat(timespec="seconds")))
        con.execute("""INSERT INTO base SELECT ?, tienda, indicador, meta, real_base
                       FROM base_vieja""", (periodo,))
        con.execute("""INSERT INTO registros (periodo, ts, tienda, indicador, cantidad)
                       SELECT ?, ts, tienda, indicador, cantidad FROM registros_vieja""", (periodo,))
    con.execute("DROP TABLE base_vieja")
    con.execute("DROP TABLE registros_vieja")
    con.execute("DROP TABLE IF EXISTS config")
    con.commit()


def init_db() -> None:
    with closing(db()) as con:
        con.execute("PRAGMA journal_mode=WAL")
        _migrar_esquema_anterior(con)
        _crear_tablas(con)
        con.commit()


def listar_periodos() -> list:
    """Meses con metas cargadas, del más reciente al más antiguo."""
    with closing(db()) as con:
        return [r[0] for r in con.execute("SELECT periodo FROM periodos ORDER BY periodo DESC")]


def guardar_base(periodo: str, df: pd.DataFrame, fuente: str) -> None:
    """Guarda las metas de UN mes (reemplaza ese mes y reinicia sus registros; el resto no se toca)."""
    filas = [(periodo, str(t), str(i), float(m), float(r))
             for t, i, m, r in df[COLUMNAS].itertuples(index=False, name=None)]
    with closing(db()) as con, con:
        con.execute("DELETE FROM base WHERE periodo = ?", (periodo,))
        con.execute("DELETE FROM registros WHERE periodo = ?", (periodo,))
        con.executemany("INSERT INTO base VALUES (?, ?, ?, ?, ?)", filas)
        con.execute("INSERT OR REPLACE INTO periodos VALUES (?, ?, ?)",
                    (periodo, fuente, ahora().isoformat(timespec="seconds")))


def cargar_datos(periodo: str):
    """Real = Real de la base + suma de los registros en tiempo real de ese mes."""
    with closing(db()) as con:
        df = pd.read_sql_query(
            """SELECT b.tienda AS Tienda, b.indicador AS Indicador, b.meta AS Meta,
                      b.real_base + COALESCE(SUM(r.cantidad), 0) AS Real
               FROM base b
               LEFT JOIN registros r ON r.periodo = b.periodo
                                    AND r.tienda = b.tienda AND r.indicador = b.indicador
               WHERE b.periodo = ?
               GROUP BY b.tienda, b.indicador, b.meta, b.real_base""",
            con, params=(periodo,),
        )
        fuente = con.execute("SELECT fuente FROM periodos WHERE periodo = ?", (periodo,)).fetchone()
    df = ordenar_df(df)
    df["Cumplimiento (%)"] = df["Real"] / df["Meta"] * 100
    return df, (fuente[0] if fuente else "")


def registrar(periodo: str, tienda: str, indicador: str, cantidad: float) -> None:
    with closing(db()) as con, con:
        con.execute(
            "INSERT INTO registros (periodo, ts, tienda, indicador, cantidad) VALUES (?, ?, ?, ?, ?)",
            (periodo, ahora().isoformat(timespec="seconds"), tienda, indicador, float(cantidad)),
        )


def deshacer_ultimo(periodo: str) -> bool:
    with closing(db()) as con, con:
        fila = con.execute(
            "SELECT id FROM registros WHERE periodo = ? ORDER BY id DESC LIMIT 1", (periodo,)
        ).fetchone()
        if fila:
            con.execute("DELETE FROM registros WHERE id = ?", fila)
        return fila is not None


def registros_recientes(periodo: str, n: int = 8) -> pd.DataFrame:
    with closing(db()) as con:
        return pd.read_sql_query(
            """SELECT ts AS Fecha, tienda AS Tienda, indicador AS Indicador, cantidad AS Cantidad
               FROM registros WHERE periodo = ? ORDER BY id DESC LIMIT ?""",
            con, params=(periodo, n),
        )


def historico_mensual() -> pd.DataFrame:
    """Meta y Real de todos los meses guardados, por tienda e indicador."""
    with closing(db()) as con:
        return pd.read_sql_query(
            """SELECT b.periodo AS Periodo, b.tienda AS Tienda, b.indicador AS Indicador,
                      b.meta AS Meta, b.real_base + COALESCE(SUM(r.cantidad), 0) AS Real
               FROM base b
               LEFT JOIN registros r ON r.periodo = b.periodo
                                    AND r.tienda = b.tienda AND r.indicador = b.indicador
               GROUP BY b.periodo, b.tienda, b.indicador, b.meta, b.real_base""",
            con,
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
def generar_pdf(df: pd.DataFrame, generado: str, periodo_txt: str) -> bytes:
    """PDF de la Zona completa del mes. Se regenera solo cuando cambian los datos (o cada minuto)."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, title=f"Reporte Zona Coppel {periodo_txt}")
    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle("Titulo", parent=estilos["Heading1"], fontSize=18, leading=22,
                            textColor=colors.HexColor("#1F6FEB"), spaceAfter=6)
    h2 = ParagraphStyle("H2", parent=estilos["Heading2"], fontSize=12, spaceBefore=14, spaceAfter=6)
    texto = ParagraphStyle("Texto", parent=estilos["Normal"], fontSize=10, leading=14)

    story = [
        Paragraph("<b>Reporte de Cumplimiento de Metas — Zona Coppel</b>", titulo),
        Paragraph(f"Periodo: <b>{periodo_txt}</b> — Generado el {generado}", texto),
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


# ------------------------------------------------------------------ Comparativo mes contra mes
def tabla_comparativa(h, periodos, tienda_sel, metrica, actual, dia_hoy, dias_actual):
    """Indicadores en filas y meses en columnas (el más reciente a la derecha).

    Devuelve (tabla, nombre de la columna de variación o None), o None si no hay datos.
    """
    if tienda_sel != TODA_LA_ZONA:
        h = h[h["Tienda"] == tienda_sel]
    g = (h[h["Periodo"].isin(periodos)]
         .groupby(["Indicador", "Periodo"], as_index=False)[["Meta", "Real"]].sum())
    if g.empty:
        return None

    if metrica == "% de cumplimiento":
        # Mes en curso: meta prorrateada a hoy, para compararlo parejo contra meses ya cerrados
        factor = g["Periodo"].map(lambda p: dia_hoy / dias_actual if p == actual else 1.0)
        g["Valor"] = g["Real"] / (g["Meta"] * factor) * 100
    else:
        g["Valor"] = g[metrica]

    t = g.pivot(index="Indicador", columns="Periodo", values="Valor").reindex(columns=periodos)
    t = t.reindex(ordenar(t.index, INDICADORES))
    t.columns.name = None

    variacion = None
    # El Real del mes en curso está incompleto: no se compara contra un mes cerrado
    if len(periodos) >= 2 and not (metrica == "Real" and periodos[-1] == actual):
        ult, ant = periodos[-1], periodos[-2]
        variacion = "Δ vs mes anterior"
        t[variacion] = (t[ult] - t[ant]) if metrica == "% de cumplimiento" else (t[ult] / t[ant] - 1) * 100

    return t.rename(columns={p: etiqueta_corta(p, actual) for p in periodos}), variacion


def estilo_comparativo(t: pd.DataFrame, variacion, metrica: str):
    es_pct = metrica == "% de cumplimiento"
    cols_valor = [c for c in t.columns if c != variacion]

    def css_pct(p):
        if pd.isna(p):
            return ""
        _, fondo, texto, _ = SEMAFORO[nivel(p)]
        return f"background-color: {fondo}; color: {texto}"

    def css_var(v):
        if pd.isna(v):
            return ""
        return "color: #0B5D1E; font-weight: 600" if v >= 0 else "color: #8C1018; font-weight: 600"

    est = t.style
    if es_pct:
        est = est.apply(lambda d: d.apply(lambda c: c.map(css_pct)), axis=None, subset=cols_valor)
        est = est.format("{:.1f}%", subset=cols_valor, na_rep="—")
    else:
        est = est.format("{:,.0f}", subset=cols_valor, na_rep="—")
        if VENTAS in t.index:
            est = est.format("${:,.0f}", subset=pd.IndexSlice[[VENTAS], cols_valor], na_rep="—")
    if variacion:
        est = est.apply(lambda d: d.apply(lambda c: c.map(css_var)), axis=None, subset=[variacion])
        est = est.format("{:+.1f} pts" if es_pct else "{:+.1f}%", subset=[variacion], na_rep="—")
    return est


# ------------------------------------------------------------------ Carga de metas del mes
def bloque_carga(periodo: str, reemplaza: bool) -> None:
    """Espacio para subir las metas del mes. Solo afecta al mes indicado."""
    archivo = st.file_uploader(
        f"Metas de {nombre_periodo(periodo)} — columnas: Tienda, Indicador, Meta (Real es opcional)",
        type=["xlsx", "xlsm", "csv"], key=f"archivo_{periodo}",
    )
    st.download_button(
        "⬇️ Plantilla de ejemplo (CSV)",
        data=plantilla_metas().to_csv(index=False).encode("utf-8-sig"),
        file_name="plantilla_metas.csv", mime="text/csv", key=f"plantilla_{periodo}",
    )
    if archivo is None:
        return
    try:
        nuevo = preparar(leer_archivo(archivo))
    except Exception as e:
        st.error(f"No se pudo procesar el archivo: {e}")
        return
    st.success(f"Archivo válido: {len(nuevo)} filas, {nuevo['Tienda'].nunique()} tiendas.")
    if reemplaza:
        st.caption("⚠️ Reemplaza las metas de este mes y reinicia sus registros en tiempo real. "
                   "Los demás meses no se tocan.")
    if st.button("✅ Aplicar como metas del mes", key=f"aplicar_{periodo}"):
        guardar_base(periodo, nuevo, archivo.name)
        st.session_state["aviso"] = (
            f"Metas de {nombre_periodo(periodo)} cargadas." if not reemplaza
            else f"Metas de {nombre_periodo(periodo)} actualizadas."
        ) + (" Ya puedes registrar avances." if not reemplaza else "")
        st.rerun()


# ------------------------------------------------------------------ Interfaz
init_db()
actual = hoy().strftime("%Y-%m")  # mes en curso: cambia solo el día 1
existentes = listar_periodos()

st.sidebar.header("Periodo")
opciones = sorted(set(existentes) | {actual}, reverse=True)
periodo = st.sidebar.selectbox(
    "Mes a consultar:", opciones, format_func=nombre_periodo,
    key=f"periodo_{actual}",  # la clave cambia con el mes: al iniciar un mes nuevo vuelve al mes en curso
)
es_actual = periodo == actual
nombre = nombre_periodo(periodo)

st.title("📊 Tablero Zona Coppel — En Tiempo Real")

if "aviso" in st.session_state:  # mensaje de la acción anterior (tras st.rerun)
    st.success(st.session_state.pop("aviso"))

# Mes en curso sin metas (p. ej. el día 1): solo se muestra el espacio para subir el archivo
if periodo not in existentes:
    st.warning(
        f"📅 Inició **{nombre}**. Sube el archivo de metas del mes para empezar a registrar avances."
    )
    bloque_carga(periodo, reemplaza=False)
    if existentes:
        st.caption("Para consultar meses anteriores, elige el mes en el menú lateral.")
    st.stop()

df, fuente = cargar_datos(periodo)
tiendas = ordenar(df["Tienda"], TIENDAS)
indicadores = ordenar(df["Indicador"], INDICADORES)

if es_actual:
    st.caption(f"📄 Metas de {nombre}: {fuente}")
    col_carga, col_captura = st.columns(2)

    with col_carga:
        with st.expander("📁 Reemplazar metas del mes", expanded=False):
            bloque_carga(periodo, reemplaza=True)

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
                    registrar(periodo, t_form, i_form, cantidad)
                    st.session_state["aviso"] = (
                        f"Se sumaron {fmt(cantidad, i_form)} a {i_form} · Tienda {t_form}."
                    )
                    st.rerun()
                else:
                    st.error("Esa combinación de tienda e indicador no existe en las metas del mes.")

            if st.button("↩️ Deshacer último registro"):
                if deshacer_ultimo(periodo):
                    st.session_state["aviso"] = "Se deshizo el último registro."
                    st.rerun()
                else:
                    st.warning("No hay registros que deshacer.")

            recientes = registros_recientes(periodo)
            if not recientes.empty:
                st.caption("Últimos registros")
                st.dataframe(recientes, hide_index=True)
else:
    st.info(
        f"📚 Histórico de **{nombre}**: solo consulta. "
        f"Para registrar avances, elige el mes en curso ({nombre_periodo(actual)}) en el menú lateral."
    )

# ------------------------------------------------------------------ Filtros laterales
st.sidebar.header("Filtros de Zona")
tienda_sel = st.sidebar.selectbox("Tienda:", [TODA_LA_ZONA] + tiendas)
ind_sel = st.sidebar.selectbox("Indicador:", [TODOS_IND] + indicadores)

anio_p, mes_p = (int(x) for x in periodo.split("-"))
primer_dia = date(anio_p, mes_p, 1)
ultimo_dia = date(anio_p, mes_p, calendar.monthrange(anio_p, mes_p)[1])
if es_actual:
    fecha_corte = st.sidebar.date_input(
        "Fecha de corte:", value=hoy(), min_value=primer_dia, max_value=ultimo_dia,
        format="DD/MM/YYYY",
        help="Día hasta el que se calcula la meta al día de la tabla (por defecto, hoy).",
    )
else:
    fecha_corte = ultimo_dia  # mes cerrado: la meta al día es la meta completa del mes
    st.sidebar.caption(f"Mes cerrado: corte al {ultimo_dia:%d/%m/%Y}.")

base_semaforo = st.sidebar.radio(
    "Colorear semáforo según:", ["Avance al día", "Meta mensual"],
    help="Define qué porcentaje pinta las filas de la tabla de semáforo.",
)
st.sidebar.button("🔄 Actualizar datos", help="Vuelve a leer los registros de todas las tiendas.")
st.sidebar.divider()

if PDF_DISPONIBLE:
    st.sidebar.download_button(
        f"📥 Descargar Reporte PDF · {nombre}",
        data=generar_pdf(df, ahora().strftime("%d/%m/%Y %H:%M"), nombre),
        file_name=f"Reporte_Zona_Coppel_{periodo}.pdf", mime="application/pdf",
    )
else:
    st.sidebar.warning("Para habilitar el PDF instala reportlab:\n\n`pip install reportlab`")

# ------------------------------------------------------------------ Resumen (KPIs)
df_alcance = df if tienda_sel == TODA_LA_ZONA else df[df["Tienda"] == tienda_sel]
alcance = "Toda la Zona" if tienda_sel == TODA_LA_ZONA else f"Tienda {tienda_sel}"
st.subheader(f"📈 Resumen · {alcance} · {nombre}")

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

tabla = df_alcance if ind_sel == TODOS_IND else df_alcance[df_alcance["Indicador"] == ind_sel]
tabla, dias_mes = con_avance_diario(tabla, fecha_corte)
tabla = tabla.rename(columns={
    "Meta": "Meta mensual",
    "Cumplimiento (%)": "Cumpl. mensual (%)",
    "Cumplimiento al día (%)": "Cumpl. al día (%)",
})

col_semaforo = "Cumpl. al día (%)" if base_semaforo == "Avance al día" else "Cumpl. mensual (%)"
tabla["Semáforo"] = tabla[col_semaforo].map(lambda p: SEMAFORO[nivel(p)][0])
tabla = tabla[["Tienda", "Indicador", "Meta mensual", "Meta al día", "Real",
               "Cumpl. mensual (%)", "Cumpl. al día (%)", "Semáforo"]]

st.caption(
    f"Corte al **día {fecha_corte.day} de {dias_mes}** ({fecha_corte:%d/%m/%Y}). "
    f"Meta al día = Meta mensual ÷ {dias_mes} × {fecha_corte.day}, es decir, a esta fecha "
    f"debería llevarse el **{fecha_corte.day / dias_mes:.0%}** de la meta mensual. "
    f"Color de las filas según: **{base_semaforo.lower()}**."
)
st.markdown(
    "🟢 **≥ 100%** &nbsp;&nbsp; 🟡 **85% – 99.99%** &nbsp;&nbsp; "
    "🟠 **60% – 84.99%** &nbsp;&nbsp; 🔴 **< 60%**"
)


def pintar_fila(fila):
    _, fondo, texto, _ = SEMAFORO[nivel(fila[col_semaforo])]
    return [f"background-color: {fondo}; color: {texto}"] * len(fila)


estilo = tabla.style.apply(pintar_fila, axis=1).format({
    "Meta mensual": "{:,.0f}", "Meta al día": "{:,.0f}", "Real": "{:,.0f}",
    "Cumpl. mensual (%)": "{:.2f}%", "Cumpl. al día (%)": "{:.2f}%",
})
filas_ventas = tabla.index[tabla["Indicador"] == VENTAS]
if len(filas_ventas):
    estilo = estilo.format(
        "${:,.0f}", subset=pd.IndexSlice[filas_ventas, ["Meta mensual", "Meta al día", "Real"]]
    )

st.dataframe(estilo, hide_index=True, height=min(38 * (len(tabla) + 1) + 3, 800))

# ------------------------------------------------------------------ Comparativo mes contra mes
st.divider()
st.subheader("📅 Comparativo mes contra mes")

periodos_cmp = sorted(existentes)[-12:]  # últimos 12 meses guardados, del más antiguo al más reciente
if len(periodos_cmp) < 2:
    st.info("El comparativo aparecerá cuando tengas metas cargadas de al menos dos meses.")
else:
    metrica = st.radio("Comparar:", ["% de cumplimiento", "Real", "Meta"], horizontal=True, key="comp_metrica")
    dia_hoy = hoy().day
    dias_actual = calendar.monthrange(hoy().year, hoy().month)[1]
    resultado = tabla_comparativa(historico_mensual(), periodos_cmp, tienda_sel, metrica,
                                  actual, dia_hoy, dias_actual)
    if resultado is None:
        st.info(f"No hay datos de {alcance} en los meses guardados.")
    else:
        t_cmp, col_var = resultado
        notas = [f"Alcance: **{alcance}** (sigue el filtro de Tienda del menú lateral)."]
        if actual in periodos_cmp and metrica == "% de cumplimiento":
            notas.append(f"El mes en curso (*) se mide contra su meta al día {dia_hoy} de {dias_actual}; "
                         "los meses cerrados, al cierre.")
        elif actual in periodos_cmp and metrica == "Real":
            notas.append("En el mes en curso (*) el Real es el acumulado a hoy.")
        st.caption(" ".join(notas))
        st.dataframe(estilo_comparativo(t_cmp, col_var, metrica))
