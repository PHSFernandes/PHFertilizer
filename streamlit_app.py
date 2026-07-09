import io
import pandas as pd
import streamlit as st
from pulp import LpProblem, LpVariable, LpMinimize, lpSum, LpStatus, value, PULP_CBC_CMD

st.set_page_config(page_title="Otimizador de Misturas NPK+C", layout="wide")

st.title("Otimizador de Misturas NPK + Carbono")
st.markdown(
    """
Este app calcula a quantidade ideal de cada ingrediente para atingir metas de **Carbono, N, P₂O₅ e K₂O**
com **menor custo**, usando programação linear. O carbono do composto orgânico é calculado como **58% da matéria orgânica**,
considerando que a matéria orgânica foi informada em **base seca** e corrigida pela umidade.
A tolerância é interpretada como **relativa à meta**. Ex.: meta de 10% com tolerância de 10% gera faixa aceitável de 9% a 11%.
"""
)

st.subheader("Como informar os dados")
st.markdown(
    """
- Para ingredientes orgânicos, preencha `MO_ms_pct` e `Umidade_pct`; o app calcula `C_pct_efetivo = 0.58 × MO_ms_pct × (1 - Umidade/100)`.
- Para fontes minerais, preencha diretamente `N_pct`, `P2O5_pct` e `K2O_pct`.
- `Min_kg` e `Max_kg` são opcionais; se `Max_kg` ficar vazio, o app considera um limite alto.
- A massa final **não é fixa**; o modelo encontra a mistura mais barata que satisfaça as metas dentro da tolerância relativa informada.
- Exemplo: meta 10 e tolerância 10% = faixa de 9 a 11; meta 8 e tolerância 10% = faixa de 7,2 a 8,8.
"""
)

def modelo_exemplo():
    return pd.DataFrame([
        {"Ingrediente": "Composto orgânico", "Preco_ton": 180, "MO_ms_pct": 45.0, "Umidade_pct": 30.0, "N_pct": 1.2, "P2O5_pct": 0.8, "K2O_pct": 1.0, "Min_kg": 0.0, "Max_kg": 5000.0},
        {"Ingrediente": "Uréia", "Preco_ton": 2900, "MO_ms_pct": 0.0, "Umidade_pct": 0.0, "N_pct": 46.0, "P2O5_pct": 0.0, "K2O_pct": 0.0, "Min_kg": 0.0, "Max_kg": 1000.0},
        {"Ingrediente": "DAP", "Preco_ton": 3400, "MO_ms_pct": 0.0, "Umidade_pct": 0.0, "N_pct": 18.0, "P2O5_pct": 46.0, "K2O_pct": 0.0, "Min_kg": 0.0, "Max_kg": 1000.0},
        {"Ingrediente": "Sulfato de potássio", "Preco_ton": 4100, "MO_ms_pct": 0.0, "Umidade_pct": 0.0, "N_pct": 0.0, "P2O5_pct": 0.0, "K2O_pct": 50.0, "Min_kg": 0.0, "Max_kg": 1000.0},
    ])

uploaded = st.file_uploader("Enviar planilha CSV ou Excel", type=["csv", "xlsx"])
if uploaded is not None:
    if uploaded.name.lower().endswith(".csv"):
        df = pd.read_csv(uploaded)
    else:
        df = pd.read_excel(uploaded)
else:
    df = modelo_exemplo()

st.subheader("Tabela de ingredientes")
expected_cols = ["Ingrediente", "Preco_ton", "MO_ms_pct", "Umidade_pct", "N_pct", "P2O5_pct", "K2O_pct", "Min_kg", "Max_kg"]
for c in expected_cols:
    if c not in df.columns:
        df[c] = 0.0 if c != "Ingrediente" else ""

df = df[expected_cols]
edited = st.data_editor(df, num_rows="dynamic", use_container_width=True)

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

base_minima = st.number_input("Massa mínima total da mistura (kg)", min_value=1.0, value=1000.0, step=100.0)


def preparar_dados(data):
    out = data.copy()
    num_cols = ["Preco_ton", "MO_ms_pct", "Umidade_pct", "N_pct", "P2O5_pct", "K2O_pct", "Min_kg", "Max_kg"]
    for col in num_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    out["Ingrediente"] = out["Ingrediente"].astype(str)
    out = out[out["Ingrediente"].str.strip() != ""].copy()
    out["C_pct"] = 0.58 * out["MO_ms_pct"] * (1 - out["Umidade_pct"] / 100.0)
    out["Max_kg"] = out["Max_kg"].replace(0, 1e9)
    return out


def resolver(data, metas, tol, massa_min):
    data = preparar_dados(data)
    if data.empty:
        return None, "Nenhum ingrediente informado.", None, None

    prob = LpProblem("Mistura_NPK_C", LpMinimize)
    x = {i: LpVariable(f"x_{i}", lowBound=max(0, float(data.loc[i, "Min_kg"])), upBound=float(data.loc[i, "Max_kg"])) for i in data.index}

    total = lpSum(x[i] for i in data.index)
    prob += lpSum(x[i] * float(data.loc[i, "Preco_ton"]) / 1000.0 for i in data.index)
    prob += total >= massa_min

    nutrientes = {
        "C_pct": metas["C"],
        "N_pct": metas["N"],
        "P2O5_pct": metas["P"],
        "K2O_pct": metas["K"],
    }

    for col, alvo in nutrientes.items():
        fator_tol = tol / 100.0
        minimo = max(0.0, alvo * (1 - fator_tol))
        maximo = alvo * (1 + fator_tol)
        contrib = lpSum(x[i] * float(data.loc[i, col]) / 100.0 for i in data.index)
        prob += contrib >= (minimo / 100.0) * total
        prob += contrib <= (maximo / 100.0) * total

    prob.solve(PULP_CBC_CMD(msg=False))
    status = LpStatus[prob.status]
    if status != "Optimal":
        return data, f"Modelo sem solução ótima. Status: {status}", None, None

    data["Quantidade_kg"] = [value(x[i]) for i in data.index]
    data = data[data["Quantidade_kg"] > 1e-6].copy()
    total_kg = data["Quantidade_kg"].sum()
    data["Participacao_pct"] = 100 * data["Quantidade_kg"] / total_kg
    data["C_kg"] = data["Quantidade_kg"] * data["C_pct"] / 100.0
    data["N_kg"] = data["Quantidade_kg"] * data["N_pct"] / 100.0
    data["P2O5_kg"] = data["Quantidade_kg"] * data["P2O5_pct"] / 100.0
    data["K2O_kg"] = data["Quantidade_kg"] * data["K2O_pct"] / 100.0
    data["Custo_total"] = data["Quantidade_kg"] * data["Preco_ton"] / 1000.0

    resumo = pd.DataFrame([
        {"Indicador": "Massa total (kg)", "Valor": total_kg},
        {"Indicador": "Custo total", "Valor": data["Custo_total"].sum()},
        {"Indicador": "Custo por tonelada da mistura", "Valor": data["Custo_total"].sum() / total_kg * 1000},
        {"Indicador": "Carbono final (%)", "Valor": data["C_kg"].sum() / total_kg * 100},
        {"Indicador": "N final (%)", "Valor": data["N_kg"].sum() / total_kg * 100},
        {"Indicador": "P₂O₅ final (%)", "Valor": data["P2O5_kg"].sum() / total_kg * 100},
        {"Indicador": "K₂O final (%)", "Valor": data["K2O_kg"].sum() / total_kg * 100},
    ])
    return data, status, resumo, value(prob.objective)

if st.button("Calcular mistura ótima", type="primary"):
    resultados, status_msg, resumo, _ = resolver(
        edited,
        {"C": meta_c, "N": meta_n, "P": meta_p, "K": meta_k},
        tolerancia,
        base_minima,
    )
    if resumo is None:
        st.error(status_msg)
    else:
        st.success("Solução ótima encontrada dentro da tolerância relativa informada.")
        st.subheader("Resumo")
        st.dataframe(resumo, use_container_width=True, hide_index=True)

        st.subheader("Ingredientes selecionados")
        mostrar = resultados[[
            "Ingrediente", "Quantidade_kg", "Participacao_pct", "Preco_ton",
            "C_pct", "N_pct", "P2O5_pct", "K2O_pct", "Custo_total"
        ]].sort_values("Quantidade_kg", ascending=False)
        st.dataframe(mostrar, use_container_width=True, hide_index=True)

        csv = mostrar.to_csv(index=False).encode("utf-8")
        st.download_button("Baixar resultado em CSV", data=csv, file_name="resultado_mistura.csv", mime="text/csv")

st.subheader("Modelo de arquivo")
modelo = modelo_exemplo().to_csv(index=False).encode("utf-8")
st.download_button("Baixar modelo CSV", data=modelo, file_name="modelo_ingredientes.csv", mime="text/csv")
