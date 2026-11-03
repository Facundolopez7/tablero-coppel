"""
Tablero de Cumplimiento de Metas · Zona Coppel
----------------------------------------------
Ejecutar:  streamlit run app.py
Requiere:  pip install streamlit pandas numpy plotly openpyxl
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="Cumplimiento de Metas · Zona Coppel",
    page_icon="📊",
    layout="wide",
)

# ------------------------------------------------------------------ Configuración
TIENDAS = ["77", "110", "203", "668", "851", "6678", "6806"]
INDICADORES = [
    "Apps Coppel",
    "Cliente Digital Avanzado (CDA)",
    "1a. Compra en Canal Digital",
    "Ventas en Coppel.com",
]
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
    """Clasifica el % de cumplimiento (con 2 decimales, como en los rangos)."""
    pct = round(pct, 2)
    if pct >= 100:
        return "Verde"
    if pct >= 85:
        return "Amarillo"
    if pct >= 60:
        return "Naranja"
    return "Rojo"


def ordenar(valores, canonicos):
    """Primero el orden canónico; lo demás (si el archivo trae otros valores) al final."""
    valores = list(dict.fromkeys(valores))
    otros = sorted(
        (v for v in valores if v not in canonicos),
        key=lambda v: (0, int(v), v) if v.isdigit() else (1, 0, v),
    )
    return [v for v in canonicos if v in valores] + otros


# ------------------------------------------------------------------ Datos
@st.cache_data
def generar_demo() -> pd.DataFrame:
    """Datos ficticios: tiendas grandes con metas altas, chicas con metas bajas."""
    rng = np.random.default_rng(2026)
    tamano = {"77": 1.00, "110": 1.60, "203": 0.70, "668": 1.30,
              "851": 0.90, "6678": 0.55, "6806": 1.90}
    base = {
        INDICADORES[0]: 1_200,      # descargas / activaciones de la app
        INDICADORES[1]: 800,        # clientes digitales avanzados
        INDICADORES[2]: 450,        # primeras compras digitales
        INDICADORES[3]: 1_500_000,  # ventas en pesos
    }
    filas = []
    for tienda in TIENDAS:
        for ind in INDICADORES:
            redondeo = -3 if ind == INDICADORES[3] else 0
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
    return pd.read_excel(archivo)  # primera hoja del libro


def preparar(df: pd.DataFrame) -> pd.DataFrame:
    """Limpia, valida y calcula el % de cumplimiento = Real / Meta * 100."""
    df = df.copy()
    df.columns = [str(c).strip().capitalize() for c in df.columns]
    faltan = [c for c in COLUMNAS if c not in df.columns]
    if faltan:
        raise ValueError(
            f"Faltan las columnas: {', '.join(faltan)}. Se requieren: {', '.join(COLUMNAS)}."
        )
    df = df[COLUMNAS].copy()

    df["Tienda"] = df["Tienda"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    canon = {i.lower(): i for i in INDICADORES}
    df["Indicador"] = df["Indicador"].astype(str).str.strip().map(lambda x: canon.get(x.lower(), x))

    for col in ("Meta", "Real"):
        if not pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].astype(str).str.replace(r"[^\d.\-]", "", regex=True)
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Meta", "Real"])
    df = df[df["Meta"] > 0]
    if df.empty:
        raise ValueError("No hay filas válidas (Meta y Real numéricas, con Meta mayor a 0).")

    # Si hay varias filas por Tienda + Indicador, se suman
    df = df.groupby(["Tienda", "Indicador"], as_index=False)[["Meta", "Real"]].sum()
    df["Cumplimiento (%)"] = df["Real"] / df["Meta"] * 100

    orden = {
        "Tienda": {v: i for i, v in enumerate(ordenar(df["Tienda"], TIENDAS))},
        "Indicador": {v: i for i, v in enumerate(ordenar(df["Indicador"], INDICADORES))},
    }
    return df.sort_values(["Tienda", "Indicador"], key=lambda s: s.map(orden[s.name])).reset_index(drop=True)


# ------------------------------------------------------------------ Gráficas
def graficas(df: pd.DataFrame, ind: str, tienda_sel: str) -> None:
    """Dos gráficas para un indicador. Si hay una tienda seleccionada, se resalta."""
    d = df[df["Indicador"] == ind]
    col1, col2 = st.columns(2)

    # 1) Barras agrupadas: Meta vs Real
    op = [1.0 if tienda_sel in (TODA_LA_ZONA, t) else 0.3 for t in d["Tienda"]]
    fig1 = go.Figure()
    fig1.add_bar(x=d["Tienda"], y=d["Meta"], name="Meta",
                 marker=dict(color="#9AA5B1", opacity=op))
    fig1.add_bar(x=d["Tienda"], y=d["Real"], name="Real",
                 marker=dict(color="#1F6FEB", opacity=op))
    fig1.update_layout(
        title=f"Meta vs. Real · {ind}", barmode="group", height=420,
        xaxis=dict(type="category", title="Tienda"),
        yaxis=dict(tickformat=",.0f"),
        legend=dict(orientation="h", y=1.12, x=0),
        margin=dict(t=80, b=10, l=10, r=10),
    )
    with col1:
        st.plotly_chart(fig1, key=f"meta_real_{ind}")

    # 2) Ranking de % de cumplimiento (mayor a menor)
    r = d.sort_values("Cumplimiento (%)", ascending=False)
    op_r = [1.0 if tienda_sel in (TODA_LA_ZONA, t) else 0.3 for t in r["Tienda"]]
    colores = [SEMAFORO[nivel(p)][3] for p in r["Cumplimiento (%)"]]
    fig2 = go.Figure(go.Bar(
        x=r["Tienda"], y=r["Cumplimiento (%)"],
        text=[f"{p:.1f}%" for p in r["Cumplimiento (%)"]], textposition="outside",
        marker=dict(color=colores, opacity=op_r),
        hovertemplate="Tienda %{x}<br>Cumplimiento: %{y:.2f}%<extra></extra>",
    ))
    fig2.add_hline(y=100, line_dash="dash", line_color="#6B7280",
                   annotation_text="Meta 100%", annotation_position="top left")
    fig2.update_layout(
        title=f"Ranking de cumplimiento (%) · {ind}", height=420,
        xaxis=dict(type="category", title="Tienda"),
        yaxis=dict(title="% de cumplimiento", range=[0, max(115, r["Cumplimiento (%)"].max() * 1.15)]),
        margin=dict(t=80, b=10, l=10, r=10), showlegend=False,
    )
    with col2:
        st.plotly_chart(fig2, key=f"ranking_{ind}")


# ------------------------------------------------------------------ Interfaz
st.title("📊 Tablero de Cumplimiento de Metas — Zona Coppel")

# 1) Carga dinámica de datos
with st.expander("📁 Cargar datos del mes (Excel o CSV)", expanded=True):
    archivo = st.file_uploader(
        "Archivo con las columnas: Tienda, Indicador, Meta, Real",
        type=["xlsx", "xlsm", "csv"],
    )
    st.caption(
        "Las metas pueden cambiar cada mes: solo sube el archivo actualizado. "
        "Si no subes nada, se muestran datos ficticios (Modo Demo). "
        "Si hay varias filas por Tienda + Indicador, se suman."
    )
    st.download_button(
        "⬇️ Descargar plantilla de ejemplo (CSV)",
        data=generar_demo().to_csv(index=False).encode("utf-8-sig"),
        file_name="plantilla_metas.csv",
        mime="text/csv",
    )

if archivo is not None:
    try:
        df = preparar(leer_archivo(archivo))
        st.caption(f"📄 Fuente de datos: {archivo.name}")
    except Exception as e:
        st.error(f"No se pudo procesar el archivo: {e}")
        st.stop()
else:
    df = preparar(generar_demo())  # 2) Modo Demo
    st.info("🧪 Modo Demo: estás viendo datos ficticios. Sube tu archivo para ver los datos reales.")

tiendas = ordenar(df["Tienda"], TIENDAS)
indicadores = ordenar(df["Indicador"], INDICADORES)

# 3) Filtros interactivos
st.sidebar.header("🔎 Filtros")
tienda_sel = st.sidebar.selectbox("Tienda", [TODA_LA_ZONA] + tiendas)
ind_sel = st.sidebar.selectbox("Indicador", indicadores + [TODOS_IND])
st.sidebar.caption(
    "Las tarjetas superiores siempre muestran la Zona completa. "
    "En las gráficas, la tienda elegida se resalta; la tabla se filtra."
)

# 4) Resumen ejecutivo de la Zona (no depende de los filtros)
st.subheader("Resumen ejecutivo de la Zona")
cols = st.columns(len(indicadores))
for col, ind in zip(cols, indicadores):
    d = df[df["Indicador"] == ind]
    promedio = d["Cumplimiento (%)"].mean()
    ponderado = d["Real"].sum() / d["Meta"].sum() * 100
    col.metric(
        label=ind,
        value=f"{promedio:.1f}%",
        delta=f"{promedio - 100:+.1f} pts vs. 100%",
        help=(f"Promedio simple del % de cumplimiento de {len(d)} tiendas. "
              f"Ponderado por meta (Σ Real / Σ Meta): {ponderado:.1f}%."),
    )

st.divider()

# 5) Gráficas
st.subheader("Visualización")
if ind_sel == TODOS_IND:
    for tab, ind in zip(st.tabs(indicadores), indicadores):
        with tab:
            graficas(df, ind, tienda_sel)
else:
    graficas(df, ind_sel, tienda_sel)

st.divider()

# 6) Tabla de semáforo
st.subheader("Semáforo de cumplimiento")
st.markdown(
    "🟢 **≥ 100%** &nbsp;&nbsp; 🟡 **85% – 99.99%** &nbsp;&nbsp; "
    "🟠 **60% – 84.99%** &nbsp;&nbsp; 🔴 **< 60%**"
)

tabla = df.copy()
if tienda_sel != TODA_LA_ZONA:
    tabla = tabla[tabla["Tienda"] == tienda_sel]
if ind_sel != TODOS_IND:
    tabla = tabla[tabla["Indicador"] == ind_sel]
tabla["Semáforo"] = tabla["Cumplimiento (%)"].map(lambda p: SEMAFORO[nivel(p)][0])


def pintar_fila(fila):
    _, fondo, texto, _ = SEMAFORO[nivel(fila["Cumplimiento (%)"])]
    return [f"background-color: {fondo}; color: {texto}"] * len(fila)


estilo = tabla.style.apply(pintar_fila, axis=1).format(
    {"Meta": "{:,.0f}", "Real": "{:,.0f}", "Cumplimiento (%)": "{:.2f}%"}
)
st.dataframe(estilo, hide_index=True, height=min(38 * (len(tabla) + 1) + 3, 800))
