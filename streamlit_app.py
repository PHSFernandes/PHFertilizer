import io
import requests
from itertools import combinations
from urllib.parse import quote

import pandas as pd
import streamlit as st
from pulp import (
    LpMinimize,
    LpProblem,
    LpStatus,
    LpVariable,
    PULP_CBC_CMD,
    lpSum,
    value,
)

# Bibliotecas para exportação PDF (A4 Paisagem)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# --------------------------------------------------------
# Configuração básica da página
# --------------------------------------------------------

st.set_page_config(page_title="Otimizador de Misturas NPK+C", layout="wide")

# IDs das planilhas Google Sheets
INSUMOS_SHEET_ID = "1rUIG49XF9eszt-4c_iS45bNLMEFlgpokVDG-qwFOF1k"
MAT_PRIMAS_SHEET_ID = "112DvPDCPnzkfIlexxtwQIbOUf5Pm6WlznEUhz7K_9_E"
COMPAT_SHEET_ID = "1D1VxuaaviKd73ccyn8ltWEnvlss2e0mK_K5j7zBo8zQ"

# Nomes das abas
INSUMOS_SHEET_NAME = "Insumos"
MAT_PRIMAS_SHEET_NAME = "Mat_Primas"
COMPAT_SHEET_NAME = "compatibilidade"

# Nutrientes principais e adicionais (todos em % m/m)
NUTRIENTES_PRINCIPAIS = ["C_pct", "N_pct", "P2O5_pct", "K2O_pct"]
NUTRIENTES_ADICIONAIS = [
    "CaO_pct",
    "MgO_pct",
    "S_pct",
    "B_pct",
    "Cl_pct",
    "Cu_pct",
    "Fe_pct",
    "Mn_pct",
    "Mo_pct",
    "Ni_pct",
    "Zn_pct",
]
NUTRIENTES_TODOS = NUTRIENTES_PRINCIPAIS + NUTRIENTES_ADICIONAIS

ROTULOS = {
    "C_pct": "Carbono (C) %",
    "N_pct": "Nitrogênio (N) %",
    "P2O5_pct": "P₂O₅ %",
    "K2O_pct": "K₂O %",
    "CaO_pct": "CaO %",
    "MgO_pct": "MgO %",
    "S_pct": "Enxofre (S) %",
    "B_pct": "Boro (B) %",
    "Cl_pct": "Cloro (Cl) %",
    "Cu_pct": "Cobre (Cu) %",
    "Fe_pct": "Ferro (Fe) %",
    "Mn_pct": "Manganês (Mn) %",
    "Mo_pct": "Molibdênio (Mo) %",
    "Ni_pct": "Níquel (Ni) %",
    "Zn_pct": "Zinco (Zn) %",
}


def gs_csv_url(sheet_id: str, sheet_name: str) -> str:
    return (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        f"/gviz/tq?tqx=out:csv&sheet={quote(sheet_name)}"
    )


# --------------------------------------------------------
# Carregamento dos dados via Google Sheets
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
        if "insumo" in c_lower:
            colmap[c] = "Insumo"
        elif "unidade" in c_lower:
            colmap[c] = "Unidade_por_ton"
        elif "quantidade" in c_lower:
            colmap[c] = "Quantidade_por_ton"
        elif "preço" in c_lower or "preco" in c_lower:
            colmap[c] = "Preco_usd_ton"
    df = df.rename(columns=colmap)

    for col in ["Insumo", "Preco_usd_ton"]:
        if col not in df.columns:
            df[col] = "" if col == "Insumo" else 0.0

    if "Quantidade_por_ton" in df.columns:
        df["Quantidade_por_ton"] = (
            df["Quantidade_por_ton"]
            .astype(str)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
        )
        df["Quantidade_por_ton"] = pd.to_numeric(
            df["Quantidade_por_ton"], errors="coerce"
        ).fillna(0.0)

    df["Preco_usd_ton"] = (
        df["Preco_usd_ton"]
        .astype(str)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    df["Preco_usd_ton"] = pd.to_numeric(df["Preco_usd_ton"], errors="coerce").fillna(0.0)

    df["Insumo"] = df["Insumo"].astype(str).str.strip()
    df = df[df["Insumo"] != ""].copy()
    return df


@st.cache_data(ttl=300)
def carregar_mat_primas() -> pd.DataFrame:
    url = gs_csv_url(MAT_PRIMAS_SHEET_ID, MAT_PRIMAS_SHEET_NAME)
    df = pd.read_csv(url)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")

    expected_cols = [
        "Ingrediente",
        "Preco_ton",
        "Umidade_pct",
        "MO_ms_pct",
        "C_pct",
        "N_pct",
        "P2O5_pct",
        "K2O_pct",
        "CaO_pct",
        "MgO_pct",
        "S_pct",
        "B_pct",
        "Cl_pct",
        "Cu_pct",
        "Fe_pct",
        "Mn_pct",
        "Mo_pct",
        "Ni_pct",
        "Zn_pct",
        "Tipo_funcao",
    ]
    for col in expected_cols:
        if col not in df.columns:
            df[col] = "" if col in ["Ingrediente", "Tipo_funcao"] else 0.0

    num_cols = [c for c in expected_cols if c not in ["Ingrediente", "Tipo_funcao"]]
    for col in num_cols:
        df[col] = (
            df[col]
            .astype(str)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
        )
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df["Ingrediente"] = df["Ingrediente"].astype(str).str.strip()
    df["Tipo_funcao"] = df["Tipo_funcao"].astype(str).str.strip().str.upper()
    df = df[df["Ingrediente"] != ""].copy()

    # Cálculo do Carbono derivado da Matéria Orgânica caso C_pct esteja zerado
    mask_c_zero = df["C_pct"].fillna(0).eq(0) & df["MO_ms_pct"].gt(0)
    df.loc[mask_c_zero, "C_pct"] = 0.58 * df.loc[mask_c_zero, "MO_ms_pct"] * (
        1 - df.loc[mask_c_zero, "Umidade_pct"] / 100.0
    )

    return df


@st.cache_data(ttl=300)
def carregar_compatibilidade() -> pd.DataFrame:
    url = gs_csv_url(COMPAT_SHEET_ID, COMPAT_SHEET_NAME)
    df = pd.read_csv(url, header=None)
    df = df.dropna(how="all")

    header_raw = df.iloc[0].tolist()
    nomes = [
        str(x).strip()
        for x in header_raw
        if pd.notna(x) and str(x).strip() != ""
    ]

    matriz = df.iloc[1 : 1 + len(nomes), : len(nomes) + 1].copy()
    matriz.columns = ["Ingrediente"] + nomes
    matriz["Ingrediente"] = matriz["Ingrediente"].astype(str).str.strip()

    for col in nomes:
        matriz[col] = matriz[col].astype(str).str.strip().str.upper()

    return matriz.set_index("Ingrediente")


# --------------------------------------------------------
# Lógica de Compatibilidade e Câmbio
# --------------------------------------------------------


def obter_status(matriz: pd.DataFrame, a: str, b: str) -> str:
    if a == b:
        return "COMPATÍVEL"
    try:
        if a in matriz.index and b in matriz.columns:
            val = str(matriz.loc[a, b]).strip().upper()
            if val and val not in ["NAN", "-"]:
                return val
        if b in matriz.index and a in matriz.columns:
            val = str(matriz.loc[b, a]).strip().upper()
            if val and val not in ["NAN", "-"]:
                return val
    except Exception:
        pass
    return "COMPATÍVEL"


def classificar_compatibilidade(selecionados: list[str], matriz: pd.DataFrame):
    incompat = []
    limitados = []
    for a, b in combinations(selecionados, 2):
        status = obter_status(matriz, a, b)
        if status == "INCOMPATÍVEL":
            incompat.append((a, b))
        elif status == "LIMITADO":
            limitados.append((a, b))
    return incompat, limitados


def obter_cotacao_usd_brl_api() -> float | None:
    try:
        resp = requests.get("https://economia.awesomeapi.com.br/last/USD-BRL", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return float(data["USDBRL"]["bid"])
    except Exception:
        pass
    return None


# --------------------------------------------------------
# Otimizador (PuLP) com Variáveis de Folga (Diagnóstico Slacks)
# --------------------------------------------------------


def resolver_base_ativa(
    ativos: pd.DataFrame,
    metas_principais: dict,
    metas_extra: dict,
    tol: float,
    massa_final: float,
    usar_bioclastico: bool,
    pct_bioclastico: float
):
    mask_contribuintes = (
        (ativos["N_pct"] > 0) |
        (ativos["P2O5_pct"] > 0) |
        (ativos["K2O_pct"] > 0) |
        (ativos["C_pct"] > 0) |
        (ativos["Ingrediente"] == "Bioclástico")
    )
    ativos = ativos[mask_contribuintes].copy()

    if ativos.empty:
        return None, "Nenhuma matéria-prima disponível no momento possui teor para suprir N, P₂O₅, K₂O ou Carbono.", None

    metas_todas = metas_principais.copy()
    metas_todas.update(metas_extra)

    prob = LpProblem("Mistura_NPK_C_Ativos", LpMinimize)
    
    x = {}
    for i, row in ativos.iterrows():
        if usar_bioclastico and row["Ingrediente"] == "Bioclástico":
            massa_bio = (pct_bioclastico / 100.0) * massa_final
            x[i] = LpVariable(f"x_{i}", lowBound=massa_bio, upBound=massa_bio)
        else:
            x[i] = LpVariable(f"x_{i}", lowBound=0.0)

    slack_nutrientes = {
        col: LpVariable(f"slack_{col}", lowBound=0.0)
        for col, alvo in metas_todas.items() if alvo > 0
    }

    PENALIDADE_SLACK = 1e6
    prob += (
        lpSum(x[i] * float(ativos.loc[i, "Preco_ton"]) / 1000.0 for i in ativos.index) +
        lpSum(slack_nutrientes[col] * PENALIDADE_SLACK for col in slack_nutrientes)
    )

    total_ativos = lpSum(x[i] for i in ativos.index)
    prob += total_ativos <= massa_final

    for col, alvo in metas_todas.items():
        if alvo <= 0:
            continue
        
        fator_tol = tol / 100.0
        minimo = max(0.0, alvo * (1 - fator_tol))
        contrib = lpSum(x[i] * float(ativos.loc[i, col]) / 100.0 for i in ativos.index)
        
        prob += contrib + (slack_nutrientes[col] / 100.0) * massa_final >= (minimo / 100.0) * massa_final
        
        if tol > 0:
            maximo = alvo * (1 + fator_tol)
            prob += contrib <= (maximo / 100.0) * massa_final

    prob.solve(PULP_CBC_CMD(msg=False))
    status = LpStatus[prob.status]

    deficits = {}
    for col, var in slack_nutrientes.items():
        v = value(var)
        if v is not None and v > 1e-4:
            deficits[ROTULOS.get(col, col)] = round(v, 2)

    if deficits:
        msg_diag = (
            "⚠️ **Diagnóstico Técnico de Viabilidade**: As matérias-primas selecionadas são pouco concentradas "
            "ou a fração de Carbono/Bioclástico ocupa muito espaço físico. Não foi possível atingir o teor desejado em 1000 kg.\n\n"
            "**Déficit identificado em relação ao piso mínimo exigido:**\n"
        )
        for nut, d_val in deficits.items():
            msg_diag += f"- **{nut}**: Falta incorporar mais **{d_val}%** na mistura final.\n"
        
        msg_diag += "\n*Sugestões do Sistema:* Aumente a Tolerância Relativa (%), reduza o % de Bioclástico ou selecione matérias-primas mais concentradas."
        return None, msg_diag, None

    if status != "Optimal":
        return None, f"Modelo de otimização sem solução. Status do solver: {status}", None

    sol = ativos.copy()
    sol["Quantidade_kg"] = [value(x[i]) for i in ativos.index]
    sol = sol[sol["Quantidade_kg"] > 1e-6].copy()
    total_kg = sol["Quantidade_kg"].sum()
    sol["Participacao_pct"] = 100 * sol["Quantidade_kg"] / total_kg
    sol["Custo_total"] = sol["Quantidade_kg"] * sol["Preco_ton"] / 1000.0

    info = {
        "massa_ativos": total_kg,
        "custo_ativos": sol["Custo_total"].sum(),
    }
    return sol, status, info


def escolher_inerte(sol_ativos: pd.DataFrame, base: pd.DataFrame, matriz: pd.DataFrame, massa_final: float):
    massa_ativos = sol_ativos["Quantidade_kg"].sum()
    faltante = massa_final - massa_ativos

    if faltante < -1e-6:
        return None, "Os ingredientes ativos excederam a massa final desejada.", [], []

    if faltante <= 1e-6:
        return None, None, [], []

    inertes = base[base["Tipo_funcao"] == "INERTE"].copy()
    presentes = sol_ativos["Ingrediente"].tolist()

    compativeis = []
    limitados = []
    motivos_incompat = []

    for _, row in inertes.iterrows():
        nome = row["Ingrediente"]
        status_list = [obter_status(matriz, nome, outro) for outro in presentes]

        pares_incompat = [
            outro for outro, s in zip(presentes, status_list) if s == "INCOMPATÍVEL"
        ]
        pares_limitado = [
            outro for outro, s in zip(presentes, status_list) if s == "LIMITADO"
        ]

        if pares_incompat:
            motivos_incompat.append(
                f"{nome}: incompatível com " + ", ".join(pares_incompat)
            )
            continue

        registro = row.copy()
        registro["Quantidade_kg"] = faltante

        if pares_limitado:
            limitados.append((registro, pares_limitado))
        else:
            compativeis.append(registro)

    compativeis_sorted = sorted(compativeis, key=lambda r: float(r["Preco_ton"]))
    limitados_sorted = sorted(limitados, key=lambda t: float(t[0]["Preco_ton"]))

    if compativeis_sorted:
        return compativeis_sorted[0], None, compativeis_sorted, limitados_sorted

    if limitados_sorted:
        nomes = ", ".join([str(t[0]["Ingrediente"]) for t in limitados_sorted])
        msg = (
            "Nenhum inerte totalmente compatível. Somente inertes em condição LIMITADO: "
            + nomes
        )
        return None, msg, compativeis_sorted, limitados_sorted

    if motivos_incompat:
        msg = "Nenhum material inerte elegível. Motivos: " + "; ".join(motivos_incompat)
    else:
        msg = "Nenhum material inerte disponível."
    return None, msg, compativeis_sorted, limitados_sorted


def resumo_nutrientes_completo(df_resultado: pd.DataFrame) -> pd.DataFrame:
    total_kg = df_resultado["Quantidade_kg"].sum()
    linhas = []
    for col in NUTRIENTES_TODOS:
        if col in df_resultado.columns:
            teor_final = (
                df_resultado["Quantidade_kg"] * df_resultado[col] / 100.0
            ).sum() / total_kg * 100
            linhas.append(
                {
                    "Nutriente": ROTULOS.get(col, col),
                    "Teor final (%)": round(teor_final, 2),
                }
            )
    return pd.DataFrame(linhas)


# --------------------------------------------------------
# Exportação PDF (ReportLab - A4 Paisagem sem erro de caracteres Unicode)
# --------------------------------------------------------


def gerar_pdf_a4_paisagem(df_ingredientes: pd.DataFrame, df_resumo_econ: pd.DataFrame, df_nutrientes: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=30,
        leftMargin=30,
        topMargin=30,
        bottomMargin=30,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#1b4332'),
        alignment=1,
        spaceAfter=10
    )
    subtitle_style = ParagraphStyle(
        'DocSubTitle',
        parent=styles['Heading2'],
        fontSize=12,
        textColor=colors.HexColor('#2d6a4f'),
        spaceBefore=8,
        spaceAfter=4
    )
    cell_style = ParagraphStyle(
        'CellText',
        parent=styles['Normal'],
        fontSize=8,
        alignment=1
    )

    def sanitizar_texto_pdf(txt: str) -> str:
        """Substitui subscritos Unicode por marcadores HTML compativeis com ReportLab"""
        return str(txt).replace("₂", "<sub>2</sub>").replace("₅", "<sub>5</sub>")

    story = []
    story.append(Paragraph("INNOVATERRA AGRISOLUTIONS", title_style))
    story.append(Paragraph("Relatório de Otimização e Formulação de Fertilizante", styles['Heading3']))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Composição das Matérias-Primas", subtitle_style))
    
    # Formatação da Tabela de Ingredientes
    headers_ing = [Paragraph(f"<b>{sanitizar_texto_pdf(c)}</b>", cell_style) for c in df_ingredientes.columns]
    data_ing = [headers_ing]
    for row in df_ingredientes.values:
        data_ing.append([Paragraph(sanitizar_texto_pdf(val), cell_style) for val in row])

    t_ing = Table(data_ing)
    t_ing.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2d6a4f')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 5),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(t_ing)
    story.append(Spacer(1, 12))

    story.append(Paragraph("Resumo Econômico & Composição Nutricional Final", subtitle_style))
    
    # Formatação da Tabela de Resumo Econômico
    headers_econ = [Paragraph(f"<b>{sanitizar_texto_pdf(c)}</b>", cell_style) for c in df_resumo_econ.columns]
    data_econ = [headers_econ]
    for row in df_resumo_econ.values:
        data_econ.append([Paragraph(sanitizar_texto_pdf(val), cell_style) for val in row])

    t_econ = Table(data_econ)
    t_econ.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#40916c')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))

    # Formatação da Tabela Nutricional
    headers_nut = [Paragraph(f"<b>{sanitizar_texto_pdf(c)}</b>", cell_style) for c in df_nutrientes.columns]
    data_nut = [headers_nut]
    for row in df_nutrientes.values:
        data_nut.append([Paragraph(sanitizar_texto_pdf(val), cell_style) for val in row])

    t_nut = Table(data_nut)
    t_nut.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#40916c')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))

    t_master = Table([[t_econ, t_nut]])
    story.append(t_master)

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


# --------------------------------------------------------
# Inicialização do Estado de Sessão
# --------------------------------------------------------

if "calc_results" not in st.session_state:
    st.session_state.calc_results = {"sistema": None, "usuario": None}

if "removidos" not in st.session_state:
    st.session_state.removidos = {"sistema": set(), "usuario": set()}


# --------------------------------------------------------
# Interface Principal e Barra Lateral
# --------------------------------------------------------

st.title("Otimizador de Misturas NPK + Carbono")
st.markdown("**INNOVATERRA AGRISOLUTIONS**")

# Barra Lateral (Sidebar)
st.sidebar.header("INNOVATERRA AGRISOLUTIONS")

if st.sidebar.button("🔄 Recarregar Dados das Planilhas"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.subheader("Cotação do Dólar (USD/BRL)")

cotacao_api = obter_cotacao_usd_brl_api()
if cotacao_api is not None:
    st.sidebar.metric("Cotação automática", f"R$ {cotacao_api:.4f}")
    cotacao_efetiva = cotacao_api
else:
    st.sidebar.warning("Cotação automática indisponível. Informe manualmente.")
    cotacao_manual = st.sidebar.number_input(
        "Cotação manual (R$/US$)",
        min_value=0.0,
        value=5.0,
        step=0.01,
    )
    cotacao_efetiva = cotacao_manual

try:
    df_insumos = carregar_insumos()
    df_mat = carregar_mat_primas()
    df_compat = carregar_compatibilidade()
except Exception as e:
    st.error(f"Falha ao carregar as planilhas do Google Sheets: {e}")
    st.stop()

st.subheader("Metas da Mistura Final")

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    meta_c = st.number_input("Carbono alvo (%)", min_value=0.0, value=8.0, step=0.1)
with col2:
    meta_n = st.number_input("N alvo (%)", min_value=0.0, value=10.0, step=0.1)
with col3:
    meta_p = st.number_input("P₂O₅ alvo (%)", min_value=0.0, value=10.0, step=0.1)
with col4:
    meta_k = st.number_input("K₂O alvo (%)", min_value=0.0, value=10.0, step=0.1)
with col5:
    tolerancia = st.number_input("Tolerância relativa (%)", min_value=0.0, value=5.0, step=0.1)

st.markdown("**Outros Macro e Micronutrientes (Metas opcionais, % m/m)**")

meta_adicionais = {}
cols_meta_extra = st.columns(4)
for i, col in enumerate(NUTRIENTES_ADICIONAIS):
    with cols_meta_extra[i % 4]:
        meta_adicionais[col] = st.number_input(
            ROTULOS[col],
            min_value=0.0,
            value=0.0,
            step=0.1,
        )

massa_final = st.number_input(
    "Massa final desejada (kg)", min_value=1.0, value=1000.0, step=100.0
)

tab_sistema, tab_usuario = st.tabs(["Sugestão do Sistema", "Sugestão do Usuário"])

# --------------------------------------------------------
# Aba: Sugestão do Sistema
# --------------------------------------------------------

with tab_sistema:
    st.subheader("Sugestão do Sistema")

    col_bio1, col_bio2 = st.columns([1, 2])
    with col_bio1:
        permitir_bioclastico_sistema = st.radio(
            "Permitir Bioclástico?",
            ["Não", "Sim"],
            key="bio_sistema",
            horizontal=True,
        )
    with col_bio2:
        pct_bioclastico_s = 0.0
        if permitir_bioclastico_sistema == "Sim":
            pct_bioclastico_s = st.number_input(
                "Percentual de Bioclástico a adicionar (%)",
                min_value=0.1,
                max_value=100.0,
                value=5.0,
                step=0.5,
                key="pct_bio_s"
            )

    ativos_sistema = df_mat[df_mat["Tipo_funcao"] != "INERTE"].copy()
    if permitir_bioclastico_sistema == "Não":
        ativos_sistema = ativos_sistema[ativos_sistema["Ingrediente"] != "Bioclástico"].copy()

    if st.session_state.removidos["sistema"]:
        ativos_sistema = ativos_sistema[~ativos_sistema["Ingrediente"].isin(st.session_state.removidos["sistema"])].copy()

    st.markdown("### Ingredientes Disponíveis (Sistema)")
    st.dataframe(
        ativos_sistema[["Ingrediente", "Preco_ton", "Umidade_pct", "MO_ms_pct", "C_pct", "N_pct", "P2O5_pct", "K2O_pct"]],
        use_container_width=True,
        hide_index=True,
    )

    # 1º Filtro: Compatibilidade Química
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
        
        sol_ativos, status_msg, info = resolver_base_ativa(
            ativos_sistema, metas_principais, meta_adicionais, tolerancia, massa_final,
            (permitir_bioclastico_sistema == "Sim"), pct_bioclastico_s
        )

        if sol_ativos is None:
            st.warning(status_msg)
            st.session_state.calc_results["sistema"] = None
        else:
            inerte_escolhido, alerta_inerte, _, lim_inertes = escolher_inerte(sol_ativos, df_mat, df_compat, massa_final)
            if inerte_escolhido is None and lim_inertes:
                inerte_escolhido = lim_inertes[0][0]

            frames = [sol_ativos.copy()]
            if inerte_escolhido is not None:
                frames.append(pd.DataFrame([inerte_escolhido]))
            res = pd.concat(frames, ignore_index=True)
            st.session_state.calc_results["sistema"] = res

    # Exibição do Resultado Persistido
    res_s = st.session_state.calc_results["sistema"]
    if res_s is not None:
        st.success("Solução ótima calculada com sucesso!")
        massa_tot = res_s["Quantidade_kg"].sum()
        massa_ton = massa_tot / 1000.0
        res_s["Participacao_pct"] = round(100 * res_s["Quantidade_kg"] / massa_tot, 2)
        res_s["Custo_total"] = round(res_s["Quantidade_kg"] * res_s["Preco_ton"] / 1000.0, 2)

        st.subheader("Ingredientes Selecionados")
        mostrar = res_s[["Ingrediente", "Quantidade_kg", "Participacao_pct", "Preco_ton", "Custo_total"]].sort_values("Quantidade_kg", ascending=False)
        st.dataframe(mostrar, use_container_width=True, hide_index=True)

        st.subheader("Insumos de Produção")
        insumos_sel = []
        for idx, row in df_insumos.iterrows():
            if st.checkbox(f"Usar {row['Insumo']} (US$ {row['Preco_usd_ton']:.2f}/t)", key=f"ins_s_{idx}"):
                insumos_sel.append(row)

        custo_mat_usd_t = res_s["Custo_total"].sum() / massa_ton
        custo_ins_usd_t = sum(r["Preco_usd_ton"] for r in insumos_sel) if insumos_sel else 0.0
        custo_tot_usd_t = custo_mat_usd_t + custo_ins_usd_t

        resumo_econ = pd.DataFrame([
            {"Indicador": "Massa total (kg)", "Valor": f"{massa_tot:.2f}"},
            {"Indicador": "Custo matérias-primas (US$/t)", "Valor": f"{custo_mat_usd_t:.2f}"},
            {"Indicador": "Custo insumos (US$/t)", "Valor": f"{custo_ins_usd_t:.2f}"},
            {"Indicador": "Custo total (US$/t)", "Valor": f"{custo_tot_usd_t:.2f}"},
            {"Indicador": "Custo total (R$/t)", "Valor": f"{(custo_tot_usd_t * cotacao_efetiva):.2f}"},
            {"Indicador": "Custo total do lote (US$)", "Valor": f"{(custo_tot_usd_t * massa_ton):.2f}"},
            {"Indicador": "Custo total do lote (R$)", "Valor": f"{(custo_tot_usd_t * massa_ton * cotacao_efetiva):.2f}"},
        ])
        st.table(resumo_econ)

        resumo_nut = resumo_nutrientes_completo(res_s)
        st.subheader("Composição Nutricional Final")
        st.dataframe(resumo_nut, use_container_width=True, hide_index=True)

        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button("Baixar CSV (Sistema)", data=mostrar.to_csv(index=False).encode("utf-8"), file_name="resultado_sistema.csv", mime="text/csv")
        with col_dl2:
            pdf_bytes = gerar_pdf_a4_paisagem(mostrar, resumo_econ, resumo_nut)
            st.download_button("Baixar PDF A4 Paisagem (Sistema)", data=pdf_bytes, file_name="resultado_sistema.pdf", mime="application/pdf")

        st.markdown("---")
        st.markdown("**INNOVATERRA AGRISOLUTIONS**")

# --------------------------------------------------------
# Aba: Sugestão do Usuário
# --------------------------------------------------------

with tab_usuario:
    st.subheader("Sugestão do Usuário (Restrição de Estoque)")

    options_estoque = df_mat[df_mat["Tipo_funcao"] != "INERTE"]["Ingrediente"].unique().tolist()
    ingredientes_usuario = st.multiselect("Selecione as matérias-primas disponíveis no estoque:", options=options_estoque, default=options_estoque, key="ing_u")

    col_bio_u1, col_bio_u2 = st.columns([1, 2])
    with col_bio_u1:
        permitir_bioclastico_usuario = st.radio("Permitir Bioclástico?", ["Não", "Sim"], key="bio_u", horizontal=True)
    with col_bio_u2:
        pct_bioclastico_u = 0.0
        if permitir_bioclastico_usuario == "Sim":
            pct_bioclastico_u = st.number_input(
                "Percentual de Bioclástico a adicionar (%)",
                min_value=0.1,
                max_value=100.0,
                value=5.0,
                step=0.5,
                key="pct_bio_u"
            )

    ativos_usuario = df_mat[df_mat["Ingrediente"].isin(ingredientes_usuario)].copy()
    if permitir_bioclastico_usuario == "Não":
        ativos_usuario = ativos_usuario[ativos_usuario["Ingrediente"] != "Bioclástico"].copy()

    if st.session_state.removidos["usuario"]:
        ativos_usuario = ativos_usuario[~ativos_usuario["Ingrediente"].isin(st.session_state.removidos["usuario"])].copy()

    st.markdown("### Ingredientes Disponíveis (Usuário)")
    st.dataframe(ativos_usuario[["Ingrediente", "Preco_ton", "Umidade_pct", "MO_ms_pct", "C_pct", "N_pct", "P2O5_pct", "K2O_pct"]], use_container_width=True, hide_index=True)

    # 1º Filtro: Compatibilidade Química
    selecionados_u = ativos_usuario["Ingrediente"].tolist()
    incompat_u, limitados_u = classificar_compatibilidade(selecionados_u, df_compat)

    if incompat_u:
        st.warning("1º Passo: Combinações INCOMPATÍVEIS detectadas no estoque. Escolha a matéria-prima a remover em cada par:")
        rem_temp_u = set()
        for idx, (a, b) in enumerate(incompat_u):
            esc = st.radio(f"Par: {a} × {b}", [f"Remover {a}", f"Remover {b}"], key=f"usu_inc_{idx}")
            rem_temp_u.add(a if esc == f"Remover {a}" else b)
        if st.button("Aplicar Remoções (Usuário)"):
            st.session_state.removidos["usuario"].update(rem_temp_u)
            st.rerun()

    if st.button("Calcular Mistura (Usuário)", type="primary", disabled=bool(incompat_u)):
        metas_principais_u = {"C_pct": meta_c, "N_pct": meta_n, "P2O5_pct": meta_p, "K2O_pct": meta_k}
        
        sol_ativos_u, status_msg_u, info_u = resolver_base_ativa(
            ativos_usuario, metas_principais_u, meta_adicionais, tolerancia, massa_final,
            (permitir_bioclastico_usuario == "Sim"), pct_bioclastico_u
        )

        if sol_ativos_u is None:
            st.warning(status_msg_u)
            st.session_state.calc_results["usuario"] = None
        else:
            inerte_u, _, _, lim_inertes_u = escolher_inerte(sol_ativos_u, df_mat, df_compat, massa_final)
            if inerte_u is None and lim_inertes_u:
                inerte_u = lim_inertes_u[0][0]

            frames_u = [sol_ativos_u.copy()]
            if inerte_u is not None:
                frames_u.append(pd.DataFrame([inerte_u]))
            res_u = pd.concat(frames_u, ignore_index=True)
            st.session_state.calc_results["usuario"] = res_u

    res_u = st.session_state.calc_results["usuario"]
    if res_u is not None:
        st.success("Solução ótima calculada com sucesso!")
        massa_tot_u = res_u["Quantidade_kg"].sum()
        massa_ton_u = massa_tot_u / 1000.0
        res_u["Participacao_pct"] = round(100 * res_u["Quantidade_kg"] / massa_tot_u, 2)
        res_u["Custo_total"] = round(res_u["Quantidade_kg"] * res_u["Preco_ton"] / 1000.0, 2)

        st.subheader("Ingredientes Selecionados")
        mostrar_u = res_u[["Ingrediente", "Quantidade_kg", "Participacao_pct", "Preco_ton", "Custo_total"]].sort_values("Quantidade_kg", ascending=False)
        st.dataframe(mostrar_u, use_container_width=True, hide_index=True)

        st.subheader("Insumos de Produção")
        insumos_sel_u = []
        for idx, row in df_insumos.iterrows():
            if st.checkbox(f"Usar {row['Insumo']} (US$ {row['Preco_usd_ton']:.2f}/t)", key=f"ins_u_{idx}"):
                insumos_sel_u.append(row)

        custo_mat_u = res_u["Custo_total"].sum() / massa_ton_u
        custo_ins_u = sum(r["Preco_usd_ton"] for r in insumos_sel_u) if insumos_sel_u else 0.0
        custo_tot_u = custo_mat_u + custo_ins_u

        resumo_econ_u = pd.DataFrame([
            {"Indicador": "Massa total (kg)", "Valor": f"{massa_tot_u:.2f}"},
            {"Indicador": "Custo matérias-primas (US$/t)", "Valor": f"{custo_mat_u:.2f}"},
            {"Indicador": "Custo insumos (US$/t)", "Valor": f"{custo_ins_u:.2f}"},
            {"Indicador": "Custo total (US$/t)", "Valor": f"{custo_tot_u:.2f}"},
            {"Indicador": "Custo total (R$/t)", "Valor": f"{(custo_tot_u * cotacao_efetiva):.2f}"},
            {"Indicador": "Custo total do lote (US$)", "Valor": f"{(custo_tot_u * massa_ton_u):.2f}"},
            {"Indicador": "Custo total do lote (R$)", "Valor": f"{(custo_tot_u * massa_ton_u * cotacao_efetiva):.2f}"},
        ])
        st.table(resumo_econ_u)

        resumo_nut_u = resumo_nutrientes_completo(res_u)
        st.subheader("Composição Nutricional Final")
        st.dataframe(resumo_nut_u, use_container_width=True, hide_index=True)

        col_dl1_u, col_dl2_u = st.columns(2)
        with col_dl1_u:
            st.download_button("Baixar CSV (Usuário)", data=mostrar_u.to_csv(index=False).encode("utf-8"), file_name="resultado_usuario.csv", mime="text/csv")
        with col_dl2_u:
            pdf_bytes_u = gerar_pdf_a4_paisagem(mostrar_u, resumo_econ_u, resumo_nut_u)
            st.download_button("Baixar PDF A4 Paisagem (Usuário)", data=pdf_bytes_u, file_name="resultado_usuario.pdf", mime="application/pdf")

        st.markdown("---")
        st.markdown("**INNOVATERRA AGRISOLUTIONS**")
