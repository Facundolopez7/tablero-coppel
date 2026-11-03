"""
Tablero de Cumplimiento de Metas · Zona Coppel (Con Control Proporcional Al Día y PDF)
---------------------------------------------------------------------------------
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
import io
from datetime import datetime
import calendar

st.set_page_config(
    page_title="Cumplimiento Coppel · Zona",
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

SEMAFORO = {
    "Verde": ("🟢 Verde", "#C6EFCE", "#0B5D1E", "#2E9E4F"),
    "Amarillo": ("🟡 Amarillo", "#FFF2B3", "#6B5300", "#F2C230"),
    "Naranja": ("🟠 Naranja", "#FFD9B3", "#7A3B00", "#F28C28"),
    "Rojo": ("🔴 Rojo", "#FFC7CE", "#8C1018", "#D64541"),
}

def nivel(pct: float) -> str:
    pct = round(pct, 2)
    if pct >= 100: return "Verde"
    if pct >= 85: return "Amarillo"
    if pct >= 60: return "Naranja"
    return "Rojo"

def ordenar(valores, canonicos):
    valores = list(dict.fromkeys(valores))
    otros = sorted((v for v in valores if v not in canonicos))
    return [v for v in canonicos if v in valores] + otros

# ------------------------------------------------------------------ Inicializar Estado de Datos
if "df_base" not in st.session_state:
    rng = np.random.default_rng(2026)
    tamano = {"77": 1.00, "110": 1.60, "203": 0.70, "668": 1.30, "851": 0.90, "6678": 0.55, "6806": 1.90}
    base = {INDICADORES[0]: 1200, INDICADORES[1]: 800, INDICADORES[2]: 450, INDICADORES[3]: 1500000}
    filas = []
    for tienda in TIENDAS:
        for ind in INDICADORES:
            redondeo = -3 if ind == INDICADORES[3] else 0
            meta = int(round(base[ind] * tamano[tienda] * rng.uniform(0.92, 1.08), redondeo))
            real = int(meta * rng.uniform(0.50, 1.15))
            filas.append({"Tienda": tienda, "Indicador": ind, "Meta": meta, "Real": real})
    st.session_state.df_base = pd.DataFrame(filas)

def leer_archivo(archivo) -> pd.DataFrame:
    if archivo.name.lower().endswith(".csv"):
        return pd.read_csv(archivo, encoding="utf-8-sig")
    return pd.read_excel(archivo)

def preparar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().capitalize() for c in df.columns]
    df = df[COLUMNAS].copy()
    df["Tienda"] = df["Tienda"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    canon = {i.lower(): i for i in INDICADORES}
    df["Indicador"] = df["Indicador"].astype(str).str.strip().map(lambda x: canon.get(x.lower(), x))
    for col in ("Meta", "Real"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna().groupby(["Tienda", "Indicador"], as_index=False)[["Meta", "Real"]].sum()
    return df

# ------------------------------------------------------------------ Cálculo de Fechas Dinámicas
hoy = datetime.now()
dia_actual = hoy.day
ultimo_dia_mes = calendar.monthrange(hoy.year, hoy.month)[1]

# ------------------------------------------------------------------ Filtros Laterales (Mover arriba para usar variables globales)
st.sidebar.header("Filtros de Zona")

# Filtro interactivo del día del mes evaluado
dia_evaluado = st.sidebar.slider(
    "Día del mes a evaluar:", 
    min_value=1, 
    max_value=ultimo_dia_mes, 
    value=min(dia_actual, ultimo_dia_mes)
)

st.sidebar.write(f"Evaluando ritmo a **{dia_evaluado}/{ultimo_dia_mes} días** del mes.")

# ------------------------------------------------------------------ Función para Reporte PDF
def generar_pdf(df_datos, dia_ev, total_dias):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, title="Reporte Coppel")
    story = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontSize=16, leading=20, textColor=colors.HexColor("#1F6FEB"), spaceAfter=15)
    text_style = ParagraphStyle('TextStyle', parent=styles['Normal'], fontSize=10, leading=14)
    
    story.append(Paragraph(f"<b>Reporte de Cumplimiento de Metas — Zona Coppel</b>", title_style))
    story.append(Paragraph(f"Resultados calculados proporcionalmente al <b>Día {dia_ev} de {total_dias}</b> del mes.", text_style))
    story.append(Spacer(1, 15))
    
    tabla_data = [["Tienda", "Indicador", "Meta Mes", "Real", "% Cumpl. Mes", "Meta al Día", "% Cumpl. al Día"]]
    for _, fila in df_datos.iterrows():
        meta_dia = (fila['Meta'] / total_dias) * dia_ev
        cumpl_mes = (fila['Real'] / fila['Meta']) * 100
        cumpl_dia = (fila['Real'] / meta_dia) * 100 if meta_dia > 0 else 0.0
        
        f_meta = f"${fila['Meta']:,.0f}" if fila['Indicador'] == "Ventas en Coppel.com" else f"{fila['Meta']:,.0f}"
        f_real = f"${fila['Real']:,.0f}" if fila['Indicador'] == "Ventas en Coppel.com" else f"{fila['Real']:,.0f}"
        f_meta_dia = f"${meta_dia:,.0f}" if fila['Indicador'] == "Ventas en Coppel.com" else f"{meta_dia:,.0f}"
        
        tabla_data.append([
            fila['Tienda'],
            fila['Indicador'],
            f_meta,
            f_real,
            f"{cumpl_mes:.1f}%",
            f_meta_dia,
            f"{cumpl_dia:.1f}%"
        ])
    
    t = Table(tabla_data, colWidths=[40, 140, 65, 65, 65, 65, 65])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1F6FEB")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('FONTSIZE', (0,0), (-1,-1), 8),
    ]))
    story.append(t)
    doc.build(story)
    buffer.seek(0)
    return buffer

# ------------------------------------------------------------------ Interfaz Gráfica
st.title("📊 Tablero Zona Coppel — Ritmo de Avance Diario")

# Captura en Tiempo Real
col_carga, col_captura = st.columns([1, 1])

with col_carga:
    with st.expander("📁 Cargar Excel / CSV completo", expanded=False):
        archivo = st.file_uploader("Actualizar mes completo:", type=["xlsx", "csv"])
        if archivo is not None:
            try:
                st.session_state.df_base = preparar(leer_archivo(archivo))
                st.success("¡Base de datos del mes actualizada!")
            except Exception as e:
                st.error(f"Error: {e}")

with col_captura:
    with st.expander("➕ Registro de Avances en Tiempo Real", expanded=False):
        t_form = st.selectbox("Tienda que reporta:", TIENDAS, key="t_form")
        i_form = st.selectbox("Indicador:", INDICADORES, key="i_form")
        label_unidad = "Monto en Pesos ($):" if i_form == "Ventas en Coppel.com" else "Cantidad (Unidades):"
        valor_sumar = st.number_input(label_unidad, min_value=1, value=1, step=1)
        
        if st.button("⚡ Registrar Impacto Inmediato"):
            idx = (st.session_state.df_base["Tienda"] == t_form) & (st.session_state.df_base["Indicador"] == i_form)
            if idx.any():
                st.session_state.df_base.loc[idx, "Real"] += valor_sumar
                st.success(f"¡Se sumaron {valor_sumar} al Real de la Tienda {t_form}!")
            else:
                st.error("No se encontró la combinación de tienda/indicador.")

# Procesar datos consolidados
df = st.session_state.df_base.copy()

# ------------------------------------------------------------------ Completar Filtros Laterales
tienda_sel = st.sidebar.selectbox("Seleccionar Tienda:", [TODA_LA_ZONA] + ordenar(df["Tienda"].unique(), TIENDAS))
ind_sel = st.sidebar.selectbox("Seleccionar Indicador:", [TODOS_IND] + ordenar(df["Indicador"].unique(), INDICADORES))

# Botón para descargar PDF
st.sidebar.write("---")
pdf_data = generar_pdf(df, dia_evaluado, ultimo_dia_mes)
st.sidebar.download_button(
    label="📥 Descargar Reporte PDF",
    data=pdf_data,
    file_name=f"Reporte_Zona_Coppel_Dia_{dia_evaluado}.pdf",
    mime="application/pdf"
)

# ------------------------------------------------------------------ KPIs Visuales (Ajustados al Ritmo Diario)
st.subheader(f"📈 Cumplimiento Acumulado al Día {dia_evaluado}")
df_resumen = df if tienda_sel == TODA_LA_ZONA else df[df["Tienda"] == tienda_sel]

for i, ind in enumerate(INDICADORES):
    d_ind = df_resumen[df_resumen["Indicador"] == ind]
    if not d_ind.empty:
        total_meta_mes = d_ind["Meta"].sum()
        total_real = d_ind["Real"].sum()
        
        # Meta calculada proporcional al día seleccionado
        total_meta_dia = (total_meta_mes / ultimo_dia_mes) * dia_evaluado
        cumplimiento_dia = (total_real / total_meta_dia) * 100 if total_meta_dia > 0 else 0
        
        formato_real = f"${total_real:,.0f}" if i == 3 else f"{total_real:,.0f}"
        formato_meta_dia = f"${total_meta_dia:,.0f}" if i == 3 else f"{total_meta_dia:,.0f}"
        
        st.metric(
            label=f"{ind} (% Ritmo Diario)", 
            value=f"{cumplimiento_dia:.1f}%", 
            delta=f"Llevas: {formato_real} de {formato_meta_dia} presupuestados"
        )

# Gráficas dinámicas ajustadas
def graficar_movil(df_plot, ind):
    d = df_plot[df_plot["Indicador"] == ind].copy()
    d["Meta_Dia"] = (d["Meta"] / ultimo_dia_mes) * dia_evaluado
    
    fig = go.Figure()
    fig.add_bar(x=d["Tienda"], y=d["Meta_Dia"], name="Meta Proporcional al Día", marker_color="#9AA5B1")
    fig.add_bar(x=d["Tienda"], y=d["Real"], name="Real Avanzado", marker_color="#1F6FEB")
