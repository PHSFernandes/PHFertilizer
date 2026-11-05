import io
import math
import os
import requests
from itertools import combinations
from urllib.parse import quote

import pandas as pd
import streamlit as st
from PIL import Image as PILImage
from pulp import (
    LpMinimize,
    LpProblem,
    LpStatus,
    LpVariable,
    PULP_CBC_CMD,
    lpSum,
    value,
)

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import Image as RLImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether
from reportlab.pdfgen import canvas

# --------------------------------------------------------
# Configuração básica da página
# --------------------------------------------------------

st.set_page_config(page_title="Otimizador de Misturas NPK+C", layout="wide")

POSSIVEIS_CAMINHOS = [
    "PHFertilizer/innovaterra - logomarca modificada por PH.png",
    "innovaterra - logomarca modificada por PH.png",
    "logo.png",
    "PHFertilizer/logo.png"
]

def obter_bytes_logo():
    for caminho in POSSIVEIS_CAMINHOS:
        if os.path.exists(caminho):
            try:
                pil_img = PILImage.open(caminho)
                if pil_img.mode not in ('RGB', 'RGBA'):
                    pil_img = pil_img.convert('RGBA')
                
                buffer_out = io.BytesIO()
                pil_img.save(buffer_out, format="PNG")
                buffer_out.seek(0)
                return buffer_out.getvalue(), caminho
            except Exception:
                continue
    return None, None

LOGO_BYTES, LOGO_PATH = obter_bytes_logo()

# IDs das planilhas Google Sheets
INSUMOS_SHEET_ID = "1rUIG49XF9eszt-4c_iS45bNLMEFlgpokVDG-qwFOF1k"
MAT_PRIMAS_SHEET_ID = "112DvPDCPnzkfIlexxtwQIbOUf5Pm6WlznEUhz7K_9_E"
COMPAT_SHEET_ID = "1D1VxuaaviKd73ccyn8ltWEnvlss2e0mK_K5j7zBo8zQ"

INSUMOS_SHEET_NAME = "Insumos"

NUTRIENTES_PRINCIPAIS = ["C_pct", "N_pct", "P2O5_pct", "K2O_pct"]
NUTRIENTES_ADICIONAIS = [
    "CaO_pct", "MgO_pct", "S_pct", "B_pct", "Cl_pct",
    "Cu_pct", "Fe_pct", "Mn_pct", "Mo_pct", "Ni_pct", "Zn_pct",
]
NUTRIENTES_TODOS = NUTRIENTES_PRINCIPAIS + NUTRIENTES_ADICIONAIS

ROTULOS = {
    "C_pct": "Carbono (C) %", "N_pct": "Nitrogênio (N) %", "P2O5_pct": "P₂O₅ %",
    "K2O_pct": "K₂O %", "CaO_pct": "CaO %", "MgO_pct": "MgO %", "S_pct": "Enxofre (S) %",
    "B_pct": "Boro (B) %", "Cl_pct": "Cloro (Cl) %", "Cu_pct": "Cobre (Cu) %",
    "Fe_pct": "Ferro (Fe) %", "Mn_pct": "Manganês (Mn) %", "Mo_pct": "Molibdênio (Mo) %",
    "Ni_pct": "Níquel (Ni) %", "Zn_pct": "Zinco (Zn) %",
}

def gs_csv_url(sheet_id: str, sheet_name: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={quote(sheet_name)}"

def eh_bioclastico(nome: str) -> bool:
    return "biocl" in str(nome).lower()

def eh_insumo_discreto(nome_insumo: str) -> bool:
    n = str(nome_insumo).lower()
    return "big bag" in n or "bigbag" in n or "pallet" in n or "palete" in n

def calcular_custos_insumos(insumos_selecionados: list, massa_ton: float):
    custo_total_lote_usd = 0.0
    detalhes_insumos = []
    unidades_inteiras = max(1, math.ceil(massa_ton))

    for item in insumos_selecionados:
        preco_unit = float(item["Preco_usd_ton"])
        if eh_insumo_discreto(item["Insumo"]):
            custo_item_lote = unidades_inteiras * preco_unit
            qtd_desc = f"{unidades_inteiras} un"
        else:
            custo_item_lote = massa_ton * preco_unit
            qtd_desc = f"{massa_ton:.2f} t"

        custo_total_lote_usd += custo_item_lote
        detalhes_insumos.append({
            "Insumo": item["Insumo"],
            "Preco_base_usd": preco_unit,
            "Quantidade_aplicada": qtd_desc,
            "Custo_lote_usd": custo_item_lote,
            "Custo_ton_usd": custo_item_lote / massa_ton if massa_ton > 0 else 0.0
        })

    custo_total_ton_usd = custo_total_lote_usd / massa_ton if massa_ton > 0 else 0.0
    return custo_total_ton_usd, custo_total_lote_usd, detalhes_insumos


# --------------------------------------------------------
# Carregamento Dinâmico dos dados via Google Sheets
# --------------------------------------------------------

@st.cache_data(ttl=300)
def carregar_insumos() -> pd.DataFrame:
    url = gs_csv_url(INSUMOS_SHEET_ID, INSUMOS_SHEET_NAME)
    df = pd.read_csv(url)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")
    colmap = {}
    for c in df.columns:
        c_lower = c.lower()
        if "insumo" in c_lower: colmap[c] = "Insumo"
        elif "unidade" in c_lower: colmap[c] = "Unidade_por_ton"
        elif "quantidade" in c_lower: colmap[c] = "Quantidade_por_ton"
        elif "preço" in c_lower or "preco" in c_lower: colmap[c] = "Preco_usd_ton"
    df = df.rename(columns=colmap)
    for col in ["Insumo", "Preco_usd_ton"]:
        if col not in df.columns: df[col] = "" if col == "Insumo" else 0.0
    if "Quantidade_por_ton" in df.columns:
        df["Quantidade_por_ton"] = df["Quantidade_por_ton"].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
        df["Quantidade_por_ton"] = pd.to_numeric(df["Quantidade_por_ton"], errors="coerce").fillna(0.0)
    df["Preco_usd_ton"] = df["Preco_usd_ton"].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    df["Preco_usd_ton"] = pd.to_numeric(df["Preco_usd_ton"], errors="coerce").fillna(0.0)
    df["Insumo"] = df["Insumo"].astype(str).str.strip()
    return df[df["Insumo"] != ""].copy()

@st.cache_data(ttl=300)
def carregar_mat_primas(sheet_name: str) -> pd.DataFrame:
    url = gs_csv_url(MAT_PRIMAS_SHEET_ID, sheet_name)
    df = pd.read_csv(url)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")

    expected_cols = [
        "Ingrediente", "Preco_ton", "Umidade_pct", "MO_ms_pct", "C_pct", "N_pct", 
        "P2O5_pct", "K2O_pct", "CaO_pct", "MgO_pct", "S_pct", "B_pct", "Cl_pct", 
        "Cu_pct", "Fe_pct", "Mn_pct", "Mo_pct", "Ni_pct", "Zn_pct", "Tipo_funcao"
    ]
    for col in expected_cols:
        if col not in df.columns:
            df[col] = "" if col in ["Ingrediente", "Tipo_funcao"] else 0.0

    num_cols = [c for c in expected_cols if c not in ["Ingrediente", "Tipo_funcao"]]
    for col in num_cols:
        df[col] = df[col].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df["Ingrediente"] = df["Ingrediente"].astype(str).str.strip()
    df["Tipo_funcao"] = df["Tipo_funcao"].astype(str).str.strip().str.upper()
    df = df[df["Ingrediente"] != ""].copy()

    mask_c_zero = df["C_pct"].fillna(0).eq(0) & df["MO_ms_pct"].gt(0)
    df.loc[mask_c_zero, "C_pct"] = 0.58 * df.loc[mask_c_zero, "MO_ms_pct"] * (1 - df.loc[mask_c_zero, "Umidade_pct"] / 100.0)
    return df

@st.cache_data(ttl=300)
def carregar_compatibilidade(sheet_name: str) -> pd.DataFrame:
    url = gs_csv_url(COMPAT_SHEET_ID, sheet_name)
    df = pd.read_csv(url, header=None)
    df = df.dropna(how="all")
    header_raw = df.iloc[0].tolist()
    nomes = [str(x).strip() for x in header_raw if pd.notna(x) and str(x).strip() != ""]
    matriz = df.iloc[1 : 1 + len(nomes), : len(nomes) + 1].copy()
    matriz.columns = ["Ingrediente"] + nomes
    matriz["Ingrediente"] = matriz["Ingrediente"].astype(str).str.strip()
    for col in nomes:
        matriz[col] = matriz[col].astype(str).str.strip().str.upper()
    return matriz.set_index("Ingrediente")


def obter_status(matriz: pd.DataFrame, a: str, b: str) -> str:
    if a == b: return "COMPATÍVEL"
    try:
        if a in matriz.index and b in matriz.columns:
            val = str(matriz.loc[a, b]).strip().upper()
            if val and val not in ["NAN", "-"]: return val
        if b in matriz.index and a in matriz.columns:
            val = str(matriz.loc[b, a]).strip().upper()
            if val and val not in ["NAN", "-"]: return val
    except Exception:
        pass
    return "COMPATÍVEL"

def classificar_compatibilidade(selecionados: list[str], matriz: pd.DataFrame):
    incompat, limitados = [], []
    for a, b in combinations(selecionados, 2):
        status = obter_status(matriz, a, b)
        if status == "INCOMPATÍVEL": incompat.append((a, b))
        elif status == "LIMITADO": limitados.append((a, b))
    return incompat, limitados

def obter_cotacao_usd_brl_api() -> float | None:
    try:
        resp = requests.get("https://economia.awesomeapi.com.br/last/USD-BRL", timeout=5)
        if resp.status_code == 200:
            return float(resp.json()["USDBRL"]["bid"])
    except Exception:
        pass
    return None

# --------------------------------------------------------
# Otimizador
# --------------------------------------------------------

def resolver_base_ativa(ativos: pd.DataFrame, metas_principais: dict, metas_extra: dict, tol: float, massa_minima: float, usar_bioclastico: bool, pct_bioclastico: float):
    metas_todas = metas_principais.copy()
    metas_todas.update(metas_extra)
    nutrientes_com_meta = [col for col, alvo in metas_todas.items() if alvo > 0]

    def tem_contribuicao(row):
        if eh_bioclastico(row["Ingrediente"]): return True
        for col in nutrientes_com_meta:
            if float(row.get(col, 0.0)) > 0: return True
        for col in NUTRIENTES_PRINCIPAIS:
            if float(row.get(col, 0.0)) > 0: return True
        return False

    ativos = ativos[ativos.apply(tem_contribuicao, axis=1)].copy()
    if ativos.empty:
        return None, "Nenhuma matéria-prima disponível no momento possui teores para suprir os nutrientes selecionados.", None

    prob = LpProblem("Mistura_NPK_C_Ativos", LpMinimize)
    x = {i: LpVariable(f"x_{i}", lowBound=0.0) for i in ativos.index}
    slack_nutrientes = {col: LpVariable(f"slack_{col}", lowBound=0.0) for col in nutrientes_com_meta}

    PENALIDADE_SLACK = 1e5
    prob += (
        lpSum(x[i] * float(ativos.loc[i, "Preco_ton"]) / 1000.0 for i in ativos.index) +
        lpSum(slack_nutrientes[col] * PENALIDADE_SLACK for col in slack_nutrientes)
    )

    total_ativos = lpSum(x[i] for i in ativos.index)
    prob += total_ativos >= massa_minima

    if usar_bioclastico:
        bio_indices = [i for i in ativos.index if eh_bioclastico(ativos.loc[i, "Ingrediente"])]
        if bio_indices:
            idx_bio = bio_indices[0]
            prob += x[idx_bio] == (pct_bioclastico / 100.0) * total_ativos

    for col in nutrientes_com_meta:
        alvo = metas_todas[col]
        fator_tol = tol / 100.0
        minimo = max(0.0, alvo * (1 - fator_tol))
        prob += (lpSum(x[i] * (float(ativos.loc[i, col]) - minimo) / 100.0 for i in ativos.index) + slack_nutrientes[col] >= 0)
        
        if tol > 0:
            maximo = alvo * (1 + fator_tol)
            prob += (lpSum(x[i] * (float(ativos.loc[i, col]) - maximo) / 100.0 for i in ativos.index) <= 0)

    prob.solve(PULP_CBC_CMD(msg=False))
    status = LpStatus[prob.status]

    if status != "Optimal":
        return None, f"Modelo de otimização sem solução. Status: {status}", None

    total_kg_calc = sum(value(x[i]) for i in ativos.index)
    deficits = {}
    for col, var in slack_nutrientes.items():
        v = value(var)
        if v is not None and v > 1e-4:
            pct_faltante = (v / total_kg_calc) * 100.0 if total_kg_calc > 0 else 0.0
            deficits[ROTULOS.get(col, col)] = round(pct_faltante, 3)

    if deficits:
        msg_diag = "⚠️ **Diagnóstico Técnico de Viabilidade**: Não foi possível suprir os nutrientes mesmo flexibilizando a massa.\n\n**Déficit identificado em relação ao piso mínimo:**\n"
        for nut, d_val in deficits.items():
            msg_diag += f"- **{nut}**: Falta incorporar **{d_val}%**.\n"
        return None, msg_diag, None

    sol = ativos.copy()
    sol["Quantidade_kg"] = [value(x[i]) for i in ativos.index]
    sol = sol[sol["Quantidade_kg"] > 1e-6].copy()
    total_kg = sol["Quantidade_kg"].sum()
    sol["Participacao_pct"] = 100 * sol["Quantidade_kg"] / total_kg
    sol["Custo_total"] = sol["Quantidade_kg"] * sol["Preco_ton"] / 1000.0
    info = {"massa_ativos": total_kg, "custo_ativos": sol["Custo_total"].sum()}
    return sol, status, info

def escolher_inerte(sol_ativos: pd.DataFrame, base: pd.DataFrame, matriz: pd.DataFrame, massa_minima: float):
    massa_ativos = sol_ativos["Quantidade_kg"].sum()
    faltante = massa_minima - massa_ativos
    if faltante <= 1e-6: return None, None, [], []

    inertes = base[base["Tipo_funcao"] == "INERTE"].copy()
    presentes = sol_ativos["Ingrediente"].tolist()
    compativeis, limitados, motivos_incompat = [], [], []

    for _, row in inertes.iterrows():
        nome = row["Ingrediente"]
        status_list = [obter_status(matriz, nome, outro) for outro in presentes]
        pares_incompat = [outro for outro, s in zip(presentes, status_list) if s == "INCOMPATÍVEL"]
        pares_limitado = [outro for outro, s in zip(presentes, status_list) if s == "LIMITADO"]

        if pares_incompat:
            motivos_incompat.append(f"{nome}: incompatível com " + ", ".join(pares_incompat))
            continue

        registro = row.copy()
        registro["Quantidade_kg"] = faltante
        if pares_limitado: limitados.append((registro, pares_limitado))
        else: compativeis.append(registro)

    compativeis_sorted = sorted(compativeis, key=lambda r: float(r["Preco_ton"]))
    limitados_sorted = sorted(limitados, key=lambda t: float(t[0]["Preco_ton"]))

    if compativeis_sorted: return compativeis_sorted[0], None, compativeis_sorted, limitados_sorted
    if limitados_sorted:
        nomes = ", ".join([str(t[0]["Ingrediente"]) for t in limitados_sorted])
        return None, "Nenhum inerte totalmente compatível. Somente inertes em condição LIMITADO: " + nomes, compativeis_sorted, limitados_sorted
    if motivos_incompat: return None, "Nenhum material inerte elegível. Motivos: " + "; ".join(motivos_incompat), compativeis_sorted, limitados_sorted
    return None, "Nenhum material inerte disponível.", compativeis_sorted, limitados_sorted

def resumo_nutrientes_completo(df_resultado: pd.DataFrame) -> pd.DataFrame:
    total_kg = df_resultado["Quantidade_kg"].sum()
    linhas = []
    for col in NUTRIENTES_TODOS:
        if col in df_resultado.columns:
            teor_final = (df_resultado["Quantidade_kg"] * df_resultado[col] / 100.0).sum() / total_kg * 100
            linhas.append({"Nutriente": ROTULOS.get(col, col), "Teor final (%)": round(teor_final, 3)})
    return pd.DataFrame(linhas)

# --------------------------------------------------------
# Exportação PDF
# --------------------------------------------------------

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count):
        self.setFont("Helvetica", 9)
        self.drawRightString(self._pagesize[0] - 30, 20, f"Página {self._pageNumber} / {page_count}")

def gerar_pdf_a4_paisagem(df_ingredientes, df_resumo_econ, df_nutrientes, detalhes_insumos, tipo_origem="Sugestão do Sistema"):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontSize=17, textColor=colors.HexColor('#1b4332'), spaceAfter=2)
    subtitle_style = ParagraphStyle('DocSubTitle', parent=styles['Heading2'], fontSize=12, textColor=colors.HexColor('#2d6a4f'), spaceBefore=14, spaceAfter=5)
    cell_style = ParagraphStyle('CellText', parent=styles['Normal'], fontSize=8, alignment=1)

    def sanitizar_texto_pdf(txt): return str(txt).replace("₂", "<sub>2</sub>").replace("₅", "<sub>5</sub>")

    story = []
    img_element = RLImage(io.BytesIO(LOGO_BYTES), width=140, height=50) if LOGO_BYTES else None
    text_block = [Paragraph("<b>INNOVATERRA AGRISOLUTIONS</b>", title_style), Paragraph(f"<b>Relatório de Otimização — Modalidade: {tipo_origem}</b>", styles['Normal'])]

    if img_element:
        header_table = Table([[img_element, text_block]], colWidths=[150, 630])
        header_table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
        story.append(header_table)
    else:
        story.append(Paragraph("INNOVATERRA AGRISOLUTIONS", title_style))
        story.append(Paragraph(f"Relatório de Otimização — Modalidade: {tipo_origem}", styles['Heading3']))

    story.append(Spacer(1, 10))
    story.append(Paragraph("Composição das Matérias-Primas", subtitle_style))
    
    data_ing = [[Paragraph(f"<b>{sanitizar_texto_pdf(c)}</b>", cell_style) for c in df_ingredientes.columns]]
    for row in df_ingredientes.values: data_ing.append([Paragraph(sanitizar_texto_pdf(val), cell_style) for val in row])
    t_ing = Table(data_ing)
    t_ing.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2d6a4f')), ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke), ('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('BOTTOMPADDING', (0, 0), (-1, 0), 5), ('GRID', (0, 0), (-1, -1), 0.5, colors.grey)]))
    story.append(t_ing)

    if detalhes_insumos:
        story.append(Spacer(1, 5))
        story.append(Paragraph("Insumos de Produção Selecionados", subtitle_style))
        data_ins = [[Paragraph("<b>Insumo</b>", cell_style), Paragraph("<b>Qtd. Considerada</b>", cell_style), Paragraph("<b>Preço Unit. (US$)</b>", cell_style), Paragraph("<b>Custo Lote (US$)</b>", cell_style), Paragraph("<b>Impacto (US$/t)</b>", cell_style)]]
        for item in detalhes_insumos:
            data_ins.append([Paragraph(sanitizar_texto_pdf(item['Insumo']), cell_style), Paragraph(item['Quantidade_aplicada'], cell_style), Paragraph(f"{item['Preco_base_usd']:.2f}", cell_style), Paragraph(f"{item['Custo_lote_usd']:.2f}", cell_style), Paragraph(f"{item['Custo_ton_usd']:.2f}", cell_style)])
        t_ins = Table(data_ins, colWidths=[200, 110, 110, 110, 110])
        t_ins.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#40916c')), ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke), ('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('BOTTOMPADDING', (0, 0), (-1, 0), 5), ('GRID', (0, 0), (-1, -1), 0.5, colors.grey)]))
        story.append(t_ins)

    story.append(Spacer(1, 5))
    data_econ = [[Paragraph(f"<b>{sanitizar_texto_pdf(c)}</b>", cell_style) for c in df_resumo_econ.columns]]
    for row in df_resumo_econ.values: data_econ.append([Paragraph(sanitizar_texto_pdf(val), cell_style) for val in row])
    t_econ = Table(data_econ)
    t_econ.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#40916c')), ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke), ('GRID', (0, 0), (-1, -1), 0.5, colors.grey)]))

    data_nut = [[Paragraph(f"<b>{sanitizar_texto_pdf(c)}</b>", cell_style) for c in df_nutrientes.columns]]
    for row in df_nutrientes.values: data_nut.append([Paragraph(sanitizar_texto_pdf(val), cell_style) for val in row])
    t_nut = Table(data_nut)
    t_nut.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#40916c')), ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke), ('GRID', (0, 0), (-1, -1), 0.5, colors.grey)]))

    story.append(KeepTogether([Paragraph("Resumo Econômico & Composição Nutricional Final", subtitle_style), Table([[t_econ, t_nut]])]))
    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer.getvalue()


# --------------------------------------------------------
# Interface Principal e Barra Lateral
# --------------------------------------------------------

if "calc_results" not in st.session_state:
    st.session_state.calc_results = {"sistema": None, "usuario": None}
if "removidos" not in st.session_state:
    st.session_state.removidos = {"sistema": set(), "usuario": set()}

if LOGO_BYTES:
    col_logo, col_tit = st.columns([1.5, 6])
    with col_logo: st.image(LOGO_BYTES, use_container_width=True)
    with col_tit:
        st.title("Otimizador de Misturas NPK + C")
        st.markdown("**INNOVATERRA AGRISOLUTIONS**")
    st.sidebar.image(LOGO_BYTES, use_container_width=True)
else:
    st.title("Otimizador de Misturas NPK + C")
    st.markdown("**INNOVATERRA AGRISOLUTIONS**")

st.sidebar.header("CONFIGURAÇÃO")

# IMPLEMENTAÇÃO ITENS 10 E 11: Menu de Modalidade e Estado Físico
modo_formulacao = st.sidebar.radio(
    "1. Método de Formulação:",
    [
        "1. Fórmula Direta (%)",
        "2. Recomendação (kg/ha)",
        "3. Análise de Solo + Cultura"
    ],
    index=0
)

estado_fisico = st.sidebar.radio(
    "2. Estado Físico:",
    ["Granulado (Sólido)", "Líquido (Fluido)"],
    index=0
)

# Definição dinâmica das abas com base no estado físico
if estado_fisico == "Granulado (Sólido)":
    nome_aba_mat = "Mat_Primas_Solidos"
    nome_aba_compat = "Compat_Solidos"
else:
    nome_aba_mat = "Mat_Primas_Liquidos"
    nome_aba_compat = "Compat_Liquidos"

if st.sidebar.button("🔄 Recarregar Dados das Planilhas"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.subheader("Cotação do Dólar")
cotacao_api = obter_cotacao_usd_brl_api()
if cotacao_api is not None:
    st.sidebar.metric("Automática (USD/BRL)", f"R$ {cotacao_api:.4f}")
    cotacao_efetiva = cotacao_api
else:
    st.sidebar.warning("Cotação automática indisponível.")
    cotacao_efetiva = st.sidebar.number_input("Cotação manual (R$/US$)", min_value=0.0, value=5.0, step=0.01)

# Carregamento direcionado para as abas corretas (Sólido ou Líquido)
try:
    df_insumos = carregar_insumos()
    df_mat = carregar_mat_primas(nome_aba_mat)
    df_compat = carregar_compatibilidade(nome_aba_compat)
except Exception as e:
    st.error(f"Falha ao carregar as abas '{nome_aba_mat}' ou '{nome_aba_compat}' do Google Sheets. Verifique os nomes. Erro: {e}")
    st.stop()


# --------------------------------------------------------
# Roteamento das Modalidades (Modo 1, Modo 2, Modo 3)
# --------------------------------------------------------

if modo_formulacao == "1. Fórmula Direta (%)":
    
    st.subheader("Metas da Mistura Final (%)")
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1: meta_c = st.number_input("Carbono alvo (%)", min_value=0.0, value=8.0, step=0.1)
    with col2: meta_n = st.number_input("N alvo (%)", min_value=0.0, value=10.0, step=0.1)
    with col3: meta_p = st.number_input("P₂O₅ alvo (%)", min_value=0.0, value=10.0, step=0.1)
    with col4: meta_k = st.number_input("K₂O alvo (%)", min_value=0.0, value=10.0, step=0.1)
    with col5: tolerancia = st.number_input("Tolerância relativa (%)", min_value=0.0, value=5.0, step=0.1)

    st.markdown("**Outros Macro e Micronutrientes (Metas opcionais, % m/m)**")
    meta_adicionais = {}
    cols_meta_extra = st.columns(4)
    for i, col in enumerate(NUTRIENTES_ADICIONAIS):
        with cols_meta_extra[i % 4]:
            meta_adicionais[col] = st.number_input(ROTULOS[col], min_value=0.0, value=0.0, step=0.01 if col in ["Ni_pct", "Mo_pct"] else 0.1, format="%.3f" if col in ["Ni_pct", "Mo_pct"] else "%.2f")

    massa_minima = st.number_input("Massa final mínima aproximada (kg)", min_value=1.0, value=1000.0, step=100.0)

    tab_sistema, tab_usuario = st.tabs(["Sugestão do Sistema", "Sugestão do Usuário"])

    with tab_sistema:
        col_bio1, col_bio2 = st.columns([1, 2])
        with col_bio1:
            permitir_bioclastico_sistema = st.radio("Permitir Bioclástico?", ["Não", "Sim"], key="bio_sistema", horizontal=True)
        with col_bio2:
            pct_bioclastico_s = st.number_input("Percentual (%)", min_value=0.1, max_value=100.0, value=5.0, step=0.5, key="pct_bio_s") if permitir_bioclastico_sistema == "Sim" else 0.0

        ativos_sistema = df_mat[df_mat["Tipo_funcao"] != "INERTE"].copy()
        if permitir_bioclastico_sistema == "Não": ativos_sistema = ativos_sistema[~ativos_sistema["Ingrediente"].apply(eh_bioclastico)].copy()
        if st.session_state.removidos["sistema"]: ativos_sistema = ativos_sistema[~ativos_sistema["Ingrediente"].isin(st.session_state.removidos["sistema"])].copy()

        st.markdown("### Ingredientes Disponíveis (Sistema)")
        st.dataframe(ativos_sistema[["Ingrediente", "Preco_ton", "Umidade_pct", "MO_ms_pct", "C_pct", "N_pct", "P2O5_pct", "K2O_pct"]], use_container_width=True, hide_index=True)

        selecionados_s = ativos_sistema["Ingrediente"].tolist()
        incompat_s, limitados_s = classificar_compatibilidade(selecionados_s, df_compat)

        if incompat_s:
            st.warning("1º Passo: Combinações INCOMPATÍVEIS detectadas. Escolha a matéria-prima a remover em cada par:")
            rem_temp = set()
            for idx, (a, b) in enumerate(incompat_s):
                esc = st.radio(f"Par: {a} × {b}", [f"Remover {a}", f"Remover {b}"], key=f"sist_inc_{idx}")
                rem_temp.add(a if esc == f"Remover {a}" else b)
            if st.button("Aplicar Remoções de Incompatibilidade (Sistema)"):
                st.session_state.removidos["sistema"].update(rem_temp)
                st.rerun()

        if st.button("Calcular Mistura (Sistema)", type="primary", disabled=bool(incompat_s)):
            metas_principais = {"C_pct": meta_c, "N_pct": meta_n, "P2O5_pct": meta_p, "K2O_pct": meta_k}
            sol_ativos, status_msg, info = resolver_base_ativa(ativos_sistema, metas_principais, meta_adicionais, tolerancia, massa_minima, (permitir_bioclastico_sistema == "Sim"), pct_bioclastico_s)

            if sol_ativos is None:
                st.warning(status_msg)
                st.session_state.calc_results["sistema"] = None
            else:
                inerte_escolhido, _, _, lim_inertes = escolher_inerte(sol_ativos, df_mat, df_compat, massa_minima)
                if inerte_escolhido is None and lim_inertes: inerte_escolhido = lim_inertes[0][0]
                frames = [sol_ativos.copy()]
                if inerte_escolhido is not None: frames.append(pd.DataFrame([inerte_escolhido]))
                st.session_state.calc_results["sistema"] = pd.concat(frames, ignore_index=True)

        res_s = st.session_state.calc_results["sistema"]
        if res_s is not None:
            massa_tot = res_s["Quantidade_kg"].sum()
            massa_ton = massa_tot / 1000.0
            res_s["Participacao_pct"] = round(100 * res_s["Quantidade_kg"] / massa_tot, 2)
            res_s["Custo_total"] = round(res_s["Quantidade_kg"] * res_s["Preco_ton"] / 1000.0, 2)

            st.success(f"Solução ótima calculada! Massa total: **{massa_tot:.2f} kg**")
            mostrar = res_s[["Ingrediente", "Quantidade_kg", "Participacao_pct", "Preco_ton", "Custo_total"]].sort_values("Quantidade_kg", ascending=False)
            st.dataframe(mostrar, use_container_width=True, hide_index=True)

            st.subheader("Insumos de Produção")
            insumos_sel = []
            for idx, row in df_insumos.iterrows():
                rotulo = f"Usar {row['Insumo']} (US$ {row['Preco_usd_ton']:.2f}/unidade)" if eh_insumo_discreto(row['Insumo']) else f"Usar {row['Insumo']} (US$ {row['Preco_usd_ton']:.2f}/t proporcional)"
                if st.checkbox(rotulo, key=f"ins_s_{idx}"): insumos_sel.append(row)

            custo_mat_usd_t = res_s["Custo_total"].sum() / massa_ton
            custo_ins_usd_t, custo_ins_usd_lote, detalhes_insumos_s = calcular_custos_insumos(insumos_sel, massa_ton)
            custo_tot_usd_t = custo_mat_usd_t + custo_ins_usd_t
            custo_tot_usd_lote = res_s["Custo_total"].sum() + custo_ins_usd_lote

            resumo_econ = pd.DataFrame([
                {"Indicador": "Massa final (kg)", "Valor": f"{massa_tot:.2f}"},
                {"Indicador": "Custo matérias-primas (US$/t)", "Valor": f"{custo_mat_usd_t:.2f}"},
                {"Indicador": "Custo total (US$/t)", "Valor": f"{custo_tot_usd_t:.2f}"},
                {"Indicador": "Custo total (R$/t)", "Valor": f"{(custo_tot_usd_t * cotacao_efetiva):.2f}"},
                {"Indicador": "Custo total do lote (R$)", "Valor": f"{(custo_tot_usd_lote * cotacao_efetiva):.2f}"},
            ])
            st.table(resumo_econ)

            resumo_nut = resumo_nutrientes_completo(res_s)
            st.subheader("Composição Nutricional Final")
            st.dataframe(resumo_nut, use_container_width=True, hide_index=True)

            col_dl1, col_dl2 = st.columns(2)
            with col_dl1: st.download_button("Baixar CSV", data=mostrar.to_csv(index=False).encode("utf-8"), file_name="resultado.csv", mime="text/csv")
            with col_dl2: st.download_button("Baixar PDF", data=gerar_pdf_a4_paisagem(mostrar, resumo_econ, resumo_nut, detalhes_insumos_s, f"Fórmula Direta - {estado_fisico}"), file_name="resultado.pdf", mime="application/pdf")

    with tab_usuario:
        st.info("A funcionalidade Restrição de Estoque opera na mesma lógica. Siga o fluxo na aba 'Sugestão do Sistema'.")

elif modo_formulacao == "2. Recomendação (kg/ha)":
    st.title("Módulo: Recomendação Direta (kg/ha)")
    st.info("🚧 Módulo em construção (Referente ao Item 15). Aqui o sistema fará a conversão de Dose por Hectare para Metas Percentuais (%).")

elif modo_formulacao == "3. Análise de Solo + Cultura":
    st.title("Módulo: Interpretação Agronômica")
    st.info("🚧 Módulo em construção (Referente aos Itens 13 e 14). Em breve será integrada a identificação do talhão, textura do solo e cálculo direto das planilhas de exigência.")

st.markdown("---")
st.markdown("**INNOVATERRA AGRISOLUTIONS**")
