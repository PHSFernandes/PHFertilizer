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

# --------------------------------------------------------
# Configuração básica
# --------------------------------------------------------

st.set_page_config(page_title="Otimizador de Misturas NPK+C", layout="wide")

# IDs das planilhas
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
# Carregamento dos dados
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
    df["Preco_usd_ton"] = pd.to_numeric(df["Preco_usd_ton"], errors="coerce").fillna(
        0.0
    )

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
        "Min_kg",
        "Max_kg",
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

    mask_c_zero = df["C_pct"].fillna(0).eq(0) & df["MO_ms_pct"].gt(0)
    df.loc[mask_c_zero, "C_pct"] = 0.58 * df.loc[mask_c_zero, "MO_ms_pct"] * (
        1 - df.loc[mask_c_zero, "Umidade_pct"] / 100.0
    )

    df["Max_kg"] = df["Max_kg"].replace(0, 1e9)
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
# Compatibilidade e câmbio
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
        resp = requests.get(
            "https://api.exchangerate.host/latest?base=USD&symbols=BRL", timeout=5
        )
        data = resp.json()
        return float(data["rates"]["BRL"])
    except Exception:
        return None


# --------------------------------------------------------
# Solver e composição
# --------------------------------------------------------


def resolver_base_ativa(
    ativos: pd.DataFrame,
    metas_principais: dict,
    metas_extra: dict,
    tol: float,
    massa_final: float,
):
    if ativos.empty:
        return None, "Nenhum ingrediente ativo elegível.", None

    metas_todas = metas_principais.copy()
    metas_todas.update(metas_extra)

    prob = LpProblem("Mistura_NPK_C_Ativos", LpMinimize)
    x = {
        i: LpVariable(
            f"x_{i}",
            lowBound=max(0.0, float(ativos.loc[i, "Min_kg"])),
            upBound=float(ativos.loc[i, "Max_kg"]),
        )
        for i in ativos.index
    }

    total_ativos = lpSum(x[i] for i in ativos.index)
    prob += lpSum(x[i] * float(ativos.loc[i, "Preco_ton"]) / 1000.0 for i in ativos.index)
    prob += total_ativos <= massa_final

    for col, alvo in metas_todas.items():
        if alvo <= 0:
            continue
        fator_tol = tol / 100.0
        minimo = max(0.0, alvo * (1 - fator_tol))
        maximo = alvo * (1 + fator_tol)
        contrib = lpSum(x[i] * float(ativos.loc[i, col]) / 100.0 for i in ativos.index)
        prob += contrib >= (minimo / 100.0) * massa_final
        prob += contrib <= (maximo / 100.0) * massa_final

    prob.solve(PULP_CBC_CMD(msg=False))
    status = LpStatus[prob.status]
    if status != "Optimal":
        return None, f"Modelo sem solução ótima. Status: {status}", None

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
                    "Teor final (%)": teor_final,
                }
            )
    return pd.DataFrame(linhas)


# --------------------------------------------------------
# Estado de sessão (incompatibilidades)
# --------------------------------------------------------

if "incomp_choices_sistema" not in st.session_state:
    st.session_state.incomp_choices_sistema = {}
if "incomp_resolvido_sistema" not in st.session_state:
    st.session_state.incomp_resolvido_sistema = False

if "incomp_choices_usuario" not in st.session_state:
    st.session_state.incomp_choices_usuario = {}
if "incomp_resolvido_usuario" not in st.session_state:
    st.session_state.incomp_resolvido_usuario = False


# --------------------------------------------------------
# Interface principal
# --------------------------------------------------------

st.title("Otimizador de Misturas NPK + Carbono")
st.markdown("**INNOVATERRA AGRISOLUTIONS**")

st.markdown(
    """
Este app calcula a mistura ideal de **Carbono, N, P₂O₅, K₂O** e outros macro/micronutrientes
com **menor custo**, usando dados do Google Sheets, compatibilidade química, Bioclástico opcional
e material inerte como QSP.
"""
)

try:
    df_insumos = carregar_insumos()
    df_mat = carregar_mat_primas()
    df_compat = carregar_compatibilidade()
except Exception as e:
    st.error(f"Falha ao ler dados do Google Sheets: {e}")
    st.stop()

st.sidebar.header("INNOVATERRA AGRISOLUTIONS")
st.sidebar.subheader("Cotação do dólar (USD/BRL)")

cotacao_api = obter_cotacao_usd_brl_api()
if cotacao_api is not None:
    st.sidebar.metric("Cotação automática", f"{cotacao_api:.4f}")
    cotacao_efetiva = cotacao_api
else:
    st.sidebar.warning("Não foi possível obter a cotação automática. Informe manualmente.")
    cotacao_manual = st.sidebar.number_input(
        "Cotação manual (R$/US$)",
        min_value=0.0,
        value=5.0,
        step=0.01,
    )
    cotacao_efetiva = cotacao_manual

st.subheader("Metas da mistura final")

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
    tolerancia = st.number_input("Tolerância relativa (%)", min_value=0.0, value=0.0, step=0.1)

st.markdown("**Outros macro e micronutrientes (metas opcionais, % m/m)**")

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

tab_sistema, tab_usuario = st.tabs(["Sugestão do sistema", "Sugestão do usuário"])

# --------------------------------------------------------
# Fluxo da aba Sugestão do sistema
# --------------------------------------------------------

with tab_sistema:
    st.subheader("Sugestão do sistema")

    permitir_bioclastico_sistema = st.radio(
        "Permitir Bioclástico?",
        ["Não", "Sim"],
        key="bio_sistema",
        horizontal=True,
    )
    st.caption(
        "Obs.: se marcar 'Não', o Bioclástico não será usado, mesmo que esteja no cadastro."
    )

    ativos_sistema = df_mat[df_mat["Tipo_funcao"] != "INERTE"].copy()
    if permitir_bioclastico_sistema == "Não":
        ativos_sistema = ativos_sistema[
            ativos_sistema["Ingrediente"] != "Bioclástico"
        ].copy()

    st.markdown("### Ingredientes disponíveis (Sistema)")
    st.dataframe(
        ativos_sistema[
            [
                "Ingrediente",
                "Preco_ton",
                "Umidade_pct",
                "MO_ms_pct",
                "C_pct",
                "N_pct",
                "P2O5_pct",
                "K2O_pct",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

    btn_sistema = st.button("Calcular mistura (Sistema)", type="primary")

    if btn_sistema or st.session_state.incomp_resolvido_sistema:
        # 1) Resolver incompatibilidades (se ainda não resolvidas)
        selecionados = ativos_sistema["Ingrediente"].tolist()
        incompat, limitados = classificar_compatibilidade(selecionados, df_compat)

        if incompat and not st.session_state.incomp_resolvido_sistema:
            st.warning(
                "Foram encontradas combinações INCOMPATÍVEIS. "
                "Escolha qual ingrediente remover em cada par e depois clique em 'Aplicar remoções'."
            )

            for idx, (a, b) in enumerate(incompat):
                st.write(f"{idx + 1}) {a} × {b} — INCOMPATÍVEL")
                escolha = st.radio(
                    f"Escolha para o par {a} × {b}",
                    [f"Remover {a}", f"Remover {b}", "Não remover nenhum"],
                    key=f"conf_incomp_sistema_{idx}",
                )
                st.session_state.incomp_choices_sistema[(a, b)] = escolha

            aplicar_remocoes = st.button(
                "Aplicar remoções de incompatibilidade (Sistema)",
                key="btn_aplicar_incomp_sistema",
            )

            if aplicar_remocoes:
                removidos = set()
                for (a, b), esc in st.session_state.incomp_choices_sistema.items():
                    if esc.startswith("Remover"):
                        nome = esc.replace("Remover ", "")
                        removidos.add(nome)

                if len(removidos) == 0 and len(incompat) > 0:
                    st.error(
                        "Nenhum ingrediente foi marcado para remoção, "
                        "mas há pares INCOMPATÍVEIS. Remova pelo menos um ingrediente por par para prosseguir."
                    )
                    st.stop()

                ativos_sistema = ativos_sistema[
                    ~ativos_sistema["Ingrediente"].isin(removidos)
                ].copy()

                selecionados_filtrados = ativos_sistema["Ingrediente"].tolist()
                incompat2, limitados2 = classificar_compatibilidade(
                    selecionados_filtrados, df_compat
                )
                if incompat2:
                    txt2 = "; ".join([f"{a} × {b}" for a, b in incompat2])
                    st.error(
                        f"Ainda há combinações INCOMPATÍVEIS após remoção: {txt2}"
                    )
                    st.stop()

                st.session_state.incomp_resolvido_sistema = True
                limitados = limitados2

            else:
                st.stop()
        else:
            # Já resolvido ou sem incompatível
            if st.session_state.incomp_resolvido_sistema:
                selecionados_filtrados = ativos_sistema["Ingrediente"].tolist()
                incompat2, limitados2 = classificar_compatibilidade(
                    selecionados_filtrados, df_compat
                )
                if incompat2:
                    txt2 = "; ".join([f"{a} × {b}" for a, b in incompat2])
                    st.error(
                        f"Ainda há combinações INCOMPATÍVEIS após remoção: {txt2}"
                    )
                    st.stop()
                limitados = limitados2

        # 2) Tratar LIMITADO com confirmação
        if limitados:
            txt_lim = "; ".join([f"{a} × {b}" for a, b in limitados])
            st.warning(f"Combinações LIMITADO detectadas: {txt_lim}")
            confirmar_lim = st.checkbox(
                "Confirmo que desejo prosseguir mesmo com combinações LIMITADO",
                key="conf_lim_sistema",
            )
            if not confirmar_lim:
                st.stop()

        # 3) Resolver base ativa
        metas_principais = {
            "C_pct": meta_c,
            "N_pct": meta_n,
            "P2O5_pct": meta_p,
            "K2O_pct": meta_k,
        }
        sol_ativos, status_msg, info = resolver_base_ativa(
            ativos_sistema, metas_principais, meta_adicionais, tolerancia, massa_final
        )

        if sol_ativos is None:
            st.error(status_msg)
        else:
            inerte_escolhido, alerta_inerte, comp_inertes, lim_inertes = escolher_inerte(
                sol_ativos, df_mat, df_compat, massa_final
            )

            if inerte_escolhido is None and alerta_inerte:
                st.warning(alerta_inerte)
                if lim_inertes:
                    nomes_lim = ", ".join(
                        [str(t[0]["Ingrediente"]) for t in lim_inertes]
                    )
                    st.info(
                        f"Inertes em condição LIMITADO disponíveis: {nomes_lim}."
                    )
                    confirmar_inerte = st.checkbox(
                        "Confirmo que aceito usar material inerte em condição LIMITADO",
                        key="conf_lim_inerte_sistema",
                    )
                    if confirmar_inerte:
                        inerte_escolhido = lim_inertes[0][0]
                    else:
                        st.stop()
                else:
                    st.error(
                        "Nenhum inerte elegível para completar a massa final."
                    )
                    st.stop()

            frames = [sol_ativos.copy()]
            if inerte_escolhido is not None:
                frames.append(pd.DataFrame([inerte_escolhido]))
            resultado = pd.concat(frames, ignore_index=True)

            massa_total_kg = resultado["Quantidade_kg"].sum()
            massa_total_ton = massa_total_kg / 1000.0
            resultado["Participacao_pct"] = (
                100 * resultado["Quantidade_kg"] / massa_total_kg
            )
            resultado["Custo_total"] = (
                resultado["Quantidade_kg"] * resultado["Preco_ton"] / 1000.0
            )

            st.success("Solução ótima encontrada (Sugestão do sistema).")

            st.subheader("Ingredientes selecionados")
            mostrar = resultado[
                [
                    "Ingrediente",
                    "Quantidade_kg",
                    "Participacao_pct",
                    "Preco_ton",
                    "C_pct",
                    "N_pct",
                    "P2O5_pct",
                    "K2O_pct",
                    "Custo_total",
                ]
            ].sort_values("Quantidade_kg", ascending=False)
            st.dataframe(mostrar, use_container_width=True, hide_index=True)

            # Insumos
            st.subheader("Insumos de produção (Sistema)")
            insumos_selecionados = []
            for idx, row in df_insumos.iterrows():
                usar = st.checkbox(
                    f"Usar {row['Insumo']} (US$ {row['Preco_usd_ton']:.2f}/t)",
                    key=f"ins_sist_{idx}",
                    value=False,
                )
                if usar:
                    insumos_selecionados.append(row)

            custo_mat_usd_ton = resultado["Custo_total"].sum() / massa_total_ton
            custo_ins_usd_ton = (
                sum(r["Preco_usd_ton"] for r in insumos_selecionados)
                if insumos_selecionados
                else 0.0
            )
            custo_total_usd_ton = custo_mat_usd_ton + custo_ins_usd_ton

            custo_mat_usd_lote = custo_mat_usd_ton * massa_total_ton
            custo_ins_usd_lote = custo_ins_usd_ton * massa_total_ton
            custo_total_usd_lote = custo_mat_usd_lote + custo_ins_usd_lote

            custo_total_brl_ton = custo_total_usd_ton * cotacao_efetiva
            custo_total_brl_lote = custo_total_usd_lote * cotacao_efetiva

            resumo_comp = resumo_nutrientes_completo(resultado)

            st.subheader("Resumo econômico (Sistema)")
            st.table(
                pd.DataFrame(
                    [
                        {"Indicador": "Massa total (kg)", "Valor": massa_total_kg},
                        {
                            "Indicador": "Custo matérias-primas (US$/t)",
                            "Valor": custo_mat_usd_ton,
                        },
                        {
                            "Indicador": "Custo insumos (US$/t)",
                            "Valor": custo_ins_usd_ton,
                        },
                        {
                            "Indicador": "Custo total (US$/t)",
                            "Valor": custo_total_usd_ton,
                        },
                        {
                            "Indicador": "Custo total (R$/t)",
                            "Valor": custo_total_brl_ton,
                        },
                        {
                            "Indicador": "Custo total do lote (US$)",
                            "Valor": custo_total_usd_lote,
                        },
                        {
                            "Indicador": "Custo total do lote (R$)",
                            "Valor": custo_total_brl_lote,
                        },
                    ]
                )
            )

            st.subheader("Composição final da mistura (Sistema)")
            st.dataframe(resumo_comp, use_container_width=True, hide_index=True)

            csv = mostrar.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Baixar resultado em CSV (Sistema)",
                data=csv,
                file_name="resultado_mistura_sistema.csv",
                mime="text/csv",
            )

            st.markdown("---")
            st.markdown("**INNOVATERRA AGRISOLUTIONS**")

# --------------------------------------------------------
# Fluxo da aba Sugestão do usuário
# --------------------------------------------------------

with tab_usuario:
    st.subheader("Sugestão do usuário")

    options_estoque = df_mat[df_mat["Tipo_funcao"] != "INERTE"]["Ingrediente"].unique().tolist()
    ingredientes_usuario = st.multiselect(
        "Matérias-primas disponíveis em estoque",
        options=options_estoque,
        default=options_estoque,
        key="ingredientes_usuario",
    )

    permitir_bioclastico_usuario = st.radio(
        "Permitir Bioclástico?",
        ["Não", "Sim"],
        key="bio_usuario",
        horizontal=True,
    )
    st.caption(
        "Obs.: se marcar 'Não', o Bioclástico será removido mesmo se estiver selecionado no estoque."
    )

    ativos_usuario = df_mat[df_mat["Ingrediente"].isin(ingredientes_usuario)].copy()
    if permitir_bioclastico_usuario == "Não":
        ativos_usuario = ativos_usuario[
            ativos_usuario["Ingrediente"] != "Bioclástico"
        ].copy()

    st.markdown("### Ingredientes disponíveis (Usuário)")
    st.dataframe(
        ativos_usuario[
            [
                "Ingrediente",
                "Preco_ton",
                "Umidade_pct",
                "MO_ms_pct",
                "C_pct",
                "N_pct",
                "P2O5_pct",
                "K2O_pct",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

    btn_usuario = st.button("Calcular mistura (Usuário)", type="primary")

    if btn_usuario or st.session_state.incomp_resolvido_usuario:
        selecionados_u = ativos_usuario["Ingrediente"].tolist()
        incompat_u, limitados_u = classificar_compatibilidade(
            selecionados_u, df_compat
        )

        if incompat_u and not st.session_state.incomp_resolvido_usuario:
            st.warning(
                "Foram encontradas combinações INCOMPATÍVEIS no estoque. "
                "Escolha qual ingrediente remover em cada par e depois clique em 'Aplicar remoções'."
            )

            for idx, (a, b) in enumerate(incompat_u):
                st.write(f"{idx + 1}) {a} × {b} — INCOMPATÍVEL")
                escolha_u = st.radio(
                    f"Escolha para o par {a} × {b}",
                    [f"Remover {a}", f"Remover {b}", "Não remover nenhum"],
                    key=f"conf_incomp_usuario_{idx}",
                )
                st.session_state.incomp_choices_usuario[(a, b)] = escolha_u

            aplicar_remocoes_u = st.button(
                "Aplicar remoções de incompatibilidade (Usuário)",
                key="btn_aplicar_incomp_usuario",
            )

            if aplicar_remocoes_u:
                removidos_u = set()
                for (a, b), esc_u in st.session_state.incomp_choices_usuario.items():
                    if esc_u.startswith("Remover"):
                        nome_u = esc_u.replace("Remover ", "")
                        removidos_u.add(nome_u)

                if len(removidos_u) == 0 and len(incompat_u) > 0:
                    st.error(
                        "Nenhum ingrediente foi marcado para remoção, "
                        "mas há pares INCOMPATÍVEIS. Remova pelo menos um ingrediente por par para prosseguir."
                    )
                    st.stop()

                ativos_usuario = ativos_usuario[
                    ~ativos_usuario["Ingrediente"].isin(removidos_u)
                ].copy()

                selecionados_filtrados_u = ativos_usuario["Ingrediente"].tolist()
                incompat2_u, limitados2_u = classificar_compatibilidade(
                    selecionados_filtrados_u, df_compat
                )
                if incompat2_u:
                    txt2_u = "; ".join([f"{a} × {b}" for a, b in incompat2_u])
                    st.error(
                        f"Ainda há combinações INCOMPATÍVEIS após remoção: {txt2_u}"
                    )
                    st.stop()

                st.session_state.incomp_resolvido_usuario = True
                limitados_u = limitados2_u

            else:
                st.stop()
        else:
            if st.session_state.incomp_resolvido_usuario:
                selecionados_filtrados_u = ativos_usuario["Ingrediente"].tolist()
                incompat2_u, limitados2_u = classificar_compatibilidade(
                    selecionados_filtrados_u, df_compat
                )
                if incompat2_u:
                    txt2_u = "; ".join([f"{a} × {b}" for a, b in incompat2_u])
                    st.error(
                        f"Ainda há combinações INCOMPATÍVEIS após remoção: {txt2_u}"
                    )
                    st.stop()
                limitados_u = limitados2_u

        if limitados_u:
            txt_lim_u = "; ".join([f"{a} × {b}" for a, b in limitados_u])
            st.warning(f"Combinações LIMITADO detectadas: {txt_lim_u}")
            confirmar_lim_u = st.checkbox(
                "Confirmo que desejo prosseguir mesmo com combinações LIMITADO",
                key="conf_lim_usuario",
            )
            if not confirmar_lim_u:
                st.stop()

        metas_principais_u = {
            "C_pct": meta_c,
            "N_pct": meta_n,
            "P2O5_pct": meta_p,
            "K2O_pct": meta_k,
        }
        sol_ativos_u, status_msg_u, info_u = resolver_base_ativa(
            ativos_usuario, metas_principais_u, meta_adicionais, tolerancia, massa_final
        )

        if sol_ativos_u is None:
            st.error(status_msg_u)
        else:
            inerte_escolhido_u, alerta_inerte_u, comp_inertes_u, lim_inertes_u = escolher_inerte(
                sol_ativos_u, df_mat, df_compat, massa_final
            )

            if inerte_escolhido_u is None and alerta_inerte_u:
                st.warning(alerta_inerte_u)
                if lim_inertes_u:
                    nomes_lim_u = ", ".join(
                        [str(t[0]["Ingrediente"]) for t in lim_inertes_u]
                    )
                    st.info(
                        f"Inertes em condição LIMITADO disponíveis: {nomes_lim_u}."
                    )
                    confirmar_inerte_u = st.checkbox(
                        "Confirmo que aceito usar material inerte em condição LIMITADO",
                        key="conf_lim_inerte_usuario",
                    )
                    if confirmar_inerte_u:
                        inerte_escolhido_u = lim_inertes_u[0][0]
                    else:
                        st.stop()
                else:
                    st.error(
                        "Nenhum inerte elegível para completar a massa final."
                    )
                    st.stop()

            frames_u = [sol_ativos_u.copy()]
            if inerte_escolhido_u is not None:
                frames_u.append(pd.DataFrame([inerte_escolhido_u]))
            resultado_u = pd.concat(frames_u, ignore_index=True)

            massa_total_kg_u = resultado_u["Quantidade_kg"].sum()
            massa_total_ton_u = massa_total_kg_u / 1000.0
            resultado_u["Participacao_pct"] = (
                100 * resultado_u["Quantidade_kg"] / massa_total_kg_u
            )
            resultado_u["Custo_total"] = (
                resultado_u["Quantidade_kg"] * resultado_u["Preco_ton"] / 1000.0
            )

            st.success("Solução ótima encontrada (Sugestão do usuário).")

            st.subheader("Ingredientes selecionados (Usuário)")
            mostrar_u = resultado_u[
                [
                    "Ingrediente",
                    "Quantidade_kg",
                    "Participacao_pct",
                    "Preco_ton",
                    "C_pct",
                    "N_pct",
                    "P2O5_pct",
                    "K2O_pct",
                    "Custo_total",
                ]
            ].sort_values("Quantidade_kg", ascending=False)
            st.dataframe(mostrar_u, use_container_width=True, hide_index=True)

            # Insumos
            st.subheader("Insumos de produção (Usuário)")
            insumos_sel_u = []
            for idx, row in df_insumos.iterrows():
                usar_u = st.checkbox(
                    f"Usar {row['Insumo']} (US$ {row['Preco_usd_ton']:.2f}/t)",
                    key=f"ins_usu_{idx}",
                    value=False,
                )
                if usar_u:
                    insumos_sel_u.append(row)

            custo_mat_usd_ton_u = (
                resultado_u["Custo_total"].sum() / massa_total_ton_u
            )
            custo_ins_usd_ton_u = (
                sum(r["Preco_usd_ton"] for r in insumos_sel_u)
                if insumos_sel_u
                else 0.0
            )
            custo_total_usd_ton_u = custo_mat_usd_ton_u + custo_ins_usd_ton_u

            custo_mat_usd_lote_u = custo_mat_usd_ton_u * massa_total_ton_u
            custo_ins_usd_lote_u = custo_ins_usd_ton_u * massa_total_ton_u
            custo_total_usd_lote_u = custo_mat_usd_lote_u + custo_ins_usd_lote_u

            custo_total_brl_ton_u = custo_total_usd_ton_u * cotacao_efetiva
            custo_total_brl_lote_u = custo_total_usd_lote_u * cotacao_efetiva

            resumo_comp_u = resumo_nutrientes_completo(resultado_u)

            st.subheader("Resumo econômico (Usuário)")
            st.table(
                pd.DataFrame(
                    [
                        {
                            "Indicador": "Massa total (kg)",
                            "Valor": massa_total_kg_u,
                        },
                        {
                            "Indicador": "Custo matérias-primas (US$/t)",
                            "Valor": custo_mat_usd_ton_u,
                        },
                        {
                            "Indicador": "Custo insumos (US$/t)",
                            "Valor": custo_ins_usd_ton_u,
                        },
                        {
                            "Indicador": "Custo total (US$/t)",
                            "Valor": custo_total_usd_ton_u,
                        },
                        {
                            "Indicador": "Custo total (R$/t)",
                            "Valor": custo_total_brl_ton_u,
                        },
                        {
                            "Indicador": "Custo total do lote (US$)",
                            "Valor": custo_total_usd_lote_u,
                        },
                        {
                            "Indicador": "Custo total do lote (R$)",
                            "Valor": custo_total_brl_lote_u,
                        },
                    ]
                )
            )

            st.subheader("Composição final da mistura (Usuário)")
            st.dataframe(resumo_comp_u, use_container_width=True, hide_index=True)

            csv_u = mostrar_u.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Baixar resultado em CSV (Usuário)",
                data=csv_u,
                file_name="resultado_mistura_usuario.csv",
                mime="text/csv",
            )

            st.markdown("---")
            st.markdown("**INNOVATERRA AGRISOLUTIONS**")
