# -*- coding: utf-8 -*-
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import sys
import time
import streamlit as st
import re
import pandas as pd
import pypdf
import io
import csv
import fitz  # PyMuPDF
import requests
import base64
import pdfplumber
import json
from datetime import datetime, timedelta, date
import os
import docx
import subprocess
import tempfile
import shutil
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from zoneinfo import ZoneInfo
import unicodedata

# =========================
# CONFIG GOOGLE SHEETS
# =========================
PLANILHA_URL = "https://docs.google.com/spreadsheets/d/1-am5qb_SV853v5omolRM46G8-IQH5ABJKXtoFh_WUvQ"
ABA_MODELO = "MODELO"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/calendar.events.readonly",
]


@st.cache_resource
def garantir_playwright_chromium():
    cache_dir = os.path.expanduser("~/.cache/ms-playwright")
    chromium_ok = False

    if os.path.isdir(cache_dir):
        try:
            chromium_ok = any("chromium" in nome.lower() for nome in os.listdir(cache_dir))
        except Exception:
            chromium_ok = False

    if not chromium_ok:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True
        )


# =========================
# CONSTANTES E MAPEAMENTOS
# =========================
TIPO_MAP_NORMA = {
    "LEI": "LEI",
    "RESOLUÇÃO": "RAL",
    "LEI COMPLEMENTAR": "LCP",
    "EMENDA À CONSTITUIÇÃO": "EMC",
    "DELIBERAÇÃO DA MESA": "DLB"
}

TIPO_MAP_PROP = {
    "PROJETO DE LEI": "PL",
    "PROJETO DE LEI COMPLEMENTAR": "PLC",
    "INDICAÇÃO": "IND",
    "PROJETO DE RESOLUÇÃO": "PRE",
    "PROPOSTA DE EMENDA À CONSTITUIÇÃO": "PEC",
    "MENSAGEM": "MSG",
    "VETO": "VET"
}

SIGLA_MAP_PARECER = {
    "requerimento": "RQN",
    "projeto de lei": "PL",
    "pl": "PL",
    "projeto de resolução": "PRE",
    "pre": "PRE",
    "proposta de emenda à constituição": "PEC",
    "pec": "PEC",
    "projeto de lei complementar": "PLC",
    "plc": "PLC",
    "emendas ao projeto de lei": "EMENDA"
}

meses = {
    "JANEIRO": "01",
    "FEVEREIRO": "02",
    "MARÇO": "03",
    "MARCO": "03",
    "ABRIL": "04",
    "MAIO": "05",
    "JUNHO": "06",
    "JULHO": "07",
    "AGOSTO": "08",
    "SETEMBRO": "09",
    "OUTUBRO": "10",
    "NOVEMBRO": "11",
    "DEZEMBRO": "12"
}

# =========================
# GOOGLE SHEETS
# =========================
TIMEZONE_PADRAO = "America/Sao_Paulo"
COLUNAS_RESPONSAVEIS = (7, 8, 14, 15)  # G, H, N, O
PADRAO_AFASTAMENTO = re.compile(r"^\s*(licen[cç]a|f[eé]rias)\s*[:\-]\s*(.+?)\s*$", re.IGNORECASE)

GRUPOS_EQUIPE = {
    "BIBLIOTECARIO": ["TIAGO", "PAULO", "CIRLENE", "SILVANA", "ROBSON", "MARCIA"],
    "BIBLIOTECARIO_TARDE": ["ROBSON", "SILVANA"],
    "BIBLIOTECARIO_EXEC": ["TIAGO", "PAULO", "CIRLENE", "MARCIA"],
    "ESTAGIARIO": ["ISABELA", "NÉLIA"],
    "TECNICO": ["ISADORA", "CLÉLIA"],
}

REGRAS_TAREFA = {
    "implantacao_normas_dne": {
        "grupos": ["ESTAGIARIO", "TECNICO"],
        "excluir": ["CLÉLIA", "MARCIA"],
    },
    "implantacao_normas_nao_dne": {
        "grupos": ["ESTAGIARIO", "TECNICO", "BIBLIOTECARIO_EXEC"],
        "excluir": ["PAULO", "CLÉLIA", "MARCIA"],
    },
    "revisao_normas": {
        "grupos": ["BIBLIOTECARIO"],
        "excluir": ["CLÉLIA", "MARCIA"],
    },

    "execucao_proposicoes_nao_up": {
        "grupos": ["BIBLIOTECARIO_EXEC", "ESTAGIARIO", "TECNICO"],
        "excluir": ["CLÉLIA"],
    },
    "execucao_proposicoes_up": {
        "incluir": ["CLÉLIA", "ISADORA"],
    },
    "revisao_proposicoes": {
        "grupos": ["BIBLIOTECARIO"],
    },

    "execucao_requerimentos": {
        "grupos": ["BIBLIOTECARIO_EXEC", "ESTAGIARIO", "TECNICO"],
    },
    "revisao_requerimentos": {
        "grupos": ["BIBLIOTECARIO"],
    },

    "execucao_pareceres": {
        "grupos": ["BIBLIOTECARIO_EXEC", "ESTAGIARIO", "TECNICO"],
        "excluir": ["CLÉLIA"],
    },
    "revisao_pareceres": {
        "grupos": ["BIBLIOTECARIO"],
    },
}
ROTULOS_TAREFA = {
    "implantacao_normas_dne": "Implantação de normas DNE",
    "implantacao_normas_nao_dne": "Implantação de normas não DNE",
    "revisao_normas": "Revisão de normas",
    "execucao_proposicoes_nao_up": "Execução de proposições não UP",
    "execucao_proposicoes_up": "Execução de proposições UP",
    "revisao_proposicoes": "Revisão de proposições",
    "execucao_requerimentos": "Execução de requerimentos",
    "revisao_requerimentos": "Revisão de requerimentos",
    "execucao_pareceres": "Execução de pareceres",
    "revisao_pareceres": "Revisão de pareceres",
}


def remover_acentos(texto: str) -> str:
    texto = str(texto or "")
    return "".join(
        ch for ch in unicodedata.normalize("NFD", texto)
        if unicodedata.category(ch) != "Mn"
    )


def nome_planilha(nome: str) -> str:
    return re.sub(r"\s+", " ", str(nome or "").strip()).upper()


def normalizar_nome_chave(nome: str) -> str:
    return remover_acentos(nome_planilha(nome))


def construir_mapa_nomes_equipe() -> dict:
    mapa = {}
    for pessoas in GRUPOS_EQUIPE.values():
        for pessoa in pessoas:
            mapa[normalizar_nome_chave(pessoa)] = nome_planilha(pessoa)
    return mapa


MAPA_NOMES_EQUIPE = construir_mapa_nomes_equipe()


def obter_google_credentials():
    creds_dict = st.secrets["gcp_service_account"]
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=GOOGLE_SCOPES
    )

    usuario_impersonado = st.secrets.get("calendar_impersonate_user", "")
    if usuario_impersonado:
        try:
            creds = creds.with_subject(usuario_impersonado)
        except Exception:
            pass

    return creds


def conectar_gsheet():
    creds = obter_google_credentials()
    client = gspread.authorize(creds)
    return client.open_by_url(PLANILHA_URL)

def conectar_calendar():
    creds_dict = st.secrets["gcp_service_account"]

    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=GOOGLE_SCOPES
    )

    return build(
        "calendar",
        "v3",
        credentials=creds,
        cache_discovery=False
    )


def buscar_afastamentos_calendar(data_ref: date):
    service = conectar_calendar()
    calendar_id = st.secrets["GOOGLE_CALENDAR_ID"]

    tz = ZoneInfo("America/Sao_Paulo")
    inicio = datetime.combine(data_ref, datetime.min.time(), tzinfo=tz)
    fim = inicio + timedelta(days=1)

    termos = ["Férias:", "Licença:"]
    eventos_por_id = {}

    for termo in termos:
        resultado = service.events().list(
            calendarId=calendar_id,
            timeMin=inicio.isoformat(),
            timeMax=fim.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            q=termo,
            maxResults=250
        ).execute()

        for evento in resultado.get("items", []):
            titulo = evento.get("summary", "").strip()
            titulo_normalizado = titulo.lower()

            if titulo_normalizado.startswith((
                "férias:",
                "ferias:",
                "licença:",
                "licenca:"
            )):
                eventos_por_id[evento["id"]] = evento

    return list(eventos_por_id.values())


@st.cache_resource
def conectar_calendar_service():
    creds = obter_google_credentials()
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def obter_calendarios_afastamento() -> list[str]:
    calendar_ids = st.secrets.get("GOOGLE_CALENDAR_ID", "")

    if isinstance(calendar_ids, str):
        calendar_ids = [x.strip() for x in calendar_ids.split(",") if x.strip()]

    return [str(x).strip() for x in calendar_ids if str(x).strip()]


def extrair_nome_evento_afastamento(summary: str) -> str:
    m = PADRAO_AFASTAMENTO.match(summary or "")
    if not m:
        return ""

    nome_raw = m.group(2).strip()
    nome_raw = re.sub(r"\s*\(.*?\)\s*$", "", nome_raw).strip()
    return MAPA_NOMES_EQUIPE.get(normalizar_nome_chave(nome_raw), "")


def evento_atinge_data(evento: dict, data_ref: date, tz: ZoneInfo) -> bool:
    start = evento.get("start", {})
    end = evento.get("end", {})

    if "date" in start and "date" in end:
        inicio = date.fromisoformat(start["date"])
        fim_exclusivo = date.fromisoformat(end["date"])
        return inicio <= data_ref < fim_exclusivo

    start_dt_raw = start.get("dateTime")
    end_dt_raw = end.get("dateTime")
    if not start_dt_raw or not end_dt_raw:
        return False

    inicio = datetime.fromisoformat(start_dt_raw.replace("Z", "+00:00")).astimezone(tz)
    fim = datetime.fromisoformat(end_dt_raw.replace("Z", "+00:00")).astimezone(tz)
    dia_inicio = datetime.combine(data_ref, datetime.min.time(), tzinfo=tz)
    dia_fim = dia_inicio + timedelta(days=1)
    return inicio < dia_fim and fim > dia_inicio


def listar_indisponiveis_calendar(data_ref: date) -> tuple[set[str], str]:
    calendar_ids = obter_calendarios_afastamento()
    if not calendar_ids:
        return set(), "Integração com Google Calendar desativada: configure 'calendar_ids_afastamentos' no st.secrets para bloquear Licença/Férias."

    try:
        service = conectar_calendar_service()
    except Exception as e:
        return set(), f"Não foi possível conectar ao Google Calendar: {e}"

    tz = ZoneInfo(TIMEZONE_PADRAO)
    consulta_inicio = datetime.combine(data_ref - timedelta(days=31), datetime.min.time(), tzinfo=tz)
    consulta_fim = datetime.combine(data_ref + timedelta(days=32), datetime.min.time(), tzinfo=tz)

    indisponiveis = set()

    try:
        for calendar_id in calendar_ids:
            page_token = None
            while True:
                resposta = service.events().list(
                    calendarId=calendar_id,
                    timeMin=consulta_inicio.isoformat(),
                    timeMax=consulta_fim.isoformat(),
                    showDeleted=False,
                    singleEvents=False,
                    maxResults=2500,
                    pageToken=page_token,
                ).execute()

                for evento in resposta.get("items", []):
                    if not evento_atinge_data(evento, data_ref, tz):
                        continue

                    nome = extrair_nome_evento_afastamento(evento.get("summary", ""))
                    if nome:
                        indisponiveis.add(nome)

                page_token = resposta.get("nextPageToken")
                if not page_token:
                    break

    except HttpError as e:
        return set(), f"Erro ao consultar o Google Calendar: {e}"
    except Exception as e:
        return set(), f"Falha ao consultar afastamentos no Google Calendar: {e}"

    return indisponiveis, ""


def candidatos_para_tarefa(chave_tarefa: str, indisponiveis: set[str] | None = None) -> list[str]:
    regra = REGRAS_TAREFA.get(chave_tarefa, {})
    indisponiveis = {nome_planilha(x) for x in (indisponiveis or set())}
    excluir = {nome_planilha(x) for x in regra.get("excluir", [])}

    candidatos = []
    vistos = set()

    for grupo in regra.get("grupos", []):
        for pessoa in GRUPOS_EQUIPE.get(grupo, []):
            pessoa_fmt = nome_planilha(pessoa)
            if not pessoa_fmt or pessoa_fmt in vistos or pessoa_fmt in excluir or pessoa_fmt in indisponiveis:
                continue
            vistos.add(pessoa_fmt)
            candidatos.append(pessoa_fmt)

    for pessoa in regra.get("incluir", []):
        pessoa_fmt = nome_planilha(pessoa)
        if not pessoa_fmt or pessoa_fmt in vistos or pessoa_fmt in excluir or pessoa_fmt in indisponiveis:
            continue
        vistos.add(pessoa_fmt)
        candidatos.append(pessoa_fmt)

    return candidatos

def pessoa_pertence_ao_grupo(nome: str, grupo: str) -> bool:
    nome_fmt = nome_planilha(nome)
    return nome_fmt in {nome_planilha(p) for p in GRUPOS_EQUIPE.get(grupo, [])}


def inicializar_df_responsaveis(df: pd.DataFrame) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()

    df = df.copy()

    if "ResponsavelExecucao" not in df.columns:
        df["ResponsavelExecucao"] = ""

    if "ResponsavelRevisao" not in df.columns:
        df["ResponsavelRevisao"] = ""

    return df


def distribuir_para_posicoes(total_linhas: int, posicoes: list[int], pessoas: list[str]) -> dict[int, str]:
    distribuicao = distribuir_em_blocos(len(posicoes), pessoas)
    return {pos: nome for pos, nome in zip(posicoes, distribuicao)}


def eh_norma_dne(r: pd.Series) -> bool:
    return nome_planilha(r.get("Tipo", "")) == "DNE"


def eh_proposicao_up(r: pd.Series) -> bool:
    valor = r.get("Observação", r.get("Categoria", ""))
    return nome_planilha(valor) == "UP"

REQ_SEM_TRATAMENTO = {
    "VOTO DE CONGRATULAÇÕES",
    "MANIFESTAÇÃO DE PESAR",
    "MANIFESTAÇÃO DE REPÚDIO",
    "MOÇÃO DE APLAUSO",
    "MANIFESTAÇÃO DE APOIO",
    "APLAUSO",
}


def requerimento_sem_tratamento(r: pd.Series) -> bool:
    valor = r.get("Observação", r.get("Classificação", ""))
    return nome_planilha(valor) in REQ_SEM_TRATAMENTO


def distribuir_revisores_sem_mesma_pessoa(execucoes: list[str], candidatos_rev: list[str]) -> list[str]:
    execucoes = [nome_planilha(x) for x in execucoes]
    candidatos_rev = [nome_planilha(x) for x in candidatos_rev if str(x).strip()]

    if not execucoes:
        return []
    if not candidatos_rev:
        return [""] * len(execucoes)

    revisao_base = distribuir_em_blocos(len(execucoes), candidatos_rev)
    uso = {p: 0 for p in candidatos_rev}
    revisoes = []

    for i, executor in enumerate(execucoes):
        preferido = revisao_base[i] if i < len(revisao_base) else ""

        if preferido and preferido != executor:
            escolhido = preferido
        else:
            opcoes = [p for p in candidatos_rev if p != executor]

            if not opcoes:
                escolhido = ""
            else:
                menor_uso = min(uso[p] for p in opcoes)
                escolhido = next(
                    p for p in candidatos_rev
                    if p in opcoes and uso[p] == menor_uso
                )

        revisoes.append(escolhido)
        if escolhido:
            uso[escolhido] += 1

    return revisoes


class DistribuidorRoundRobin:
    def __init__(self):
        self._indices = {}

    def proximo(self, chave: str, candidatos: list[str]) -> str:
        if not chave or not candidatos:
            return ""

        idx_atual = self._indices.get(chave, 0)
        escolhido = candidatos[idx_atual % len(candidatos)]
        self._indices[chave] = idx_atual + 1
        return escolhido


def linha_continuacao_norma(r: pd.Series) -> bool:
    campos_base = [
        r.get("Página", ""),
        r.get("Coluna", ""),
        r.get("Sanção", ""),
        r.get("Tipo", ""),
        r.get("Número", ""),
    ]
    return all(str(v).strip() == "" for v in campos_base) and str(r.get("Alterações", "")).strip() != ""


def distribuir_responsaveis_dataframe(
    df: pd.DataFrame,
    chave_execucao: str = "",
    chave_revisao: str = "",
    indisponiveis: set[str] | None = None,
    distribuidor: DistribuidorRoundRobin | None = None,
    replicar_em_linhas_continuacao: bool = False,
) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()

    df = df.copy()
    if df.empty:
        if "ResponsavelExecucao" not in df.columns:
            df["ResponsavelExecucao"] = ""
        if "ResponsavelRevisao" not in df.columns:
            df["ResponsavelRevisao"] = ""
        return df

    distribuidor = distribuidor or DistribuidorRoundRobin()
    candidatos_exec = candidatos_para_tarefa(chave_execucao, indisponiveis) if chave_execucao else []
    candidatos_rev = candidatos_para_tarefa(chave_revisao, indisponiveis) if chave_revisao else []

    execucoes = []
    revisoes = []
    ultimo_exec = ""
    ultimo_rev = ""

    for _, r in df.iterrows():
        if replicar_em_linhas_continuacao and linha_continuacao_norma(r):
            execucoes.append(ultimo_exec)
            revisoes.append(ultimo_rev)
            continue

        ultimo_exec = distribuidor.proximo(chave_execucao, candidatos_exec) if chave_execucao else ""
        ultimo_rev = distribuidor.proximo(chave_revisao, candidatos_rev) if chave_revisao else ""
        execucoes.append(ultimo_exec)
        revisoes.append(ultimo_rev)

    df["ResponsavelExecucao"] = execucoes
    df["ResponsavelRevisao"] = revisoes
    return df


def distribuir_tarefas_extraidas_em_blocos(
    df_exec: pd.DataFrame,
    df_adm: pd.DataFrame,
    df_leg_normas: pd.DataFrame,
    df_props: pd.DataFrame,
    df_reqs: pd.DataFrame,
    df_pareceres: pd.DataFrame,
    indisponiveis: set[str] | None = None,
):
    df_exec = atribuir_responsaveis_normas(
        df_exec,
        indisponiveis=indisponiveis,
        replicar_em_linhas_continuacao=True,
    )

    df_adm = atribuir_responsaveis_normas(
        df_adm,
        indisponiveis=indisponiveis,
        replicar_em_linhas_continuacao=True,
    )

    df_leg_normas = atribuir_responsaveis_normas(
        df_leg_normas,
        indisponiveis=indisponiveis,
        replicar_em_linhas_continuacao=True,
    )

    df_props = atribuir_responsaveis_proposicoes(
        df_props,
        indisponiveis=indisponiveis,
    )

    df_reqs = atribuir_responsaveis_requerimentos(
        df_reqs,
        indisponiveis=indisponiveis,
    )

    df_pareceres = atribuir_responsaveis_pareceres(
        df_pareceres,
        indisponiveis=indisponiveis,
    )

    return df_exec, df_adm, df_leg_normas, df_props, df_reqs, df_pareceres


def validar_pools_distribuicao(indisponiveis: set[str] | None = None) -> list[str]:
    avisos = []
    for chave, rotulo in ROTULOS_TAREFA.items():
        candidatos = candidatos_para_tarefa(chave, indisponiveis)
        if not candidatos:
            avisos.append(f"Sem pessoas disponíveis para {rotulo}.")
    return avisos


def nome_aba_data(data_str: str) -> str:
    return datetime.strptime(data_str, "%d/%m/%Y").strftime("%d/%m")


def listar_nomes_abas(spreadsheet) -> set:
    return {ws.title.strip() for ws in spreadsheet.worksheets()}


def aba_existe(spreadsheet, data_str: str) -> tuple[bool, str]:
    nome_aba = nome_aba_data(data_str)
    nome_aba_alt = nome_aba.replace("/", "-")

    nomes = listar_nomes_abas(spreadsheet)

    if nome_aba in nomes:
        return True, nome_aba
    if nome_aba_alt in nomes:
        return True, nome_aba_alt

    return False, nome_aba


def obter_ou_criar_aba_data(spreadsheet, data_str: str, nome_modelo: str = ABA_MODELO):
    existe, nome_encontrado = aba_existe(spreadsheet, data_str)
    if existe:
        raise ValueError(
            f"A aba '{nome_encontrado}' já existe. Operação bloqueada para evitar sobrescrita."
        )

    nome_aba = nome_aba_data(data_str)
    modelo = spreadsheet.worksheet(nome_modelo)

    try:
        spreadsheet.duplicate_sheet(
            source_sheet_id=modelo.id,
            new_sheet_name=nome_aba
        )
        return spreadsheet.worksheet(nome_aba)
    except Exception:
        nome_aba_alt = nome_aba.replace("/", "-")
        spreadsheet.duplicate_sheet(
            source_sheet_id=modelo.id,
            new_sheet_name=nome_aba_alt
        )
        return spreadsheet.worksheet(nome_aba_alt)


def encontrar_linha(ws, texto: str, ocorrencia: int = 1):
    valores = ws.col_values(1)
    alvo = texto.strip().upper()
    cont = 0

    for idx, valor in enumerate(valores, start=1):
        if str(valor).strip().upper() == alvo:
            cont += 1
            if cont == ocorrencia:
                return idx
    raise ValueError(f"Marcador '{texto}' (ocorrência {ocorrencia}) não encontrado na aba.")


def encontrar_linha_safe(ws, texto: str, ocorrencia: int = 1):
    try:
        return encontrar_linha(ws, texto, ocorrencia)
    except Exception:
        return None


def num_to_col(n: int) -> str:
    resultado = ""
    while n > 0:
        n, resto = divmod(n - 1, 26)
        resultado = chr(65 + resto) + resultado
    return resultado


def escrever_bloco(ws, linha_inicial: int, linhas: list[list], mesclar_coluna_a: bool = True):
    if not linhas:
        return

    ncols = max(len(l) for l in linhas)
    linhas = [l + [""] * (ncols - len(l)) for l in linhas]

    formulas_para_reaplicar = []
    for i, linha in enumerate(linhas, start=linha_inicial):
        for j, valor in enumerate(linha, start=1):
            if isinstance(valor, str) and valor.startswith("="):
                formulas_para_reaplicar.append({
                    "range": f"{num_to_col(j)}{i}",
                    "values": [[valor]]
                })

    extras = len(linhas) - 1
    if extras > 0:
        ws.insert_rows(
            [[""] * ncols for _ in range(extras)],
            row=linha_inicial + 1,
            value_input_option="USER_ENTERED",
            inherit_from_before=True
        )

    col_fim = num_to_col(ncols)
    linha_fim = linha_inicial + len(linhas) - 1
    faixa = f"A{linha_inicial}:{col_fim}{linha_fim}"

    ws.update(
        faixa,
        linhas,
        value_input_option="USER_ENTERED"
    )

    ws.format(
        faixa,
        {
            "backgroundColor": {
                "red": 1.0,
                "green": 1.0,
                "blue": 1.0
            },
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
            "textFormat": {
                "fontFamily": "Inconsolata",
                "fontSize": 10,
                "bold": True
            }
        }
    )

    if mesclar_coluna_a and len(linhas) > 1:
        faixa_merge = f"A{linha_inicial}:A{linha_fim}"

        try:
            ws.unmerge_cells(faixa_merge)
        except Exception:
            pass

        ws.merge_cells(faixa_merge)

        ws.format(
            faixa_merge,
            {
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
                "textFormat": {
                    "fontFamily": "Inconsolata",
                    "fontSize": 10,
                    "bold": True
                }
            }
        )

    if formulas_para_reaplicar:
        ws.batch_update(
            formulas_para_reaplicar,
            value_input_option="USER_ENTERED"
        )


def mesclar_linhas_intervalo(ws, linha_inicial: int, qtd_linhas: int, col_inicial: int, col_final: int):
    if qtd_linhas <= 0:
        return

    start_row = linha_inicial - 1
    end_row = linha_inicial + qtd_linhas - 1
    start_col = col_inicial - 1
    end_col = col_final

    faixa_total = {
        "sheetId": ws.id,
        "startRowIndex": start_row,
        "endRowIndex": end_row,
        "startColumnIndex": start_col,
        "endColumnIndex": end_col
    }

    try:
        ws.spreadsheet.batch_update({
            "requests": [
                {
                    "unmergeCells": {
                        "range": faixa_total
                    }
                }
            ]
        })
    except Exception:
        pass

    requests_batch = []

    for linha in range(linha_inicial, linha_inicial + qtd_linhas):
        faixa_linha = {
            "sheetId": ws.id,
            "startRowIndex": linha - 1,
            "endRowIndex": linha,
            "startColumnIndex": start_col,
            "endColumnIndex": end_col
        }

        requests_batch.append({
            "mergeCells": {
                "range": faixa_linha,
                "mergeType": "MERGE_ALL"
            }
        })

        requests_batch.append({
            "repeatCell": {
                "range": faixa_linha,
                "cell": {
                    "userEnteredFormat": {
                        "horizontalAlignment": "LEFT",
                        "verticalAlignment": "MIDDLE",
                        "textFormat": {
                            "fontFamily": "Inconsolata",
                            "fontSize": 10,
                            "bold": True
                        }
                    }
                },
                "fields": "userEnteredFormat(horizontalAlignment,verticalAlignment,textFormat)"
            }
        })

    if requests_batch:
        ws.spreadsheet.batch_update({"requests": requests_batch})


def escrever_celula(ws, celula: str, valor):
    ws.update(celula, [[valor]], value_input_option="USER_ENTERED")


def desmesclar_intervalo(ws, linha_inicial: int, qtd_linhas: int, col_inicial: int, col_final: int):
    if qtd_linhas <= 0:
        return

    faixa_total = {
        "sheetId": ws.id,
        "startRowIndex": linha_inicial - 1,
        "endRowIndex": linha_inicial + qtd_linhas - 1,
        "startColumnIndex": col_inicial - 1,
        "endColumnIndex": col_final,
    }

    try:
        ws.spreadsheet.batch_update({
            "requests": [
                {
                    "unmergeCells": {
                        "range": faixa_total
                    }
                }
            ]
        })
    except Exception:
        pass


def aplicar_cor_responsaveis(ws, linha_inicial: int, linhas: list[list], colunas=COLUNAS_RESPONSAVEIS):
    if not linhas:
        return

    requests_batch = []
    for idx_linha, linha in enumerate(linhas, start=linha_inicial):
        for col in colunas:
            if col > len(linha):
                continue

            valor = str(linha[col - 1]).strip()
            if not valor or valor == "-":
                continue

            requests_batch.append({
                "repeatCell": {
                    "range": {
                        "sheetId": ws.id,
                        "startRowIndex": idx_linha - 1,
                        "endRowIndex": idx_linha,
                        "startColumnIndex": col - 1,
                        "endColumnIndex": col,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {
                                "foregroundColor": {
                                    "red": 1.0,
                                    "green": 0.00,
                                    "blue": 0.00,
                                },
                                "bold": True,
                            }
                        }
                    },
                    "fields": "userEnteredFormat.textFormat.foregroundColor,userEnteredFormat.textFormat.bold"
                }
            })

    if requests_batch:
        ws.spreadsheet.batch_update({"requests": requests_batch})


def contar_alteracoes(df: pd.DataFrame) -> int:
    if df is None or df.empty or "Alterações" not in df.columns:
        return 0
    return int(
        df["Alterações"]
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
        .sum()
    )

def obter_quantidade_e_vides(alteracao):
    texto = str(alteracao or "").strip().upper()

    if not texto:
        return "-", "-"

    if texto == "DEC 48589 2023":
        return 0, 1

    return 1, 1


def contar_normas_principais(df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0

    return int(
        (~df.apply(linha_continuacao_norma, axis=1)).sum()
    )


def somar_quantidade_vides(df: pd.DataFrame) -> tuple[int, int]:
    if df is None or df.empty or "Alterações" not in df.columns:
        return 0, 0

    total_quantidade = 0
    total_vides = 0

    for alteracao in df["Alterações"].fillna("").astype(str):
        qtd, vides = obter_quantidade_e_vides(alteracao)

        if qtd != "-":
            total_quantidade += int(qtd)

        if vides != "-":
            total_vides += int(vides)

    return total_quantidade, total_vides


# =========================
# DATA / UI OPERACIONAL
# =========================
def ajustar_data_operacional(dt: date) -> date:
    # weekday(): segunda=0 ... domingo=6
    if dt.weekday() == 0:  # segunda-feira
        return dt - timedelta(days=2)  # sábado anterior
    if dt.weekday() == 6:  # domingo
        return dt - timedelta(days=1)  # sábado anterior
    return dt


def data_padrao_operacional() -> date:
    return ajustar_data_operacional(date.today())


def preparar_datas(data_str):
    dt = datetime.strptime(data_str, "%d/%m/%Y")
    return {
        "yyyy": dt.strftime("%Y"),
        "mm": dt.strftime("%m"),
        "dd": dt.strftime("%d"),
        "yyyymmdd": dt.strftime("%Y%m%d"),
        "iso_exec": dt.strftime("%Y-%m-%dT06:00:00.000Z"),
        "display": dt.strftime("%d/%m/%Y"),
    }


# =========================
# URLS
# =========================
def montar_urls(d):
    return {
        "executivo_html": (
            "https://www.jornalminasgerais.mg.gov.br/edicao-do-dia"
            f"?dados=%7B%22dataPublicacaoSelecionada%22:%22{d['iso_exec']}%22%7D"
        ),
        "legislativo": f"https://diariolegislativo.almg.gov.br/{d['yyyy']}/L{d['yyyymmdd']}.pdf",
        "administrativo": (
            "https://intra.almg.gov.br/export/sites/default/acontece/"
            f"diario-administrativo/arquivos/{d['yyyy']}/{d['mm']}/L{d['yyyymmdd']}.pdf"
        ),
    }


# =========================
# DOWNLOAD
# =========================
def baixar(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


# =========================
# UTILITÁRIOS EXTRATOR
# =========================
def classify_req(segment: str) -> str:
    s = re.sub(r"\s+", " ", segment).strip().lower()

    if "seja formulado voto de congratulações" in s:
        return "Voto de congratulações"
    if "manifestação de pesar" in s:
        return "Manifestação de pesar"
    if "manifestação de repúdio" in s:
        return "Manifestação de repúdio"
    if "moção de aplauso" in s:
        return "Moção de aplauso"
    if "manifestação de apoio" in s:
        return "Manifestação de apoio"
    return ""


# =========================
# EXECUTIVO - DOWNLOAD PDF
# =========================
def baixar_pdf_jornal_mg_por_link(url_pagina: str) -> bytes:
    ultimo_erro = None

    for tentativa in range(3):
        try:
            match = re.search(r'dados=([^&]+)', url_pagina)
            if not match:
                raise Exception("Parâmetro dados não encontrado")

            dados_codificados = match.group(1)
            json_str = requests.utils.unquote(dados_codificados)
            dados = json.loads(json_str)

            data_iso = dados["dataPublicacaoSelecionada"]
            data = data_iso.split("T")[0]

            api_url = (
                "https://www.jornalminasgerais.mg.gov.br/api/v1/Jornal/"
                f"ObterEdicaoPorDataPublicacao?dataPublicacao={data}"
            )

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage"
                    ]
                )

                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                    locale="pt-BR"
                )

                page = context.new_page()
                page.goto(url_pagina, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)

                resultado = page.evaluate(
                    """
                    async ({ apiUrl }) => {
                        const resp = await fetch(apiUrl, {
                            method: "GET",
                            credentials: "include",
                            headers: {
                                "accept": "application/json, text/plain, */*"
                            }
                        });

                        const text = await resp.text();
                        return {
                            status: resp.status,
                            text
                        };
                    }
                    """,
                    {"apiUrl": api_url}
                )

                browser.close()

            status = resultado["status"]
            if status != 200:
                raise Exception(f"API do Executivo retornou HTTP {status}")

            dados_api = json.loads(resultado["text"])
            base64_pdf = dados_api["dados"]["arquivoCadernoPrincipal"]["arquivo"]
            return base64.b64decode(base64_pdf)

        except PlaywrightTimeoutError as e:
            ultimo_erro = f"Timeout no Playwright: {e}"
        except Exception as e:
            ultimo_erro = e

        if tentativa < 2:
            time.sleep(2 * (tentativa + 1))

    raise Exception(f"Erro ao obter PDF do Executivo após 3 tentativas: {ultimo_erro}")


# =========================
# PREENCHIMENTO DO MODELO
# =========================
def montar_link_data(texto_data: str, url: str) -> str:
    if not url:
        return texto_data

    texto_data = str(texto_data).replace('"', '""')
    url = str(url).replace('"', '""')

    return f'=HIPERLINK("{url}";"{texto_data}")'


def montar_link_numero_norma(tipo: str, numero, sancao: str) -> str:
    numero_txt = str(numero).strip()
    tipo_txt = str(tipo).strip().upper()
    sancao_txt = str(sancao).strip()

    if not numero_txt or not tipo_txt:
        return numero_txt

    ano = ""
    m = re.search(r"(\d{4})$", sancao_txt)
    if m:
        ano = m.group(1)

    if not ano:
        return numero_txt

    url = f"https://www.almg.gov.br/legislacao-mineira/{tipo_txt}/{numero_txt}/{ano}/"
    numero_txt_esc = numero_txt.replace('"', '""')
    url_esc = url.replace('"', '""')

    return f'=HIPERLINK("{url_esc}";"{numero_txt_esc}")'


def montar_link_alteracao_norma(alteracao) -> str:
    texto = str(alteracao).strip()

    if not texto:
        return ""

    partes = texto.split()

    if len(partes) < 3:
        return texto

    tipo_txt = partes[0].strip().upper()
    numero_txt = partes[1].strip()
    ano_txt = partes[2].strip()

    if not tipo_txt or not numero_txt or not ano_txt:
        return texto

    url = f"https://www.almg.gov.br/legislacao-mineira/{tipo_txt}/{numero_txt}/{ano_txt}/"

    texto_esc = texto.replace('"', '""')
    url_esc = url.replace('"', '""')

    return f'=HIPERLINK("{url_esc}";"{texto_esc}")'


def montar_link_numero_proposicao(tipo: str, numero, ano) -> str:
    numero_txt = str(numero).strip()
    tipo_txt = str(tipo).strip().upper()
    ano_txt = str(ano).strip()

    if not numero_txt or not tipo_txt or not ano_txt:
        return numero_txt

    url = f"https://www.almg.gov.br/projetos-de-lei/{tipo_txt}/{numero_txt}/{ano_txt}/"
    numero_txt_esc = numero_txt.replace('"', '""')
    url_esc = url.replace('"', '""')

    return f'=HIPERLINK("{url_esc}";"{numero_txt_esc}")'

def montar_linhas_normas(
    data_str: str,
    df: pd.DataFrame,
    url_diario: str = "",
    preencher_vazio_com_traco: bool = False
) -> list[list]:
    link_data = montar_link_data(data_str, url_diario)

    if df is None or df.empty:
        if preencher_vazio_com_traco:
            return [[link_data, "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-"]]
        return [[link_data, "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""]]

    df = df.fillna("")
    linhas = []

    for i, (_, r) in enumerate(df.iterrows()):
        numero_link = montar_link_numero_norma(
            tipo=r.get("Tipo", ""),
            numero=r.get("Número", ""),
            sancao=r.get("Sanção", "")
        )

        alteracao = r.get("Alterações", "")
        alteracao_link = montar_link_alteracao_norma(alteracao)

        responsavel_exec = nome_planilha(r.get("ResponsavelExecucao", ""))
        responsavel_rev = nome_planilha(r.get("ResponsavelRevisao", ""))

        eh_continuacao = linha_continuacao_norma(r)
        tem_alteracao = bool(str(alteracao).strip())

        quantidade, vides = obter_quantidade_e_vides(alteracao)

        if eh_continuacao:
            linhas.append([
                link_data if i == 0 else "",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                quantidade,
                alteracao_link,
                vides,
                responsavel_exec,
                responsavel_rev,
                "-",
                "-",
                r.get("Observação", "")
            ])
        else:
            linhas.append([
                link_data if i == 0 else "",
                r.get("Página", ""),
                r.get("Coluna", ""),
                r.get("Sanção", ""),
                r.get("Tipo", ""),
                numero_link,
                responsavel_exec,
                responsavel_rev,
                quantidade if tem_alteracao else "-",
                alteracao_link if tem_alteracao else "-",
                vides if tem_alteracao else "-",
                responsavel_exec if tem_alteracao else "-",
                responsavel_rev if tem_alteracao else "-",
                responsavel_exec,
                responsavel_rev,
                r.get("Observação", "")
            ])

    return linhas


def montar_linhas_proposicoes(
    data_str: str,
    df: pd.DataFrame,
    url_diario: str = "",
    preencher_vazio_com_traco: bool = False
) -> list[list]:
    link_data = montar_link_data(data_str, url_diario)

    if df is None or df.empty:
        if preencher_vazio_com_traco:
            return [[link_data, "-", "-", "-", "-", "-", "-"]]
        return [[link_data, "", "", "", "", "", ""]]

    df = df.fillna("")
    linhas = []

    for i, (_, r) in enumerate(df.iterrows()):
        numero_link = montar_link_numero_proposicao(
            tipo=r.get("Tipo", ""),
            numero=r.get("Número", ""),
            ano=r.get("Ano", "")
        )

        responsavel_exec = nome_planilha(r.get("ResponsavelExecucao", ""))
        responsavel_rev = nome_planilha(r.get("ResponsavelRevisao", ""))

        observacao = r.get("Observação", r.get("Categoria", ""))
        if nome_planilha(observacao) == "UP":
            observacao = ""

        linhas.append([
            link_data if i == 0 else "",
            r.get("Tipo", ""),
            numero_link,
            r.get("Ano", ""),
            responsavel_exec,
            responsavel_rev,
            observacao
        ])
    return linhas


def montar_linhas_requerimentos(
    data_str: str,
    df: pd.DataFrame,
    url_diario: str = "",
    preencher_vazio_com_traco: bool = False
) -> list[list]:
    link_data = montar_link_data(data_str, url_diario)

    if df is None or df.empty:
        if preencher_vazio_com_traco:
            return [[link_data, "-", "-", "-", "-", "-", "-"]]
        return [[link_data, "", "", "", "", "", ""]]

    df = df.fillna("")
    linhas = []

    for i, (_, r) in enumerate(df.iterrows()):
        numero_link = montar_link_numero_proposicao(
            tipo=r.get("Tipo", ""),
            numero=r.get("Número", ""),
            ano=r.get("Ano", "")
        )

        responsavel_exec = nome_planilha(r.get("ResponsavelExecucao", ""))
        responsavel_rev = nome_planilha(r.get("ResponsavelRevisao", ""))

        linhas.append([
            link_data if i == 0 else "",
            r.get("Tipo", ""),
            numero_link,
            r.get("Ano", ""),
            responsavel_exec,
            responsavel_rev,
            r.get("Observação", r.get("Classificação", ""))
        ])

    return linhas


def montar_linhas_pareceres(
    data_str: str,
    df: pd.DataFrame,
    url_diario: str = "",
    preencher_vazio_com_traco: bool = False
) -> list[list]:
    link_data = montar_link_data(data_str, url_diario)

    if df is None or df.empty:
        if preencher_vazio_com_traco:
            return [[link_data, "-", "-", "-", "-", "-", "-", "-"]]
        return [[link_data, "", "", "", "", "", "", ""]]

    df = df.fillna("")
    linhas = []

    for i, (_, r) in enumerate(df.iterrows()):
        numero_link = montar_link_numero_proposicao(
            tipo=r.get("Tipo", ""),
            numero=r.get("Número", ""),
            ano=r.get("Ano", "")
        )

        responsavel_exec = nome_planilha(r.get("ResponsavelExecucao", ""))
        responsavel_rev = nome_planilha(r.get("ResponsavelRevisao", ""))

        linhas.append([
            link_data if i == 0 else "",
            r.get("Tipo", ""),
            numero_link,
            r.get("Ano", ""),
            r.get("Subtipo", ""),
            responsavel_exec,
            responsavel_rev,
            r.get("Observação", "")
        ])
    return linhas

def distribuir_em_blocos(qtd: int, pessoas: list[str]) -> list[str]:
    pessoas = [nome_planilha(p) for p in pessoas if str(p).strip()]
    if qtd <= 0:
        return []
    if not pessoas:
        return [""] * qtd

    base, resto = divmod(qtd, len(pessoas))
    resultado = []

    for i, pessoa in enumerate(pessoas):
        repetir = base + (1 if i < resto else 0)
        resultado.extend([pessoa] * repetir)

    return resultado


def atribuir_responsaveis_normas(
    df: pd.DataFrame,
    indisponiveis: set[str] | None = None,
    replicar_em_linhas_continuacao: bool = True,
) -> pd.DataFrame:
    df = inicializar_df_responsaveis(df)
    if df.empty:
        return df

    cand_exec_dne = candidatos_para_tarefa("implantacao_normas_dne", indisponiveis)
    cand_exec_nao_dne = candidatos_para_tarefa("implantacao_normas_nao_dne", indisponiveis)
    cand_rev = candidatos_para_tarefa("revisao_normas", indisponiveis)

    mascara_cont = []
    pos_principais = []
    pos_dne = []
    pos_nao_dne = []

    for pos, (_, r) in enumerate(df.iterrows()):
        cont = replicar_em_linhas_continuacao and linha_continuacao_norma(r)
        mascara_cont.append(cont)

        if cont:
            continue

        pos_principais.append(pos)

        if eh_norma_dne(r):
            pos_dne.append(pos)
        else:
            pos_nao_dne.append(pos)

    mapa_exec_dne = distribuir_para_posicoes(len(df), pos_dne, cand_exec_dne)
    mapa_exec_nao_dne = distribuir_para_posicoes(len(df), pos_nao_dne, cand_exec_nao_dne)

    # 1) monta primeiro a execução das normas principais
    execucoes_principais = []
    for pos in pos_principais:
        executor = mapa_exec_dne.get(pos, mapa_exec_nao_dne.get(pos, ""))
        execucoes_principais.append(nome_planilha(executor))

    # 2) distribui revisores impedindo executor == revisor
    revisoes_principais = distribuir_revisores_sem_mesma_pessoa(
        execucoes_principais,
        cand_rev
    )

    mapa_rev = {
        pos: revisor
        for pos, revisor in zip(pos_principais, revisoes_principais)
    }

    execucoes = []
    revisoes = []
    ultimo_exec = ""
    ultimo_rev = ""

    for pos, cont in enumerate(mascara_cont):
        if cont:
            execucoes.append(ultimo_exec)
            revisoes.append(ultimo_rev)
            continue

        ultimo_exec = mapa_exec_dne.get(pos, mapa_exec_nao_dne.get(pos, ""))
        ultimo_rev = mapa_rev.get(pos, "")

        execucoes.append(ultimo_exec)
        revisoes.append(ultimo_rev)

    df["ResponsavelExecucao"] = execucoes
    df["ResponsavelRevisao"] = revisoes
    return df


def atribuir_responsaveis_proposicoes(
    df: pd.DataFrame,
    indisponiveis: set[str] | None = None,
) -> pd.DataFrame:
    df = inicializar_df_responsaveis(df)
    if df.empty:
        return df

    cand_exec_up = candidatos_para_tarefa("execucao_proposicoes_up", indisponiveis)
    cand_exec_nao_up = candidatos_para_tarefa("execucao_proposicoes_nao_up", indisponiveis)
    cand_rev = candidatos_para_tarefa("revisao_proposicoes", indisponiveis)

    pos_up = []
    pos_nao_up = []

    for pos, (_, r) in enumerate(df.iterrows()):
        if eh_proposicao_up(r):
            pos_up.append(pos)
        else:
            pos_nao_up.append(pos)

    mapa_exec_up = distribuir_para_posicoes(len(df), pos_up, cand_exec_up)
    mapa_exec_nao_up = distribuir_para_posicoes(len(df), pos_nao_up, cand_exec_nao_up)

    execucoes = []
    for pos in range(len(df)):
        execucoes.append(mapa_exec_up.get(pos, mapa_exec_nao_up.get(pos, "")))

    revisoes = distribuir_revisores_sem_mesma_pessoa(execucoes, cand_rev)

    df["ResponsavelExecucao"] = execucoes
    df["ResponsavelRevisao"] = revisoes
    return df


def atribuir_responsaveis_requerimentos(
    df: pd.DataFrame,
    indisponiveis: set[str] | None = None,
) -> pd.DataFrame:
    df = inicializar_df_responsaveis(df)
    if df.empty:
        return df

    cand_exec = candidatos_para_tarefa("execucao_requerimentos", indisponiveis)
    cand_rev = candidatos_para_tarefa("revisao_requerimentos", indisponiveis)

    execucoes = [""] * len(df)
    revisoes = [""] * len(df)

    pos_sem_tratamento = []
    pos_normais = []

    for pos, (_, r) in enumerate(df.iterrows()):
        if requerimento_sem_tratamento(r):
            pos_sem_tratamento.append(pos)
        else:
            pos_normais.append(pos)

    for pos in pos_sem_tratamento:
        execucoes[pos] = "-"
        revisoes[pos] = "-"

    mapa_exec = distribuir_para_posicoes(len(df), pos_normais, cand_exec)

    posicoes_com_revisao = []
    execucoes_com_revisao = []

    for pos in pos_normais:
        executor = nome_planilha(mapa_exec.get(pos, ""))
        execucoes[pos] = executor

        if pessoa_pertence_ao_grupo(executor, "BIBLIOTECARIO"):
            revisoes[pos] = "-"
        else:
            posicoes_com_revisao.append(pos)
            execucoes_com_revisao.append(executor)

    revisores_distribuidos = distribuir_revisores_sem_mesma_pessoa(execucoes_com_revisao, cand_rev)

    for pos, revisor in zip(posicoes_com_revisao, revisores_distribuidos):
        revisoes[pos] = revisor

    df["ResponsavelExecucao"] = execucoes
    df["ResponsavelRevisao"] = revisoes
    return df


def atribuir_responsaveis_pareceres(
    df: pd.DataFrame,
    indisponiveis: set[str] | None = None,
) -> pd.DataFrame:
    df = inicializar_df_responsaveis(df)
    if df.empty:
        return df

    cand_exec = candidatos_para_tarefa("execucao_pareceres", indisponiveis)
    cand_rev = candidatos_para_tarefa("revisao_pareceres", indisponiveis)

    execucoes = distribuir_em_blocos(len(df), cand_exec)
    revisoes = distribuir_revisores_sem_mesma_pessoa(execucoes, cand_rev)

    df["ResponsavelExecucao"] = execucoes
    df["ResponsavelRevisao"] = revisoes
    return df

def preencher_aba_modelo(
    ws,
    data_str: str,
    urls: dict,
    df_exec: pd.DataFrame,
    df_adm: pd.DataFrame,
    df_leg_normas: pd.DataFrame,
    df_props: pd.DataFrame,
    df_reqs: pd.DataFrame,
    df_pareceres: pd.DataFrame
):
    # ================= PROPOSIÇÕES =================
    linha_props = encontrar_linha(ws, "PROPOSIÇÕES", 1) + 1
    linhas_props = montar_linhas_proposicoes(
        data_str, df_props, urls["legislativo"], preencher_vazio_com_traco=True
    )
    desmesclar_intervalo(ws, linha_props, len(linhas_props), 7, 15)
    escrever_bloco(ws, linha_props, linhas_props, mesclar_coluna_a=True)
    mesclar_linhas_intervalo(ws, linha_props, len(linhas_props), 7, 15)
    aplicar_cor_responsaveis(ws, linha_props, linhas_props, colunas=(5, 6))

    # ================= REQUERIMENTOS =================
    linha_reqs = encontrar_linha(ws, "REQUERIMENTOS", 1) + 1
    linhas_reqs = montar_linhas_requerimentos(
        data_str, df_reqs, urls["legislativo"], preencher_vazio_com_traco=True
    )
    desmesclar_intervalo(ws, linha_reqs, len(linhas_reqs), 7, 15)
    escrever_bloco(ws, linha_reqs, linhas_reqs, mesclar_coluna_a=True)
    mesclar_linhas_intervalo(ws, linha_reqs, len(linhas_reqs), 7, 15)
    aplicar_cor_responsaveis(ws, linha_reqs, linhas_reqs, colunas=(5, 6))

    # ================= PARECERES =================
    linha_pareceres = encontrar_linha(ws, "PARECERES", 1) + 1
    linhas_pareceres = montar_linhas_pareceres(
        data_str, df_pareceres, urls["legislativo"], preencher_vazio_com_traco=True
    )
    desmesclar_intervalo(ws, linha_pareceres, len(linhas_pareceres), 8, 15)
    escrever_bloco(ws, linha_pareceres, linhas_pareceres, mesclar_coluna_a=True)
    mesclar_linhas_intervalo(ws, linha_pareceres, len(linhas_pareceres), 8, 15)
    aplicar_cor_responsaveis(ws, linha_pareceres, linhas_pareceres, colunas=(6, 7))

    # ================= NORMAS - LEGISLATIVO =================
    linha_leg = encontrar_linha(ws, "DIÁRIO DO LEGISLATIVO", 1) + 1
    linhas_leg = montar_linhas_normas(
        data_str,
        df_leg_normas,
        urls["legislativo"],
        preencher_vazio_com_traco=True
    )
    escrever_bloco(ws, linha_leg, linhas_leg, mesclar_coluna_a=True)
    aplicar_cor_responsaveis(ws, linha_leg, linhas_leg, colunas=(7, 8, 12, 13, 14, 15))

    # ================= NORMAS - ADMINISTRATIVO =================
    linha_adm = encontrar_linha(ws, "DIÁRIO ADMINISTRATIVO", 1) + 1
    linhas_adm = montar_linhas_normas(
        data_str, df_adm, urls["administrativo"], preencher_vazio_com_traco=True
    )
    escrever_bloco(ws, linha_adm, linhas_adm, mesclar_coluna_a=True)
    aplicar_cor_responsaveis(ws, linha_adm, linhas_adm, colunas=(7, 8, 12, 13, 14, 15))

    # ================= NORMAS - DIÁRIO DA JUSTIÇA =================
    linha_dj = encontrar_linha(ws, "DIÁRIO DA JUSTIÇA", 1) + 1
    linhas_dj = montar_linhas_normas(data_str, pd.DataFrame(), "")
    escrever_bloco(ws, linha_dj, linhas_dj, mesclar_coluna_a=True)
    aplicar_cor_responsaveis(ws, linha_dj, linhas_dj, colunas=(7, 8, 12, 13, 14, 15))

    # ================= NORMAS - EXECUTIVO =================
    linha_exec = encontrar_linha(ws, "DIÁRIO DO EXECUTIVO", 1) + 1
    linhas_exec = montar_linhas_normas(data_str, df_exec, urls["executivo_html"])
    escrever_bloco(ws, linha_exec, linhas_exec, mesclar_coluna_a=True)
    aplicar_cor_responsaveis(ws, linha_exec, linhas_exec, colunas=(7, 8, 12, 13, 14, 15))

    # ================= TOTAIS =================
    total_1 = encontrar_linha_safe(ws, "TOTAL", 1)
    total_2 = encontrar_linha_safe(ws, "TOTAL", 2)
    total_3 = encontrar_linha_safe(ws, "TOTAL", 3)
    total_4 = encontrar_linha_safe(ws, "TOTAL", 4)
    total_5 = encontrar_linha_safe(ws, "TOTAL", 5)

    total_normas = (
        contar_normas_principais(df_exec) +
        contar_normas_principais(df_adm) +
        contar_normas_principais(df_leg_normas)
    )

    qtd_exec, vides_exec = somar_quantidade_vides(df_exec)
    qtd_adm, vides_adm = somar_quantidade_vides(df_adm)
    qtd_leg, vides_leg = somar_quantidade_vides(df_leg_normas)

    total_quantidade = qtd_exec + qtd_adm + qtd_leg
    total_vides = vides_exec + vides_adm + vides_leg

    if total_1:
        escrever_celula(ws, f"F{total_1}", total_normas)
        escrever_celula(ws, f"I{total_1}", total_quantidade)
        escrever_celula(ws, f"K{total_1}", total_vides)

    if total_2:
        escrever_celula(ws, f"C{total_2}", len(df_props))

    if total_3:
        escrever_celula(ws, f"C{total_3}", len(df_reqs))

    if total_4:
        escrever_celula(ws, f"C{total_4}", len(df_pareceres))

    if total_5:
        escrever_celula(ws, f"C{total_5}", 0)


# =========================
# CLASS LegislativeProcessor
# =========================
class LegislativeProcessor:
    def __init__(self, pdf_bytes: bytes):
        self.pdf_bytes = pdf_bytes

        reader = pypdf.PdfReader(io.BytesIO(self.pdf_bytes))
        page_texts = []
        for page in reader.pages:
            pt = page.extract_text() or ""
            pt = re.sub(r"[ \t]+", " ", pt)
            page_texts.append(pt)

        self._offsets = []
        parts = []
        cursor = 0

        for idx, pt in enumerate(page_texts, start=1):
            chunk = pt + "\n"
            start = cursor
            end = cursor + len(chunk)
            self._offsets.append((start, end, idx))
            parts.append(chunk)
            cursor = end

        self.text = "".join(parts)

    def _pagina_from_pos(self, pos: int) -> str:
        for start, end, pnum in self._offsets:
            if start <= pos < end:
                return str(pnum)
        return ""

    def process_normas(self) -> pd.DataFrame:
        pattern = re.compile(
            r"^(LEI COMPLEMENTAR|LEI|RESOLUÇÃO|EMENDA À CONSTITUIÇÃO|DELIBERAÇÃO DA MESA)\s+N[º°]?\s*(\d{1,5}(?:\.\d{0,3})?)(?:/(\d{4}))?(?:,\s*DE .+? DE (\d{4}))?$",
            re.MULTILINE | re.IGNORECASE
        )

        data_na_epigrafe_regex = re.compile(
            r"\bDE\s+(\d{1,2})\s+DE\s+([A-ZÇÃÁÉÍÓÔÚ]+)\s+DE\s+(\d{4})\b",
            re.IGNORECASE
        )

        meses_leg = {
            "JANEIRO": "01",
            "FEVEREIRO": "02",
            "MARÇO": "03",
            "MARCO": "03",
            "ABRIL": "04",
            "MAIO": "05",
            "JUNHO": "06",
            "JULHO": "07",
            "AGOSTO": "08",
            "SETEMBRO": "09",
            "OUTUBRO": "10",
            "NOVEMBRO": "11",
            "DEZEMBRO": "12"
        }

        comandos_regex = re.compile(
            r"(Ficam\s+revogados|Fica\s+revogado|"
            r"Fica\s+acrescentado|Ficam\s+acrescentados|"
            r"Fica\s+alterado|Ficam\s+alterados|"
            r"Altera|Alteram|"
            r"Revoga|Revogam|"
            r"Dá\s+nova\s+redação|Dão\s+nova\s+redação|"
            r"Passa\s+a\s+vigorar|Passam\s+a\s+vigorar)",
            re.IGNORECASE
        )

        norma_alterada_regex = re.compile(
            r"(LEI COMPLEMENTAR|LEI|RESOLUÇÃO|EMENDA À CONSTITUIÇÃO|DELIBERAÇÃO DA MESA)\s+"
            r"N[º°]?\s*(\d{1,5}(?:\.\d{0,3})?)"
            r"(?:\s*/\s*(\d{4}))?"
            r"(?:,\s*de\s*.*?(\d{4}))?",
            re.IGNORECASE
        )

        # NOVO: fecho real da norma
        fecho_norma_regex = re.compile(
            r"Palácio\s+da\s+Inconfidência.*?Independência\s+do\s+Brasil\.?",
            re.IGNORECASE | re.DOTALL
        )

        normas_encontradas = []
        for match in pattern.finditer(self.text):
            tipo_extenso = match.group(1).upper().strip()
            numero_raw = match.group(2).replace(".", "")
            ano = match.group(3) if match.group(3) else match.group(4)
            if not ano:
                continue

            pagina = self._pagina_from_pos(match.start())
            coluna = 1

            sancao = ""
            linha_epigrafe = match.group(0) or ""
            dm = data_na_epigrafe_regex.search(linha_epigrafe)
            if dm:
                dia = (dm.group(1) or "").zfill(2)
                mes_nome = (dm.group(2) or "").upper().strip()
                mes = meses_leg.get(mes_nome, "")
                ano_data = (dm.group(3) or "").strip()
                if mes:
                    sancao = f"{dia}/{mes}/{ano_data}"

            sigla = TIPO_MAP_NORMA[tipo_extenso]

            normas_encontradas.append({
                "start": match.start(),
                "end": match.end(),
                "Página": pagina,
                "Coluna": coluna,
                "Sanção": sancao,
                "Sigla": sigla,
                "Número": numero_raw,
                "Ano": ano
            })

        resultados = []

        for i, norma in enumerate(normas_encontradas):
            start_bloco = norma["end"]
            end_bloco = normas_encontradas[i + 1]["start"] if i + 1 < len(normas_encontradas) else len(self.text)
            bloco = self.text[start_bloco:end_bloco]

            # NOVO: corta no fecho da lei
            m_fecho = fecho_norma_regex.search(bloco)
            if m_fecho:
                bloco = bloco[:m_fecho.end()]

            linha = {
                "Página": norma["Página"],
                "Coluna": norma["Coluna"],
                "Sanção": norma["Sanção"],
                "Sigla": norma["Sigla"],
                "Número": norma["Número"],
                "Ano": norma["Ano"],
                "Alterações": "",
            }
    
            resultados.append(linha)

            seen_alteracoes = set()

            def add_alteracao(chave: str):
                if not chave or chave in seen_alteracoes:
                    return
                seen_alteracoes.add(chave)

                if linha["Alterações"] == "":
                    linha["Alterações"] = chave
                else:
                    resultados.append({
                        "Página": "",
                        "Coluna": "",
                        "Sanção": "",
                        "Sigla": "",
                        "Número": "",
                        "Ano": "",
                        "Alterações": chave
                    })

            if linha["Sigla"] == "EMC":
                add_alteracao("CON 1989 1989")

            eventos = []
            for c in comandos_regex.finditer(bloco):
                eventos.append(("command", c.start(), c))

            eventos.sort(key=lambda e: e[1])

            for ev in eventos:
                tipo_ev, pos_ev, match_obj = ev
                command_text = match_obj.group(0).lower()

                if tipo_ev != "command":
                    continue

                raio = 300
                start_block = max(0, pos_ev - raio)
                end_block = min(len(bloco), pos_ev + raio)
                bloco_janela = bloco[start_block:end_block]

                alteracoes_para_processar = []

                if "revoga" in command_text or "revogado" in command_text:
                    trecho = bloco[pos_ev:]

                    deslocamento_inicio_busca = match_obj.end() - pos_ev

                    # corta no próximo artigo, para não invadir Art. 19, Art. 20 etc.
                    m_fim = re.search(
                        r"\n\s*Art\.\s*\d+º?\s*[–—-]",
                        trecho[deslocamento_inicio_busca:],
                        re.IGNORECASE
                    )

                    if m_fim:
                        fim = deslocamento_inicio_busca + m_fim.start()
                        bloco_revogacao = trecho[:fim]
                    else:
                        bloco_revogacao = trecho[:1500]

                    alteracoes_para_processar = list(
                        norma_alterada_regex.finditer(bloco_revogacao)
                    )

                else:
                    alteracoes_candidatas = list(
                        norma_alterada_regex.finditer(bloco_janela)
                    )

                    if alteracoes_candidatas:
                        pos_comando_no_bloco = pos_ev - start_block
                        melhor_candidato = min(
                            alteracoes_candidatas,
                            key=lambda m: abs(m.start() - pos_comando_no_bloco)
                        )
                        alteracoes_para_processar = [melhor_candidato]

                for alt in alteracoes_para_processar:
                    tipo_alt_extenso = alt.group(1).upper().strip()
                    num_alt = alt.group(2).replace(".", "")
                    ano_alt = alt.group(3) or alt.group(4) or ""

                    sigla_alt = TIPO_MAP_NORMA.get(tipo_alt_extenso, tipo_alt_extenso)

                    if (
                        sigla_alt == linha["Sigla"]
                        and num_alt == linha["Número"]
                        and ((not ano_alt) or ano_alt == linha["Ano"])
                    ):
                        continue

                    chave = f"{sigla_alt} {num_alt}"
                    if ano_alt:
                        chave += f" {ano_alt}"

                    if chave in seen_alteracoes:
                        continue

                    add_alteracao(chave)

        return pd.DataFrame(
            resultados,
            columns=["Página", "Coluna", "Sanção", "Sigla", "Número", "Ano", "Alterações", "Observação"]
        )

    def process_proposicoes(self) -> pd.DataFrame:
        pattern_prop = re.compile(
            r"^\s*(?:- )?\s*(PROJETO DE LEI COMPLEMENTAR|PROJETO DE LEI|INDICAÇÃO|PROJETO DE RESOLUÇÃO|PROPOSTA DE EMENDA À CONSTITUIÇÃO|MENSAGEM|VETO) Nº (\d{1,4}\.?\d{0,3}/\d{4})",
            re.MULTILINE
        )
        pattern_utilidade = re.compile(r"Declara de utilidade pública", re.IGNORECASE | re.DOTALL)
        ignore_redacao_final = re.compile(r"opinamos por se dar à proposição a seguinte redação final", re.IGNORECASE)
        ignore_publicada_antes = re.compile(r"foi publicad[ao] na edição anterior\.", re.IGNORECASE)
        ignore_em_epigrafe = re.compile(r"Na publicação da matéria em epígrafe", re.IGNORECASE)

        proposicoes = []
        for match in pattern_prop.finditer(self.text):
            start_idx = match.start()
            end_idx = match.end()
            contexto_antes = self.text[max(0, start_idx - 200):start_idx]
            contexto_depois = self.text[end_idx:end_idx + 250]

            if ignore_em_epigrafe.search(contexto_depois):
                continue
            if ignore_redacao_final.search(contexto_antes) or ignore_publicada_antes.search(contexto_depois):
                continue

            subseq_text = self.text[end_idx:end_idx + 250]
            if "(Redação do Vencido)" in subseq_text:
                continue

            tipo_extenso = match.group(1)
            numero_ano = match.group(2).replace(".", "")
            numero, ano = numero_ano.split("/")
            sigla = TIPO_MAP_PROP[tipo_extenso]
            categoria = "UP" if pattern_utilidade.search(subseq_text) else ""
            proposicoes.append([sigla, numero, ano, categoria])

        return pd.DataFrame(proposicoes, columns=["Sigla", "Número", "Ano", "Categoria"])

    def process_requerimentos(self) -> pd.DataFrame:
        requerimentos = []

        ignore_officio_pattern = re.compile(
            r"Ofício[\s\S]{0,200}?Requerimento\s*n[ºo]?\s*(\d{1,5}(?:\.\d{0,3})?)/(\d{4})",
            re.IGNORECASE
        )

        ignore_anexese_pattern = re.compile(
            r"Anexe-se\s+ao\s+Requerimento\s*n[ºo]?\s*(\d{1,5}(?:\.\d{0,3})?)/(\d{4})",
            re.IGNORECASE
        )

        ignore_relativas_pattern = re.compile(
            r"(?:relativa[s]?|referente[s]?|informações\s+relativas\s+ao)"
            r"[\s\S]{0,80}?Requerimento\s*n[ºo]?\s*(\d{1,5}(?:\.\d{0,3})?)/(\d{4})",
            re.IGNORECASE
        )

        reqs_to_ignore = set()

        for match in ignore_officio_pattern.finditer(self.text):
            num_part = match.group(1).replace(".", "")
            ano = match.group(2)
            reqs_to_ignore.add(f"{num_part}/{ano}")

        for match in ignore_anexese_pattern.finditer(self.text):
            num_part = match.group(1).replace(".", "")
            ano = match.group(2)
            reqs_to_ignore.add(f"{num_part}/{ano}")

        for match in ignore_relativas_pattern.finditer(self.text):
            num_part = match.group(1).replace(".", "")
            ano = match.group(2)
            reqs_to_ignore.add(f"{num_part}/{ano}")

        ignore_pattern = re.compile(
            r"Ofício nº .*?,.*?relativas ao Requerimento\s*nº (\d{1,4}\.?\d{0,3}/\d{4})",
            re.IGNORECASE | re.DOTALL
        )
        aprovado_pattern = re.compile(
            r"(da Comissão.*?, informando que, na.*?foi aprovado o Requerimento\s*nº (\d{1,5}(?:\.\d{0,3})?)/(\d{4}))",
            re.IGNORECASE | re.DOTALL
        )

        for match in ignore_pattern.finditer(self.text):
            numero_ano = match.group(1).replace(".", "")
            reqs_to_ignore.add(numero_ano)

        for match in aprovado_pattern.finditer(self.text):
            num_part = match.group(2).replace(".", "")
            ano = match.group(3)
            numero_ano = f"{num_part}/{ano}"
            reqs_to_ignore.add(numero_ano)

        req_recebimento_pattern = re.compile(
            r"RECEBIMENTO DE PROPOSIÇÃO[\s\S]*?REQUERIMENTO Nº (\d{1,5}(?:\.\d{0,3})?)/(\d{4})",
            re.IGNORECASE | re.DOTALL
        )
        for match in req_recebimento_pattern.finditer(self.text):
            trecho_match = match.group(0)

            if re.search(r"PARECER\s+SOBRE\s+O\s+REQUERIMENTO", trecho_match, re.IGNORECASE):
                continue

            num_part = match.group(1).replace(".", "")
            ano = match.group(2)
            numero_ano = f"{num_part}/{ano}"
            if numero_ano not in reqs_to_ignore:
                requerimentos.append(["RQN", num_part, ano, "", "", "Recebido"])

        rqc_pattern_aprovado = re.compile(
            r"É\s+recebido\s+pela\s+presidência,\s+submetido\s+a\s+votação\s+e\s+aprovado\s+o\s+Requerimento(?:s)?(?: nº| Nº| n\u00ba| n\u00b0)?\s*(\d{1,5}(?:\.\d{0,3})?)/\s*(\d{4})",
            re.IGNORECASE
        )
        for match in rqc_pattern_aprovado.finditer(self.text):
            num_part = match.group(1).replace(".", "")
            ano = match.group(2)
            numero_ano = f"{num_part}/{ano}"
            if numero_ano not in reqs_to_ignore:
                requerimentos.append(["RQC", num_part, ano, "", "", "Aprovado"])

        rqc_recebido_apreciacao_pattern = re.compile(
            r"É recebido pela\s+presidência, para posterior apreciação, o Requerimento(?: nº| Nº)?\s*(\d{1,5}(?:\.\d{0,3})?)/(\d{4})",
            re.IGNORECASE | re.DOTALL
        )
        for match in rqc_recebido_apreciacao_pattern.finditer(self.text):
            num_part = match.group(1).replace(".", "")
            ano = match.group(2)
            numero_ano = f"{num_part}/{ano}"
            if numero_ano not in reqs_to_ignore:
                requerimentos.append(["RQC", num_part, ano, "", "", "Recebido para apreciação"])

        rqc_prejudicado_pattern = re.compile(
            r"(?:é|foi|fica|considera(?:-se)?)(?:[\s\S]{0,80}?)prejudicado\s+o\s+Requerimento(?: nº| Nº| n\u00ba| n\u00b0)?\s*(\d{1,5}(?:\.\d{0,3})?)/\s*(\d{4})",
            re.IGNORECASE
        )
        for match in rqc_prejudicado_pattern.finditer(self.text):
            num_part = match.group(1).replace(".", "")
            ano = match.group(2)
            numero_ano = f"{num_part}/{ano}"
            if numero_ano not in reqs_to_ignore:
                requerimentos.append(["RQC", num_part, ano, "", "", "Prejudicado"])

        rqc_rejeitado_pattern = re.compile(
            r"É\s+recebido\s+pela\s+presidência,\s+submetido\s+a\s+votação\s+e\s+rejeitado\s+o\s+Requerimento(?:s)?(?: nº| Nº| n\u00ba| n\u00b0)?\s*(\d{1,5}(?:\.\d{0,3})?)/\s*(\d{4})",
            re.IGNORECASE | re.DOTALL
        )
        for match in rqc_rejeitado_pattern.finditer(self.text):
            num_part = match.group(1).replace(".", "")
            ano = match.group(2)
            numero_ano = f"{num_part}/{ano}"
            if numero_ano not in reqs_to_ignore:
                requerimentos.append(["RQC", num_part, ano, "", "", "Rejeitado"])

        def bloco_parece_requerimento_real(block: str) -> bool:
            b = re.sub(r"\s+", " ", block).strip().lower()

            indicadores = [
                "em que requer",
                "requer seja",
                "requerem seja",
                "que seja formulado voto de congratulações",
                "manifestação de pesar",
                "manifestação de repúdio",
                "moção de aplauso",
                "manifestação de apoio",
            ]

            return any(ind in b for ind in indicadores)


        def fecha_parentese_logo_depois(texto: str, start_idx: int, lookahead: int = 80) -> bool:
            depois = texto[start_idx:start_idx + lookahead]
            return ")" in depois


        rqn_pattern = re.compile(r"^(?:\s*)(Nº)\s+(\d{2}\.?\d{3}/\d{4})\s*,\s*d+(?:as|os|a|o)\b", re.MULTILINE)
        rqc_old_pattern = re.compile(r"^(?:\s*)(nº)\s+(\d{2}\.?\d{3}/\d{4})\s*,\s*d+(?:as|os|a|o)\b", re.MULTILINE)

        for pattern, sigla_prefix in [(rqn_pattern, "RQN"), (rqc_old_pattern, "RQC")]:
            for match in pattern.finditer(self.text):
                start_idx = match.start()

                # ignora citações do tipo:
                # nº 16.969/2026, da Comissão dos Direitos da Mulher).
                if fecha_parentese_logo_depois(self.text, start_idx, lookahead=80):
                    continue

                next_match = re.search(
                    r"^(?:\s*)(Nº|nº)\s+(\d{2}\.?\d{3}/\d{4})",
                    self.text[start_idx + 1:],
                    flags=re.MULTILINE
                )
                end_idx = (next_match.start() + start_idx + 1) if next_match else len(self.text)
                block = self.text[start_idx:end_idx].strip()
                nums_in_block = re.findall(r"\d{2}\.?\d{3}/\d{4}", block)
                if not nums_in_block:
                    continue

                num_part, ano = nums_in_block[0].replace(".", "").split("/")
                numero_ano = f"{num_part}/{ano}"

                # se o número estiver em ignore, mas o bloco for claramente um requerimento real,
                # ele deve ser mantido
                if numero_ano in reqs_to_ignore and not bloco_parece_requerimento_real(block):
                    continue

                classif = classify_req(block)
                requerimentos.append([sigla_prefix, num_part, ano, "", "", classif])

        nao_recebidas_header_pattern = re.compile(r"PROPOSIÇÕES\s*NÃO\s*RECEBIDAS", re.IGNORECASE)
        header_match = nao_recebidas_header_pattern.search(self.text)
        if header_match:
            start_idx = header_match.end()
            next_section_pattern = re.compile(r"^\s*(\*?)\s*.*\s*(\*?)\s*$", re.MULTILINE)
            next_section_match = next_section_pattern.search(self.text, start_idx)
            end_idx = next_section_match.start() if next_section_match else len(self.text)
            nao_recebidos_block = self.text[start_idx:end_idx]
            rqn_nao_recebido_pattern = re.compile(r"REQUERIMENTO Nº (\d{2}\.?\d{3}/\d{4})", re.IGNORECASE)

            for match in rqn_nao_recebido_pattern.finditer(nao_recebidos_block):
                numero_ano = match.group(1).replace(".", "")
                num_part, ano = numero_ano.split("/")
                if numero_ano not in reqs_to_ignore:
                    requerimentos.append(["RQN", num_part, ano, "", "", "NÃO RECEBIDO"])

        prioridade = {
            "Voto de congratulações": 100,
            "Manifestação de pesar": 90,
            "Manifestação de repúdio": 90,
            "Moção de aplauso": 90,
            "Manifestação de apoio": 90,
            "Aprovado": 50,
            "Recebido para apreciação": 50,
            "Recebido": 50,
            "Prejudicado": 50,
            "Rejeitado": 50,
            "NÃO RECEBIDO": 55,
            "": 0,
        }

        melhor_por_key = {}

        for r in requerimentos:
            key = (r[0], r[1], r[2])
            atual = melhor_por_key.get(key)

            if atual is None:
                melhor_por_key[key] = r
            else:
                classif_nova = r[5] if len(r) > 5 else ""
                classif_atual = atual[5] if len(atual) > 5 else ""

                if prioridade.get(classif_nova, 0) > prioridade.get(classif_atual, 0):
                    melhor_por_key[key] = r

        unique_reqs = list(melhor_por_key.values())

        return pd.DataFrame(
            unique_reqs,
            columns=["Sigla", "Número", "Ano", "Coluna4", "Coluna5", "Classificação"]
        )

    def process_pareceres(self) -> pd.DataFrame:
        found_projects = {}
        pareceres_start_pattern = re.compile(r"TRAMITAÇÃO DE PROPOSIÇÕES")
        votacao_pattern = re.compile(
            r"(Votação do Requerimento[\s\S]*?)(?=Votação do Requerimento|Diário do Legislativo|Projetos de Lei Complementar|Diário do Legislativo - Poder Legislativo|$)",
            re.IGNORECASE
        )
        pareceres_start = pareceres_start_pattern.search(self.text)
        if not pareceres_start:
            return pd.DataFrame(columns=["Sigla", "Número", "Ano", "Tipo"])

        pareceres_text = self.text[pareceres_start.end():]
        clean_text = pareceres_text
        for match in votacao_pattern.finditer(pareceres_text):
            clean_text = clean_text.replace(match.group(0), "")

        ignore_edital_emenda_pattern = re.compile(
            r"e votar,\s*no\s*\d+º\s*turno,\s*o\s*Parecer\s*sobre\s+a\s*Emenda\s*n[º°o]?\s*\d+\s*ao\s*Projeto\s*de\s*Lei(?:\s*Complementar)?\s*n[º°o]?\s*\d{1,4}\.?\d{0,3}/\d{4}.*?e\s*de\s*receber,\s*discutir\s*e\s*votar\s*proposições\s*da\s*comissão",
            re.IGNORECASE | re.DOTALL
        )

        clean_text = ignore_edital_emenda_pattern.sub("", clean_text)

        emenda_projeto_lei_pattern = re.compile(
            r"EMENDAS AO PROJETO DE LEI Nº (\d{1,4}\.?\d{0,3})/(\d{4})",
            re.IGNORECASE | re.DOTALL
        )
        for match in emenda_projeto_lei_pattern.finditer(clean_text):
            numero_raw = match.group(1).replace(".", "")
            ano = match.group(2)
            project_key = ("PL", numero_raw, ano)
            if project_key not in found_projects:
                found_projects[project_key] = set()
            found_projects[project_key].add("EMENDA")

        emenda_completa_pattern = re.compile(
            r"EMENDA Nº (\d+)\s+AO\s+(?:SUBSTITUTIVO Nº \d+\s+AO\s+)?PROJETO DE LEI(?: COMPLEMENTAR)? Nº (\d{1,4}\.?\d{0,3})/(\d{4})",
            re.IGNORECASE
        )
        emenda_pattern = re.compile(r"^(?:\s*)EMENDA Nº (\d+)\s*", re.MULTILINE)
        substitutivo_pattern = re.compile(r"^(?:\s*)SUBSTITUTIVO Nº (\d+)\s*", re.MULTILINE)
        project_pattern = re.compile(
            r"Conclusão\s*([\s\S]*?)"
            r"(Projeto de Lei|PL|Projeto de Resolução|PRE|Proposta de Emenda à Constituição|PEC|Projeto de Lei Complementar|PLC|Requerimento)\s+"
            r"(?:n[º°o]|N[º°O])?\s*"
            r"(\d{1,4}(?:\.\d{1,3})?)\s*/\s*"
            r"(\d{2,4})",
            re.IGNORECASE | re.DOTALL
        )

        for match in emenda_completa_pattern.finditer(clean_text):
            numero = match.group(2).replace(".", "")
            ano = match.group(3)
            sigla = "PLC" if "COMPLEMENTAR" in match.group(0).upper() else "PL"
            project_key = (sigla, numero, ano)
            if project_key not in found_projects:
                found_projects[project_key] = set()
            found_projects[project_key].add("EMENDA")

        all_matches = sorted(
            list(emenda_pattern.finditer(clean_text)) + list(substitutivo_pattern.finditer(clean_text)),
            key=lambda x: x.start()
        )

        for title_match in all_matches:
            text_before_title = clean_text[:title_match.start()]
            last_project_match = None
            for match in project_pattern.finditer(text_before_title):
                last_project_match = match

            if last_project_match:
                sigla_raw = last_project_match.group(2)
                sigla = SIGLA_MAP_PARECER.get(sigla_raw.lower(), sigla_raw.upper())
                numero = last_project_match.group(3).replace(".", "")
                ano = last_project_match.group(4)

                if len(ano) == 2:
                    ano = f"20{ano}"

                project_key = (sigla, numero, ano)
                item_type = "EMENDA" if "EMENDA" in title_match.group(0).upper() else "SUBSTITUTIVO"
                if project_key not in found_projects:
                    found_projects[project_key] = set()
                found_projects[project_key].add(item_type)

        emenda_projeto_lei_pattern = re.compile(
            r"EMENDAS AO PROJETO DE LEI Nº (\d{1,4}\.?\d{0,3})/(\d{4})",
            re.IGNORECASE
        )
        for match in emenda_projeto_lei_pattern.finditer(clean_text):
            numero_raw = match.group(1).replace(".", "")
            ano = match.group(2)
            project_key = ("PL", numero_raw, ano)
            if project_key not in found_projects:
                found_projects[project_key] = set()
            found_projects[project_key].add("EMENDA")

        pareceres = []
        for (sigla, numero, ano), types in found_projects.items():
            type_str = "SUB/EMENDA" if len(types) > 1 else list(types)[0]
            pareceres.append([sigla, numero, ano, type_str])

        return pd.DataFrame(pareceres, columns=["Sigla", "Número", "Ano", "Tipo"])

    def process_all(self) -> dict:
        df_normas = self.process_normas()
        df_proposicoes = self.process_proposicoes()
        df_requerimentos = self.process_requerimentos()
        df_pareceres = self.process_pareceres()
        return {
            "Normas": df_normas,
            "Proposicoes": df_proposicoes,
            "Requerimentos": df_requerimentos,
            "Pareceres": df_pareceres
        }


# =========================
# CLASS AdministrativeProcessor
# =========================
class AdministrativeProcessor:
    def __init__(self, pdf_bytes: bytes):
        self.pdf_bytes = pdf_bytes

        self.meses = {
            "janeiro": "01",
            "fevereiro": "02",
            "março": "03",
            "marco": "03",
            "abril": "04",
            "maio": "05",
            "junho": "06",
            "julho": "07",
            "agosto": "08",
            "setembro": "09",
            "outubro": "10",
            "novembro": "11",
            "dezembro": "12"
        }

        self.norma_publicada_regex = re.compile(
            r'^(DELIBERAÇÃO DA MESA|'
            r'PORTARIA\s+(?:DGE|PSEC\s*/\s*DGE|PRES\s*/\s*DGE|PRES\s*/\s*PSEC)|'
            r'ORDEM DE SERVIÇO PRES/PSEC)\s+N[º°]\s+([\d\.]+)\s*/\s*(\d{4})\s*$',
            re.IGNORECASE | re.MULTILINE
        )

        self.revogacoes_caput_regex = re.compile(
            r'Ficam\s+revogados\s+os\s+seguintes\s+atos\s+normativos,'
            r'\s+sem\s+preju[ií]zo\s+dos\s+efeitos\s+por\s+eles\s+produzidos\s*:',
            re.IGNORECASE
        )

        self.revogacao_simples_regex = re.compile(r'\bFic(?:a|am)\s+revogad(?:a|o|as|os)\b', re.IGNORECASE)
        self.sem_efeito_regex = re.compile(r'\bFic(?:a|am)\s+sem\s+efeito\b|\bTorn(?:a|am)\s+sem\s+efeito\b', re.IGNORECASE)
        self.prorrogacao_regex = re.compile(r'\bFic(?:a|am)\s+prorrogad(?:a|o|as|os)\b', re.IGNORECASE)
        self.redacao_regex = re.compile(
            r'\bpassa\s+a\s+vigorar\b|\bpassam\s+a\s+vigorar\b|\bpassa\s+a\s+vigorar\s+com\s+a\s+seguinte\s+reda[cç][aã]o\b',
            re.IGNORECASE
        )

        dash = r'[–—-]'

        self.fim_lista_revogacoes_regex = re.compile(
            rf'\bArt\.\s*\d+º?\s*{dash}\s*|\bArtigo\s+\d+º?\s*{dash}\s*',
            re.IGNORECASE
        )

        self.norma_alterada_regex = re.compile(
            rf'\b('
            rf'DELIBERAÇÃO\s+DA\s+MESA|'
            rf'PORTARIA'
            rf'(?:'
                rf'\s+DA\s+PRESID[ÊE]NCIA\s+E\s+DA\s+DIRETORIA-GERAL'
                rf'|'
                rf'\s+DA\s+1ª-SECRETARIA\s*{dash}\s*PSEC\s*{dash}\s*E\s+DA\s+DIRETORIA-GERAL\s*{dash}\s*DGE\s*{dash}'
                rf'|'
                rf'\s+DA\s+DIRETORIA-GERAL(?:\s*{dash}\s*DGE\s*{dash})?'
                rf'|'
                rf'\s*PSEC\s*/\s*DGE'
                rf'|'
                rf'\s*PRES\s*/\s*DGE'
                rf'|'
                rf'\s*PRES\s*/\s*PSEC'
                rf'|'
                rf'\s*DGE'
            rf')?'
            rf'|'
            rf'ORDEM\s+DE\s+SERVI[ÇC]O\s+PRES/PSEC|'
            rf'ORDEM\s+DE\s+SERVI[ÇC]O\s+DA\s+PRESID[ÊE]NCIA\s+E\s+DA\s+1ª-SECRETARIA|'
            rf'ORDEM\s+DE\s+SERVI[ÇC]O'
            rf')\s*N[º°]\s*([\d\.]+)'
            rf'(?:\s*/\s*(\d{{4}}))?'
            rf'(?:\s*,\s*de\s*[^;\.]*?(\d{{4}}))?',
            re.IGNORECASE
        )

        self.fecho_palacio_regex = re.compile(
            r'Pal[aá]cio\s+da\s+Inconfid[eê]ncia\s*,\s*'
            r'(\d{1,2})\s+de\s+([A-Za-zçÇãÃáÁéÉíÍóÓôÔúÚ]+)\s+de\s+(\d{4})',
            re.IGNORECASE
        )
        self.fecho_sala_mesa_regex = re.compile(
            r'Sala\s+de\s+Reuni[õo]es\s+da\s+Mesa\s+da\s+Assembleia\s+Legislativa\s*,\s*'
            r'(\d{1,2})\s+de\s+([A-Za-zçÇãÃáÁéÉíÍóÓôÔúÚ]+)\s+de\s+(\d{4})',
            re.IGNORECASE
        )

        self.regex_dcs = re.compile(r'DECIS[ÃA]O DA 1ª-SECRETARIA', re.IGNORECASE)

    def _formatar_data_fecho(self, bloco: str) -> str:
        bloco = bloco or ""

        m = self.fecho_palacio_regex.search(bloco)
        if not m:
            m = self.fecho_sala_mesa_regex.search(bloco)
        if not m:
            return ""

        dia = m.group(1).zfill(2)
        mes_nome = (m.group(2) or "").strip().lower()
        ano = (m.group(3) or "").strip()
        mes = self.meses.get(mes_nome, "")
        if not mes:
            return ""
        return f"{dia}/{mes}/{ano}"

    def _normalizar_sigla(self, tipo_txt_upper: str) -> str:
        t = (tipo_txt_upper or "").upper()
        if "DELIBERAÇÃO DA MESA" in t:
            return "DLB"
        if "PORTARIA" in t:
            return "PRT"
        if "ORDEM DE SERVI" in t:
            return "OSV"
        return t.strip()

    def _sigla_norma_publicada(self, tipo_raw: str) -> str:
        t = (tipo_raw or "").upper().strip()
        t = re.sub(r'\s+', ' ', t)
        t = re.sub(r'\s*/\s*', '/', t)
        return {
            "DELIBERAÇÃO DA MESA": "DLB",
            "PORTARIA DGE": "PRT",
            "PORTARIA PSEC/DGE": "PRT",
            "PORTARIA PRES/DGE": "PRT",
            "PORTARIA PRES/PSEC": "PRT",
            "ORDEM DE SERVIÇO PRES/PSEC": "OSV",
        }.get(t, "")

    def process_pdf(self):
        try:
            reader = pypdf.PdfReader(io.BytesIO(self.pdf_bytes))
        except Exception as e:
            st.error(f"Erro ao abrir o arquivo PDF: {e}")
            return None

        page_texts = []
        for p in reader.pages:
            page_texts.append(p.extract_text() or "")

        offsets = []
        full_text_parts = []
        cursor = 0
        for idx, pt in enumerate(page_texts, start=1):
            full_text_parts.append(pt + "\n")
            cursor_end = cursor + len(pt) + 1
            offsets.append((cursor, cursor_end, idx))
            cursor = cursor_end

        full_text = "".join(full_text_parts)
        full_text = re.sub(r"[ \t]+", " ", full_text)
        full_text = re.sub(r"\n+", "\n", full_text)

        def _pagina_from_pos(pos: int):
            for start, end, pnum in offsets:
                if start <= pos < end:
                    return pnum
            return ""

        normas = []
        for m in self.norma_publicada_regex.finditer(full_text):
            pos = m.start()
            pagina = _pagina_from_pos(pos)

            tipo_raw = m.group(1)
            numero = (m.group(2) or "").replace(".", "").replace(" ", "")
            ano = (m.group(3) or "").strip()

            sigla = self._sigla_norma_publicada(tipo_raw)
            if sigla:
                normas.append({
                    "pos": pos,
                    "end": m.end(),
                    "pagina": pagina,
                    "coluna": 1,
                    "sigla": sigla,
                    "numero": numero,
                    "ano": ano
                })

        resultados = []

        for i, n in enumerate(normas):
            start = n["end"]
            end = normas[i + 1]["pos"] if i + 1 < len(normas) else len(full_text)
            bloco = full_text[start:end]

            linha = {
                "Página": n["pagina"],
                "Coluna": n["coluna"],
                "Sanção": self._formatar_data_fecho(bloco),
                "Sigla": n["sigla"],
                "Número": n["numero"],
                "Ano": n["ano"],
                "Alterações": ""
            }
            resultados.append(linha)

            seen_alteracoes = set()

            def _add_alt(chave: str):
                nonlocal resultados
                if chave in seen_alteracoes:
                    return
                seen_alteracoes.add(chave)

                if linha["Alterações"] == "":
                    linha["Alterações"] = chave
                else:
                    resultados.append({
                        "Página": "",
                        "Coluna": "",
                        "Sanção": "",
                        "Sigla": "",
                        "Número": "",
                        "Ano": "",
                        "Alterações": chave
                    })

            def _extrair_alteracoes(seg: str):
                for alt in self.norma_alterada_regex.finditer(seg or ""):
                    tipo_alt_raw = (alt.group(1) or "").upper().strip()
                    num_alt = (alt.group(2) or "").replace(".", "").replace(" ", "")
                    ano_alt = alt.group(3) or alt.group(4) or ""
                    sigla_alt = self._normalizar_sigla(tipo_alt_raw)

                    if sigla_alt == linha["Sigla"] and num_alt == linha["Número"]:
                        if (not ano_alt) or (ano_alt == linha["Ano"]):
                            continue

                    chave = f"{sigla_alt} {num_alt}" + (f" {ano_alt}" if ano_alt else "")
                    _add_alt(chave)

            cap = self.revogacoes_caput_regex.search(bloco)
            if cap:
                after = bloco[cap.end():]
                fim = None
                m_art = self.fim_lista_revogacoes_regex.search(after)
                if m_art:
                    fim = m_art.start()
                segmento = after[:fim] if fim is not None else after
                _extrair_alteracoes(segmento)

            for gat in (self.revogacao_simples_regex, self.sem_efeito_regex, self.prorrogacao_regex):
                for gm in gat.finditer(bloco):
                    janela = bloco[gm.start(): gm.start() + 1200]
                    _extrair_alteracoes(janela)

            for gm in self.redacao_regex.finditer(bloco):
                start_j = max(0, gm.start() - 600)
                end_j = min(len(bloco), gm.end() + 1200)
                janela = bloco[start_j:end_j]
                _extrair_alteracoes(janela)

        if self.regex_dcs.search(full_text):
            resultados.append({
                "Página": "",
                "Coluna": 1,
                "Sanção": "",
                "Sigla": "DCS",
                "Número": "",
                "Ano": "",
                "Alterações": ""
            })

        return pd.DataFrame(
            resultados,
            columns=["Página", "Coluna", "Sanção", "Sigla", "Número", "Ano", "Alterações"]
        )


# =========================
# CLASS ExecutiveProcessor
# =========================
class ExecutiveProcessor:
    def __init__(self, pdf_bytes: bytes):
        self.pdf_bytes = self._clean_pdf_bytes(pdf_bytes)

        self.mapa_tipos = {
            "LEI": "LEI",
            "LEI COMPLEMENTAR": "LCP",
            "DECRETO": "DEC",
            "DECRETO NE": "DNE"
        }

        self.norma_regex = re.compile(
            r'(?:^|\n|\r|\f)\s*(\*)?\s*(LEI\s+COMPLEMENTAR|LEI|DECRETO\s+NE|DECRETO)\s+N[º°]\s*([\d\s\.]+),?\s*DE\s+(.+?)(?:\n|$)',
            re.DOTALL
        )
        self.comandos_regex = re.compile(
            r"(Ficam\s+revogados|Fica\s+revogado|"
            r"Fica\s+acrescentad[oa]|Ficam\s+acrescentad[oa]s|"
            r"Fica\s+alterad[oa]|Ficam\s+alterad[oa]s|"
            r"\bAltera\b|\bAlteram\b|"
            r"Revoga|Revogam|"
            r"Dá\s+nova\s+redação|Dão\s+nova\s+redação|"
            r"Passa\s+a\s+vigorar|Passam\s+a\s+vigorar|"
            r"passando\s+o\s+item)",
            re.IGNORECASE
        )
        self.norma_alterada_regex = re.compile(
            r'(LEI\s+COMPLEMENTAR|LEI|DECRETO\s+NE|DECRETO)\s+'
            r'N[º°]?\s*([\d][\d\.\s]*)'
            r'(?:\s*/\s*(\d{4}))?'
            r'(?:,\s*de\s*([\s\S]*?\b\d{4}\b))?',
            re.IGNORECASE
        )

    def _clean_pdf_bytes(self, dirty_bytes: bytes) -> bytes:
        pdf_signature = b'%PDF-'
        try:
            start_index = dirty_bytes.index(pdf_signature)
            if start_index > 0:
                return dirty_bytes[start_index:]
            return dirty_bytes
        except ValueError:
            return dirty_bytes

    def _cortar_apos_atos_governador(self, texto: str) -> tuple[str, bool]:
        """
        Corta o texto a partir do marcador 'Atos do Governador'.
        Retorna:
          - texto antes do marcador
          - True se o marcador foi encontrado
        """
        m = re.search(r"\bAtos\s+do\s+Governador\b", texto, re.IGNORECASE)
        if not m:
            return texto, False

        return texto[:m.start()].strip(), True

    def _remover_rodape_autenticidade(self, texto: str) -> str:
                            if not texto:
                                return texto

                            padroes = [
                                r'Documento\s+assinado\s+eletronicamente\s+com\s+fundamento\s+no\s+art\.\s*6º\s+do\s+Decreto\s+n[º°]\s*47\.222,\s+de\s+26\s+de\s+julho\s+de\s+2017\.',
                                r'A\s+autenticidade\s+deste\s+documento\s+pode\s+ser\s+verificada\s+no\s+endereço\s+http://www\.jornalminasgerais\.mg\.gov\.br/Autenticidade,\s+sob\s+o\s+número\s+\d+\.',
                                r'http://www\.jornalminasgerais\.mg\.gov\.br/Autenticidade',
                             ]

                            texto_limpo = texto
                            for padrao in padroes:
                                texto_limpo = re.sub(padrao, ' ', texto_limpo, flags=re.IGNORECASE)

                            texto_limpo = re.sub(r'[ \t]+', ' ', texto_limpo)
                            texto_limpo = re.sub(r'\n\s*\n+', '\n', texto_limpo)
                            return texto_limpo.strip()

    def find_relevant_pages(self) -> tuple:
        try:
            reader = pypdf.PdfReader(io.BytesIO(self.pdf_bytes))
            start_page_num, end_page_num = None, None
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if not text.strip():
                    continue
                if re.search(r'Leis\s*e\s*Decretos', text, re.IGNORECASE):
                    start_page_num = i
                if re.search(r'Atos\s*do\s*Governador', text, re.IGNORECASE):
                    end_page_num = i
            if start_page_num is None or end_page_num is None or start_page_num > end_page_num:
                st.warning("Não foi encontrado o trecho de 'Leis e Decretos' ou 'Atos do Governador' para delimitar a seção.")
                return None, None
            return start_page_num, end_page_num + 1
        except Exception as e:
            st.error(f"Erro ao buscar páginas relevantes com PyPDF: {e}")
            return None, None

    def process_pdf(self) -> pd.DataFrame:
        start_page_idx, end_page_idx = self.find_relevant_pages()
        if start_page_idx is None:
            return pd.DataFrame()

        trechos = []
        fim_secao = False

        try:
            with pdfplumber.open(io.BytesIO(self.pdf_bytes)) as pdf:
                for i in range(start_page_idx, end_page_idx):
                    if fim_secao:
                        break

                    pagina = pdf.pages[i]
                    largura, altura = pagina.width, pagina.height

                    for col_num, (x0, x1) in enumerate(
                        [(0, largura / 2), (largura / 2, largura)],
                        start=1
                    ):
                        coluna = pagina.crop((x0, 0, x1, altura)).extract_text(layout=True) or ""
                        texto_limpo = coluna.replace("\xa0", " ")
                        texto_limpo = self._remover_rodape_autenticidade(texto_limpo)

                        texto_limpo, encontrou_fim = self._cortar_apos_atos_governador(texto_limpo)

                        if texto_limpo.strip():
                            trechos.append({
                                "pagina": i + 1,
                                "coluna": col_num,
                                "texto": texto_limpo
                            })

                        if encontrou_fim:
                            fim_secao = True
                            break

        except Exception as e:
            st.error(f"Erro ao extrair texto detalhado do PDF do Executivo: {e}")
            return pd.DataFrame()

        dados = []
        ultima_norma = None
        seen_alteracoes = set()

        for t in trechos:
            pagina = t["pagina"]
            coluna = t["coluna"]
            texto = t["texto"]
            eventos = []

            for m in self.norma_regex.finditer(texto):
                eventos.append(("published", m.start(), m))
            for c in self.comandos_regex.finditer(texto):
                eventos.append(("command", c.start(), c))

            eventos.sort(key=lambda e: e[1])

            for ev in eventos:
                tipo_ev, pos_ev, match_obj = ev
                command_text = match_obj.group(0).lower()

                if tipo_ev == "published":
                    match = match_obj

                    tem_asterisco = bool(match.group(1))
                    tipo_raw = match.group(2).strip()
                    tipo = self.mapa_tipos.get(tipo_raw.upper(), tipo_raw)
                    numero = match.group(3).replace(" ", "").replace(".", "")
                    data_texto = (match.group(4) or "").strip()

                    data_match = re.search(
                        r'(\d{1,2})(?:º)?\s+DE\s+([A-ZÇÃÁÉÍÓÔÚ]+)\s+DE\s+(\d{4})',
                        data_texto,
                        re.IGNORECASE
                    )

                    if data_match:
                        dia = data_match.group(1).zfill(2)
                        mes_nome = data_match.group(2).upper()
                        mes = meses.get(mes_nome, "")
                        ano = data_match.group(3)
                        sancao = f"{dia}/{mes}/{ano}" if mes else ""
                    else:
                        sancao = ""

                    linha = {
                        "Página": pagina,
                        "Coluna": coluna,
                        "Sanção": sancao,
                        "Tipo": tipo,
                        "Número": numero,
                        "Alterações": "",
                        "Observação": "*Retificação" if tem_asterisco else ""
                    }
                    dados.append(linha)
                    ultima_norma = linha
                    seen_alteracoes = set()

                elif tipo_ev == "command":
                    if ultima_norma is None:
                        continue

                    # A janela de busca deve ficar dentro da norma atual.
                    # Ela não pode atravessar para a próxima epígrafe normativa.
                    raio_antes = 350
                    raio_depois = 350

                    normas_na_coluna = list(self.norma_regex.finditer(texto))

                    norma_anterior_end = 0
                    proxima_norma_start = len(texto)

                    for nm in normas_na_coluna:
                        if nm.start() < pos_ev:
                            norma_anterior_end = max(norma_anterior_end, nm.end())
                        elif nm.start() > pos_ev:
                            proxima_norma_start = min(proxima_norma_start, nm.start())

                    start_block = max(norma_anterior_end, pos_ev - raio_antes)
                    end_block = min(proxima_norma_start, pos_ev + raio_depois)

                    bloco = texto[start_block:end_block]

                    alteracoes_para_processar = []

                    if "revoga" in command_text or "revogado" in command_text:
                        alteracoes_para_processar = list(
                            self.norma_alterada_regex.finditer(bloco)
                        )
                    else:
                        alteracoes_candidatas = list(
                            self.norma_alterada_regex.finditer(bloco)
                        )

                        if alteracoes_candidatas:
                            pos_comando_no_bloco = pos_ev - start_block

                            melhor_candidato = min(
                                alteracoes_candidatas,
                                key=lambda m: abs(m.start() - pos_comando_no_bloco)
                            )

                            alteracoes_para_processar = [melhor_candidato]

                    for alt in alteracoes_para_processar:
                        tipo_alt_raw = alt.group(1).strip()
                        tipo_alt = self.mapa_tipos.get(tipo_alt_raw.upper(), tipo_alt_raw)

                        num_alt_bruto = alt.group(2) or ""
                        num_alt = re.sub(r"[^\d]", "", num_alt_bruto)

                        ano_alt = (alt.group(3) or "").strip()

                        if not ano_alt:
                            data_texto_alt = alt.group(4) or ""
                            ano_match = re.search(r"\b(\d{4})\b", data_texto_alt)
                            if ano_match:
                                ano_alt = ano_match.group(1)

                        if tipo_alt == "DEC" and num_alt == "48589" and not ano_alt:
                            ano_alt = "2023"

                        chave_alt = f"{tipo_alt} {num_alt}"
                        if ano_alt:
                            chave_alt += f" {ano_alt}"

                        if tipo_alt == ultima_norma["Tipo"] and num_alt == ultima_norma["Número"]:
                            continue

                        if chave_alt in seen_alteracoes:
                            continue

                        seen_alteracoes.add(chave_alt)

                        if ultima_norma["Alterações"] == "":
                            ultima_norma["Alterações"] = chave_alt
                        else:
                            dados.append({
                                "Página": "",
                                "Coluna": "",
                                "Sanção": "",
                                "Tipo": "",
                                "Número": "",
                                "Alterações": chave_alt,
                                "Observação": ""
                            })
        return pd.DataFrame(dados) if dados else pd.DataFrame()


# =========================
# FUNÇÕES PARA GERADOR DE LINKS
# =========================
def dia_anterior():
    st.session_state.data -= timedelta(days=1)


def dia_posterior():
    st.session_state.data += timedelta(days=1)


def ir_hoje():
    st.session_state.data = datetime.today().date()


# =========================
# FUNÇÕES PARA CHATBOT
# =========================
DOCUMENTOS_PRE_CARREGADOS = {
    "Manual de Indexação": "manual_indexacao.pdf",
    "Regimento Interno da ALMG": "regimento.pdf",
    "Constituição Estadual": "constituicao.pdf",
    "Manual de redação parlamentar": "manual_redacao.pdf",
}

PROMPTS_POR_DOCUMENTO = {
    "Manual de Indexação": """
Personalização da IA:
Você deve atuar como um bibliotecário da Assembleia Legislativa do Estado de Minas Gerais, que tira dúvidas sobre como devem ser indexados os documentos legislativos com base no documento Conhecimento Manual de Indexação 4ª ed.-2023.docx.

====================================================================

Tarefa principal:
A partir do documento, você deve auxiliar o bibliotecário localizado as regras de indexação e resumo dos documentos legislativos.

====================================================================

Regras específicas:
Não consulte nenhum outro documento. 
Se não entender a pergunta ou não localizar a resposta, responda que não é possível responder a solicitação, pois não está prevista no Manual de Indexação.
O documento está estruturado em seções. Os exemplos vêm dentro de quadros. Você deve sugerir os termos de indexação conforme os exemplos, usando somente os termos mais específicos.
Você deve apresentar somente os termos mais específicos da indexação. Se o campo resumo estiver preenchido com #, significa que aquele tipo não precisa de resumo.
Caso ele esteja preenchido, você deve informar que ele deve ter resumo e mostrar o exemplo do resumo.
Sempre que achar a resposta, você deve primeiro listar os termos de indexação relevantes de maneira mais explícita, indicando a informação que será indexada. Por exemplo: "Para indexar [informação que vem na pergunta], você deve utilizar os seguintes termos:". Em seguida, liste os termos.
Depois, reproduza o quadro de exemplo correspondente, precedido da frase "Confira o exemplo a seguir:", e a resposta deve ser fechada com a seguinte citação da página, sem aspas:

"Você pode verificar a informação na página [cite a página] do Manual de Indexação."

Confira o exemplo a seguir:

| Tipo: | DEC 48.340 2021 |
| :--- | :--- |
| **Ementa:** | Altera o Decreto nº 48.589, de 22 de março de 2023, que regulamenta o Imposto sobre Operações relativas à Circulação de Mercadorias e sobre Prestações de Serviços de Transporte Interestadual e Intermunicipal e de Comunicação – ICMS. |
| **Indexação:** | Thesaurus/Tema/[...]/ICMS<br>Thesaurus/Tema/[...]/Substituição Tributária |
| **Resumo:** | # |

==================================================================================

Público-alvo: Os bibliotecários da Assembleia Legislativa do Estado de Minas Gerais, que vão indexar os documentos legislativos, atribuindo indexação e resumo.

---
Histórico da Conversa:
{historico_da_conversa}
---
Documento:
{conteudo_do_documento}
---
Pergunta: {pergunta_usuario}
""",

    "Regimento Interno da ALMG": """
Personalização da IA:
Você é um assistente especializado no Regimento Interno da Assembleia Legislativa de Minas Gerais.
Sua única fonte de informação é o documento "Regimento Interno da ALMG.pdf".

====================================================================

Regras de Resposta:
- Responda de forma objetiva, formal e clara.
- Se a informação não estiver no documento, responda: "A informação não foi encontrada no documento."
- Para cada resposta, forneça uma explicação detalhada, destrinchando o processo e as regras relacionadas. Sempre que possível, cite os artigos, parágrafos e incisos relevantes do Regimento.
- Sempre cite a fonte da sua resposta. A fonte deve ser a página onde a informação foi encontrada no documento, no seguinte formato: "Você pode verificar a informação na página [cite a página] do Regimento Interno da ALMG."

---
Histórico da Conversa:
{historico_da_conversa}
---
Documento:
{conteudo_do_documento}
---
Pergunta: {pergunta_usuario}
""",

    "Constituição Estadual": """
Personalização da IA:
Você é um assistente especializado na Constituição do Estado de Minas Gerais.
Sua única fonte de informação é o documento "Constituição Estadual.pdf".

====================================================================

Regras de Resposta:
- Responda de forma objetiva, formal e clara.
- Se a informação não estiver no documento, responda: "A informação não foi encontrada no documento."
- Para cada resposta, forneça uma explicação detalhada, destrinchando o processo e as regras relacionadas. Sempre que possível, cite os artigos, parágrafos e incisos relevantes da Constituição.
- Sempre cite a fonte da sua resposta. A fonte deve ser a página onde a informação foi encontrada no documento, no seguinte formato: "Você pode verificar a informação na página [cite a página] da Constituição Estadual."

---
Histórico da Conversa:
{historico_da_conversa}
---
Documento:
{conteudo_do_documento}
---
Pergunta: {pergunta_usuario}
""",

    "Manual de redação parlamentar": """
Personalização da IA:
Você é um assistente especializado no Manual de Redação Parlamentar da Assembleia Legislativa de Minas Gerais.
Sua única fonte de informação é o documento "manual_redacao.pdf".

====================================================================

Regras de Resposta:
- Responda de forma objetiva, formal e clara.
- Se a informação não estiver no documento, responda: "A informação não foi encontrada no documento."
- Para cada resposta, forneça uma explicação detalhada, destrinchando o processo e as regras relacionadas. Sempre que possível, cite as seções, capítulos e exemplos relevantes do Manual de Redação.
- Sempre cite a fonte da sua resposta. A fonte deve ser a página onde a informação foi encontrada no documento, no seguinte formato: "Você pode verificar a informação na página [cite a página] do Manual de redação parlamentar."

---
Histórico da Conversa:
{historico_da_conversa}
---
Documento:
{conteudo_do_documento}
---
Pergunta: {pergunta_usuario}
""",
}


def carregar_documento_do_disco(caminho_arquivo):
    if not os.path.exists(caminho_arquivo):
        st.error(f"Erro: O arquivo '{caminho_arquivo}' não foi encontrado.")
        return None

    extensao = os.path.splitext(caminho_arquivo)[1].lower()

    try:
        if extensao == ".txt":
            with open(caminho_arquivo, 'r', encoding='utf-8') as f:
                return f.read()
        elif extensao == ".docx":
            doc = docx.Document(caminho_arquivo)
            texto = [paragrafo.text for paragrafo in doc.paragraphs]
            return "\n".join(texto)
        elif extensao == ".pdf":
            texto = ""
            with fitz.open(caminho_arquivo) as pdf_doc:
                for page in pdf_doc:
                    texto += page.get_text()
            return texto
        else:
            st.error(f"Erro: Formato de arquivo '{extensao}' não suportado.")
            return None
    except Exception as e:
        st.error(f"Ocorreu um erro ao ler o arquivo: {e}")
        return None


def get_api_key():
    api_key = os.environ.get("GOOGLE_API_KEY") or st.secrets.get("GOOGLE_API_KEY")
    if not api_key:
        st.error("Erro: A chave de API não foi configurada.")
        return None
    return api_key


def answer_from_document(prompt_completo, api_key):
    if not api_key:
        return "Erro: Chave de API ausente."

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"

    payload = {
        "contents": [{"parts": [{"text": prompt_completo}]}]
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
        resposta = result.get("candidates", [])[0].get("content", {}).get("parts", [])[0].get("text", "Não foi possível gerar a resposta.")
        return resposta
    except requests.exceptions.HTTPError as http_err:
        return f"Erro na comunicação com a API: {http_err}"
    except Exception as e:
        return f"Ocorreu um erro: {e}"


# =========================
# FUNÇÕES PARA GERADOR DE TERMOS E RESUMOS
# =========================
def carregar_dicionario_termos(nome_arquivo):
    termos = []
    mapa_hierarquia = {}

    try:
        with open(nome_arquivo, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                partes = [p.strip() for p in line.split('>') if p.strip()]

                if not partes:
                    continue

                termo_especifico = partes[-1]
                if termo_especifico:
                    termo_especifico = termo_especifico.replace('\t', '')
                    termos.append(termo_especifico)

                if len(partes) > 1:
                    termo_pai = partes[-2].replace('\t', '')
                    if termo_pai not in mapa_hierarquia:
                        mapa_hierarquia[termo_pai] = []
                    mapa_hierarquia[termo_pai].append(termo_especifico)

    except FileNotFoundError:
        st.error(f"Erro: O arquivo '{nome_arquivo}' não foi encontrado.")
        return [], {}
    except Exception as e:
        st.error(f"Ocorreu um erro ao carregar o dicionário de termos: {e}")
        return [], {}

    return termos, mapa_hierarquia


def carregar_exemplos_resumos(nome_arquivo):
    if not os.path.exists(nome_arquivo):
        print(f"Aviso: Arquivo de exemplos '{nome_arquivo}' não encontrado. Usando apenas o prompt base.")
        return []

    try:
        df = pd.read_csv(nome_arquivo)
        exemplos_formatados = []

        for index, row in df.iterrows():
            exemplo = f"""
            --- Exemplo {index + 1} ---
            TEXTO ORIGINAL: {row['texto_original']}
            RESUMO ESPERADO: {row['resumo_esperado']}
            """
            exemplos_formatados.append(exemplo)

        return exemplos_formatados

    except Exception as e:
        print(f"Erro ao carregar exemplos de resumo: {e}")
        return []


def aplicar_logica_hierarquia(termos_sugeridos, mapa_hierarquia):
    termos_finais = set(termos_sugeridos)
    mapa_inverso_hierarquia = {}

    for pai, filhos in mapa_hierarquia.items():
        for filho in filhos:
            mapa_inverso_hierarquia[filho] = pai

    termos_a_remover = set()
    for termo in termos_sugeridos:
        if termo in mapa_inverso_hierarquia:
            termo_pai = mapa_inverso_hierarquia[termo]
            if termo_pai in termos_finais:
                termos_a_remover.add(termo_pai)

    termos_finais = termos_finais - termos_a_remover
    return list(termos_finais)


def gerar_resumo(texto_original, exemplos_resumos):
    api_key = get_api_key()

    if not api_key:
        st.error("Erro: A chave de API não foi configurada.")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    regras_adicionais = """
    - Mantenha o resumo em um único parágrafo, com no máximo 4 frases.
    - Use linguagem formal e evite gírias.
    - Mantenha um tom objetivo e neutro.
    - Use verbos na terceira pessoa do singular, na voz ativa.
    - Para descrever ações ou responsabilidades de autoridades, prefira o uso de verbos auxiliares como 'deve' ou 'pode' para indicar obrigação ou possibilidade.
    - Evite o uso de verbos com partícula apassivadora ou de indeterminação do sujeito.
    - Separe as siglas com o caractere "–".
    - Inicie o resumo com sujeito explícito seguido de verbo (Ex: "A norma estabelece..."; "O decreto determina..."; "A lei dispõe...")
    - Sempre que citar uma data, utilize o formato dd/mm/aaaa, quando o mês for entre outubro e dezembro, e dd/m/aaaa, quando o mês for entre janeiro e setembro (Ex: 23/3/2026, 12/12/2030, 15/1/2025.)
    - Não inclua a parte sobre a vigência da lei.
    - O resumo deve focar em três pontos principais:
        1. O que o programa institui e a quem se destina.
        2. Quem aciona o alerta e em que condições.
        3. Quais informações podem ser incluídas nas mensagens e quais tecnologias são permitidas.
    - O resumo não deve mencionar:
        - Detalhes sobre a Lei Geral de Proteção de Dados – LGPD.
        - Detalhes específicos sobre a Defesa Civil, ANATEL ou outros órgãos.
        - Nomes específicos de programas.
        - 'Minas Gerais' ou 'Estado de Minas Gerais'.
    - Todas as palavras de origem estrangeira devem ser escritas entre aspas.
    - Represente os numerais de 0 a 9 por extenso, para 10 ou mais, use apenas o algarismo.
    """

    exemplos_prompt = "\n".join(exemplos_resumos)
    contexto_exemplos = ""
    if exemplos_prompt:
        contexto_exemplos = f"""
        # EXEMPLOS DE FORMATAÇÃO E ESTILO (Few-Shot Examples)
        Aqui estão exemplos de como o resumo final DEVE ser formatado e escrito, aplicando todas as regras abaixo. Use estes exemplos para padronizar sua resposta.
        {exemplos_prompt}
        """

    prompt_resumo = f"""
    {contexto_exemplos}

    # INSTRUÇÃO PRINCIPAL
    Resuma a seguinte proposição legislativa de forma clara, concisa e com as regras abaixo. Sua resposta deve ser apenas o resumo, sem cabeçalhos.

    # Regras para o Resumo
    {regras_adicionais}

    # Texto da Proposição a ser resumida
    {texto_original}
    """

    payload = {
        "contents": [{"parts": [{"text": prompt_resumo}]}],
        "tools": [{"google_search": {}}]
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
        return result.get("candidates", [])[0].get("content", {}).get("parts", [])[0].get("text", "")
    except requests.exceptions.HTTPError as http_err:
        st.error(f"Erro na comunicação com a API: {http_err}")
    except Exception as e:
        st.error(f"Ocorreu um erro: {e}")

    return "Não foi possível gerar o resumo."


def gerar_termos_llm(texto_original, termos_dicionario, num_termos):
    api_key = get_api_key()

    if not api_key:
        st.error("Erro: A chave de API não foi configurada.")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    prompt_termos = f"""
    A partir do texto abaixo, selecione até {num_termos} termos de indexação relevantes.
    Os termos de indexação devem ser selecionados EXCLUSIVAMENTE da seguinte lista:
    {', '.join(termos_dicionario)}
    Se nenhum termo da lista for aplicável, a resposta deve ser uma lista JSON vazia: [].
    A resposta DEVE ser uma lista JSON de strings, sem texto adicional antes ou depois.

    Texto da Proposição: {texto_original}
    """

    payload = {
        "contents": [{"parts": [{"text": prompt_termos}]}],
        "tools": [{"google_search": {}}]
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        result = response.json()

        json_string = result.get("candidates", [])[0].get("content", {}).get("parts", [])[0].get("text", "")

        termos_sugeridos = []
        matches = re.findall(r'(\[.*?\])', json_string, re.DOTALL)

        for match in matches:
            cleaned_string = match.replace("'", '"')
            try:
                parsed_list = json.loads(cleaned_string)
                if isinstance(parsed_list, list) and all(isinstance(item, str) for item in parsed_list):
                    termos_sugeridos = parsed_list
                    break
            except json.JSONDecodeError:
                continue

        return termos_sugeridos

    except requests.exceptions.HTTPError as http_err:
        st.error(f"Erro na comunicação com a API: {http_err}")
    except Exception as e:
        st.error(f"Ocorreu um erro: {e}")

    return []


# =========================
# FUNÇÕES PARA CONVERSOR DE PDF EM TEXTO (OCR)
# =========================
def correct_ocr_text(raw_text):
    """
    Chama a API da Gemini para corrigir erros de OCR, normalizar a ortografia arcaica,
    remover cabeçalho e formatar dados estruturados como tabela em Markdown — SEM negrito.
    """
    api_key = get_api_key()
    if not api_key:
        st.error("Chave de API do Gemini não encontrada. Verifique as variáveis de ambiente ou secrets.")
        return raw_text

    apiUrl = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    system_prompt = """
Você é um corretor ortográfico e normalizador de texto brasileiro, especializado em documentos históricos.
Sua tarefa é receber um texto bruto de OCR, corrigir erros e normalizar a ortografia arcaica (ex: 'Geraes' → 'Gerais', 'legaes' → 'legais').
**Você deve retornar o resultado INTEIRO no formato Markdown.**

Regras estritas:
- **NÃO use negrito (`**` ou `__`) em NENHUMA parte do texto.**
- **Remova o cabeçalho do jornal/documento**: TÍTULO (ex: "MINAS GERAES"), data, número da edição, assinatura, venda avulsa, linhas divisórias. Mantenha apenas o corpo do texto.
- **Corrija erros óbvios de OCR** e normalize ortografia arcaica.
- **Se o texto contiver pares claros de "rótulo … valor" (ex: "Ativo … 450:200$000"), recrie-os como uma tabela Markdown com DUAS COLUNAS, SEM CABEÇALHOS.**
  - A primeira coluna deve conter o item descritivo (ex: "Saldo de 1930", "Rendas arrecadadas").
  - A segunda coluna deve conter o valor correspondente (ex: "13:868$112", "243:234$308").
  - **Não crie cabeçalhos como "Item" e "Valor". Deixe as células vazias na primeira linha ou use apenas `--- | ---` como separador.**
  - **Se houver títulos seccionais (ex: "Receita:", "Despesa:", "Situação patrimonial..."), inclua-os como linhas de tabela, com o texto na primeira coluna e a segunda coluna vazia.**
  - **Mantenha a ordem exata dos itens do texto original. Não invente, não resuma, não omita.**
  - **Nunca adicione linhas como "Total", "Subtotal", "Geral", etc., a menos que estejam explicitamente no texto.**
- **Retorne APENAS o texto corrigido em Markdown**, sem explicações, sem blocos de código (ex: ```markdown```), sem introduções.
"""
    payload = {
        "contents": [{"parts": [{"text": raw_text}]}],
        "system_instruction": {"parts": [{"text": system_prompt}]},
    }

    try:
        response = requests.post(
            apiUrl,
            headers={'Content-Type': 'application/json'},
            data=json.dumps(payload)
        )
        if response.status_code == 400:
            st.error(f"Erro detalhado da API (400): {response.text}. Verifique o tamanho do PDF.")
            return raw_text
        response.raise_for_status()
        result = response.json()
        corrected_text = result.get("candidates", [])[0].get("content", {}).get("parts", [])[0].get("text", "")
        return corrected_text if corrected_text else raw_text
    except requests.exceptions.HTTPError as http_err:
        st.error(f"Erro HTTP ({http_err.response.status_code}) na correção via Gemini. Exibindo texto bruto.")
    except Exception as e:
        st.error(f"Ocorreu um erro inesperado durante a correção via Gemini: {e}. Exibindo texto bruto.")
    return raw_text


# =========================
# FUNÇÃO PRINCIPAL DA APLICAÇÃO
# =========================
def run_app():
    st.set_page_config(page_title="Assistente Virtual da GIL")

    st.markdown("""
        <style>
        .title-container {
            text-align: center;
            background-color: #f0f0f0;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .main-title {
            color: #d11a2a;
            font-size: 3em;
            font-weight: bold;
            margin-bottom: 0;
        }
        .subtitle-gil {
            color: gray;
            font-size: 1.5em;
            margin-top: 5px;
        }
        .stRadio > div {
            flex-direction: column;
            align-items: flex-start;
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown("""
        <div class="title-container">
            <h1 class="main-title">Assistente Virtual da GIL</h1>
            <h4 class="subtitle-gil">Gerência de Informação Legislativa – GIL/GDI</h4>
        </div>
    """, unsafe_allow_html=True)

    st.divider()
    opcao = st.radio(
        "Escolha a funcionalidade:",
        (
            "Extrator de Diários Oficiais",
            "Gerador de Links do Jornal Minas Gerais",
            "Chatbot – Gerência de Informação Legislativa",
            "Gerador de Termos e Resumos de Proposições",
            "Conversor de PDF em texto (OCR)"
        ),
        horizontal=False
    )
    st.divider()

    # =========================================================
    # NOVO EXTRATOR - AUTOMAÇÃO GOOGLE SHEETS
    # =========================================================
    if opcao == "Extrator de Diários Oficiais":
        st.subheader("Automação dos Diários Oficiais")

        try:
            spreadsheet = conectar_gsheet()
        except Exception as e:
            st.error(f"Erro ao conectar na planilha do Google Sheets: {e}")
            st.stop()

        if "data_ref" not in st.session_state:
            st.session_state["data_ref"] = data_padrao_operacional()

        if "ajuste_msg" not in st.session_state:
            st.session_state["ajuste_msg"] = ""

        st.caption("Selecione a data de trabalho")

        data_selecionada = st.date_input(
            "Data",
            value=st.session_state["data_ref"],
            format="DD/MM/YYYY",
            max_value=date.today()
        )

        data_ajustada = ajustar_data_operacional(data_selecionada)

        if data_ajustada != st.session_state["data_ref"]:
            st.session_state["data_ref"] = data_ajustada

            if data_ajustada != data_selecionada:
                st.session_state["ajuste_msg"] = (
                    f"Data ajustada automaticamente para "
                    f"{data_ajustada.strftime('%d/%m/%Y')}."
                )
            else:
                st.session_state["ajuste_msg"] = ""

            st.rerun()

        if st.session_state["ajuste_msg"]:
            st.info(st.session_state["ajuste_msg"])
            st.session_state["ajuste_msg"] = ""

        data_obj = st.session_state["data_ref"]
        data = data_obj.strftime("%d/%m/%Y")

        pode_processar = True

        if data_obj > date.today():
            st.error("Data futura não é permitida.")
            pode_processar = False
        else:
            existe, nome_encontrado = aba_existe(spreadsheet, data)

            if existe:
                st.caption(f"🟩 {data} — aba '{nome_encontrado}' já existe")
                pode_processar = False
            else:
                st.caption(f"🟥 {data} — ainda não criada")

        if st.button("Processar", disabled=not pode_processar, use_container_width=True):
            try:
                d = preparar_datas(data)
            except ValueError:
                st.error("Data inválida. Use o formato DD/MM/AAAA.")
                st.stop()

            urls = montar_urls(d)
            st.write("🔎 Processando...")

            df_exec = pd.DataFrame()
            df_adm = pd.DataFrame()
            df_leg_normas = pd.DataFrame()
            df_props = pd.DataFrame()
            df_reqs = pd.DataFrame()
            df_pareceres = pd.DataFrame()

            # ================= EXECUTIVO =================
            try:
                garantir_playwright_chromium()
                pdf_exec = baixar_pdf_jornal_mg_por_link(urls["executivo_html"])
                exec_proc = ExecutiveProcessor(pdf_exec)
                df_exec = exec_proc.process_pdf()

                if not df_exec.empty:
                    df_exec = df_exec.copy()
                    if "Sanção" in df_exec.columns:
                        df_exec["Ano"] = df_exec["Sanção"].fillna("").astype(str).str[-4:]
                    else:
                        df_exec["Ano"] = ""

                st.success(f"Executivo OK ({len(df_exec)} registros)")
            except Exception as e:
                st.error(f"Erro Executivo: {e}")
                df_exec = pd.DataFrame()

            # ================= LEGISLATIVO =================
            try:
                pdf_leg = baixar(urls["legislativo"])
                leg_proc = LegislativeProcessor(pdf_leg)
                dados_leg = leg_proc.process_all()

                df_leg_normas = dados_leg["Normas"].copy()
                if not df_leg_normas.empty:
                    df_leg_normas = df_leg_normas.rename(columns={"Sigla": "Tipo"})

                df_props = dados_leg["Proposicoes"].copy()
                if not df_props.empty:
                    df_props = df_props.rename(columns={
                        "Sigla": "Tipo",
                        "Categoria": "Observação"
                    })

                df_reqs = dados_leg["Requerimentos"].copy()
                if not df_reqs.empty:
                    df_reqs = df_reqs.rename(columns={
                        "Sigla": "Tipo",
                        "Classificação": "Observação"
                    })

                df_pareceres = dados_leg["Pareceres"].copy()
                if not df_pareceres.empty:
                    df_pareceres = df_pareceres.rename(columns={
                        "Sigla": "Tipo",
                        "Tipo": "Subtipo"
                    })

                st.success(f"Legislativo OK ({len(df_leg_normas)} normas)")
                st.success(f"Proposições OK ({len(df_props)} registros)")
                st.success(f"Requerimentos OK ({len(df_reqs)} registros)")
                st.success(f"Pareceres OK ({len(df_pareceres)} registros)")
            except Exception as e:
                st.error(f"Erro Legislativo: {e}")
                df_leg_normas = pd.DataFrame()
                df_props = pd.DataFrame()
                df_reqs = pd.DataFrame()
                df_pareceres = pd.DataFrame()

            # ================= ADMINISTRATIVO =================
            try:
                pdf_adm = baixar(urls["administrativo"])
                adm_proc = AdministrativeProcessor(pdf_adm)
                df_adm = adm_proc.process_pdf()

                if df_adm is None:
                    df_adm = pd.DataFrame()
                elif not df_adm.empty:
                    df_adm = df_adm.rename(columns={"Sigla": "Tipo"})

                st.success(f"Administrativo OK ({len(df_adm)} registros)")
            except Exception as e:
                st.error(f"Erro Administrativo: {e}")
                df_adm = pd.DataFrame()

            # ================= DISTRIBUIÇÃO AUTOMÁTICA =================
            indisponiveis, aviso_calendar = listar_indisponiveis_calendar(data_obj)
            if aviso_calendar:
                st.warning(aviso_calendar)

            if indisponiveis:
                st.info(
                    "Indisponíveis por Licença/Férias no calendário: " +
                    ", ".join(sorted(indisponiveis))
                )
            else:
                st.info("Nenhuma Licença/Férias encontrada no calendário para a data selecionada.")

            for aviso in validar_pools_distribuicao(indisponiveis):
                st.warning(aviso)

            (
                df_exec,
                df_adm,
                df_leg_normas,
                df_props,
                df_reqs,
                df_pareceres,
            ) = distribuir_tarefas_extraidas_em_blocos(
                df_exec=df_exec,
                df_adm=df_adm,
                df_leg_normas=df_leg_normas,
                df_props=df_props,
                df_reqs=df_reqs,
                df_pareceres=df_pareceres,
                indisponiveis=indisponiveis,
            )

            # ================= GOOGLE SHEETS =================
            try:
                ws = obter_ou_criar_aba_data(
                    spreadsheet=spreadsheet,
                    data_str=data,
                    nome_modelo=ABA_MODELO
                )

                preencher_aba_modelo(
                    ws=ws,
                    data_str=d["display"],
                    urls=urls,
                    df_exec=df_exec,
                    df_adm=df_adm,
                    df_leg_normas=df_leg_normas,
                    df_props=df_props,
                    df_reqs=df_reqs,
                    df_pareceres=df_pareceres
                )

                st.success(f"Aba '{ws.title}' criada e preenchida com sucesso 🚀")
                st.rerun()

            except Exception as e:
                st.error(f"Erro Google Sheets: {e}")

    # =========================================================
    # GERADOR DE LINKS
    # =========================================================
    elif opcao == "Gerador de Links do Jornal Minas Gerais":
        min_data = date(1835, 1, 1)
        max_data = datetime.today().date()

        if "data" not in st.session_state:
            data_inicial = datetime.today().date()
            if data_inicial < min_data:
                data_inicial = min_data
            elif data_inicial > max_data:
                data_inicial = max_data
            st.session_state.data = data_inicial

        data_selecionada = st.date_input(
            "Selecione a data de publicação:",
            st.session_state.data,
            min_value=min_data,
            max_value=max_data
        )
        st.session_state.data = data_selecionada

        col1, col2, col3 = st.columns([1, 1, 1])

        with col1:
            if st.session_state.data > min_data:
                if st.button("⬅️ Dia Anterior"):
                    dia_anterior()
            else:
                st.button("⬅️ Dia Anterior", disabled=True)

        with col2:
            if st.button("📅 Hoje"):
                ir_hoje()

        with col3:
            if st.session_state.data < max_data:
                if st.button("➡️ Próximo Dia"):
                    dia_posterior()
            else:
                st.button("➡️ Próximo Dia", disabled=True)

        if st.button("📝 Gerar link"):
            data_formatada_link = st.session_state.data.strftime("%Y-%m-%d")
            dados_dict = {"dataPublicacaoSelecionada": f"{data_formatada_link}T06:00:00.000Z"}
            json_str = json.dumps(dados_dict, separators=(',', ':'))
            novo_dados = json_str.replace("{", "%7B").replace("}", "%7D").replace('"', "%22")
            novo_link = f"https://www.jornalminasgerais.mg.gov.br/edicao-do-dia?dados={novo_dados}"
            st.markdown(f"**Data escolhida:** {st.session_state.data.strftime('%d/%m/%Y')}")
            st.success("Link gerado com sucesso!")
            st.text_area("Link:", value=novo_link, height=100)

    # =========================================================
    # CHATBOT
    # =========================================================
    elif opcao == "Chatbot – Gerência de Informação Legislativa":
        file_names = list(DOCUMENTOS_PRE_CARREGADOS.keys())
        if not file_names:
            st.warning("Nenhum documento pré-carregado. Por favor, adicione arquivos à lista `DOCUMENTOS_PRE_CARREGADOS` no código.")
        else:
            selected_file_name_display = st.selectbox("Escolha o assunto sobre o qual você quer conversar:", file_names)
            selected_file_path = DOCUMENTOS_PRE_CARREGADOS[selected_file_name_display]

            if selected_file_name_display in PROMPTS_POR_DOCUMENTO:
                prompt_base = PROMPTS_POR_DOCUMENTO[selected_file_name_display]
            else:
                st.error("Erro: Não foi encontrado um prompt personalizado para este documento.")
                prompt_base = "Responda a pergunta do usuário com base no seguinte documento: {conteudo_do_documento}. Pergunta: {pergunta_usuario}"

            DOCUMENTO_CONTEUDO = carregar_documento_do_disco(selected_file_path)

            if DOCUMENTO_CONTEUDO:
                st.success(f"Documento '{selected_file_name_display}' carregado com sucesso!")

                if "messages" not in st.session_state:
                    st.session_state.messages = []

                for message in st.session_state.messages:
                    with st.chat_message(message["role"]):
                        st.markdown(message["content"])

                if pergunta_usuario := st.chat_input("Faça sua pergunta:"):
                    st.session_state.messages.append({"role": "user", "content": pergunta_usuario})

                    with st.chat_message("user"):
                        st.markdown(pergunta_usuario)

                    with st.chat_message("assistant"):
                        with st.spinner("Buscando a resposta..."):
                            api_key = get_api_key()
                            if api_key and DOCUMENTO_CONTEUDO:
                                prompt_completo = prompt_base.format(
                                    historico_da_conversa=st.session_state.messages,
                                    conteudo_do_documento=DOCUMENTO_CONTEUDO,
                                    pergunta_usuario=pergunta_usuario
                                )
                                resposta = answer_from_document(prompt_completo, api_key)
                                st.markdown(resposta)
                                st.session_state.messages.append({"role": "assistant", "content": resposta})

            if st.button("Limpar Chat"):
                st.session_state.messages = []
                st.rerun()

    # =========================================================
    # GERADOR DE TERMOS E RESUMOS
    # =========================================================
    elif opcao == "Gerador de Termos e Resumos de Proposições":
        TIPOS_DOCUMENTO = {
            "Documentos Gerais": "dicionario_termos.txt"
        }

        tipo_documento_selecionado = st.selectbox(
            "Selecione o tipo de documento:",
            options=["Proposição", "Requerimento"],
        )

        num_termos_selecionado = st.selectbox(
            "Selecione a quantidade de termos de indexação:",
            options=["Até 3", "de 3 a 5", "5+"],
        )

        num_termos = 10
        if num_termos_selecionado == "Até 3":
            num_termos = 3
        elif num_termos_selecionado == "de 3 a 5":
            num_termos = 5

        arquivo_dicionario = TIPOS_DOCUMENTO["Documentos Gerais"]
        termo_dicionario, mapa_hierarquia = carregar_dicionario_termos(arquivo_dicionario)

        if "Minas Gerais (MG)" in termo_dicionario:
            termo_dicionario.remove("Minas Gerais (MG)")

        texto_proposicao = st.text_area(
            "Cole o texto da proposição aqui:",
            height=300,
            placeholder="Ex: 'A presente proposição dispõe sobre a criação de um programa de incentivo...'"
        )

        if st.button("Gerar Resumo e Termos"):
            if not texto_proposicao:
                st.warning("Por favor, cole o texto da proposição para continuar.")
            else:
                with st.spinner('Gerando resumo e termos...'):
                    resumo_gerado = ""
                    termos_finais = []

                    exemplos_resumos = carregar_exemplos_resumos("exemplos_resumos.csv")

                    match_doacao = re.search(r"Município de ([\w\s-]+?)(?:\s+o\simóvel|\s+os\simóveis|\s*\d)", texto_proposicao, re.IGNORECASE)
                    match_servidao = re.search(r"declara de utilidade pública,.*servidão.*no Município de ([\w\s-]+)", texto_proposicao, re.IGNORECASE | re.DOTALL)
                    match_utilidade_publica = re.search(r"declara de utilidade pública.*no Município de ([\w\s-]+)", texto_proposicao, re.IGNORECASE | re.DOTALL)

                    if match_doacao:
                        municipio = match_doacao.group(1).strip()
                        termos_finais = ["Doação de Imóvel", municipio]
                        resumo_gerado = "Não precisa de resumo."
                    elif match_servidao:
                        municipio = match_servidao.group(1).strip()
                        termos_finais = ["Servidão Administrativa", municipio]
                        resumo_gerado = "Não precisa de resumo."
                    elif match_utilidade_publica:
                        municipio = match_utilidade_publica.group(1).strip()
                        termos_finais = ["Utilidade Pública", municipio]
                        resumo_gerado = "Não precisa de resumo."
                    else:
                        if tipo_documento_selecionado == "Proposição":
                            resumo_gerado = gerar_resumo(texto_proposicao, exemplos_resumos)
                        elif tipo_documento_selecionado == "Requerimento":
                            resumo_gerado = "Não precisa de resumo."

                        termos_sugeridos_brutos = gerar_termos_llm(texto_proposicao, termo_dicionario, num_termos)

                        if re.search(r"institui (?:a|o) (?:política|programa) estadual|cria (?:a|o) (?:política|programa) estadual", texto_proposicao, re.IGNORECASE):
                            if termos_sugeridos_brutos is not None and "Política Pública" not in termos_sugeridos_brutos:
                                termos_sugeridos_brutos.append("Política Pública")

                        if termos_sugeridos_brutos is not None:
                            termos_finais = aplicar_logica_hierarquia(termos_sugeridos_brutos, mapa_hierarquia)
                        else:
                            termos_finais = []

                    st.subheader("Resumo")
                    st.markdown(f"<p style='text-align: justify;'>{resumo_gerado}</p>", unsafe_allow_html=True)

                    st.subheader("Termos de Indexação")
                    if termos_finais:
                        termos_str = ", ".join(termos_finais)
                        st.success(termos_str)
                    else:
                        st.warning("Nenhum termo relevante foi encontrado no dicionário.")

    # =========================================================
    # OCR
    # =========================================================
    elif opcao == "Conversor de PDF em texto (OCR)":
        OCRMypdf_PATH = shutil.which("ocrmypdf")
        PANDOC_PATH = shutil.which("pandoc")

        if not OCRMypdf_PATH or not PANDOC_PATH:
            st.error("""
                O executável **'ocrmypdf' ou 'pandoc' não foi encontrado**.
                Verifique se o arquivo `packages.txt` (na raiz do repositório) contém as linhas `ocrmypdf` e `pandoc`.
                Pode ser necessário forçar um re-deploy ou restart do aplicativo.
            """)
            st.stop()

        st.title("Conversor de PDF para ODT (LibreOffice)")
        st.warning("⚠️ **AVISO IMPORTANTE:** Este aplicativo só deve ser utilizado para edições antigas do Jornal Minas Gerais. Versões atuais são pesadas e podem fazer o aplicativo parar de funcionar devido aos limites de recursos.")

        uploaded_file = st.file_uploader("Escolha um arquivo PDF...", type=["pdf"])

        if uploaded_file is not None:
            st.info("Arquivo carregado com sucesso. Processando...")

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as input_file:
                input_file.write(uploaded_file.read())
                input_filepath = input_file.name

            output_ocr_filepath = os.path.join(tempfile.gettempdir(), "output_ocr.pdf")
            markdown_filepath = os.path.join(tempfile.gettempdir(), "texto_temporario.md")
            odt_filepath = os.path.join(tempfile.gettempdir(), "documento_final.odt")

            try:
                with st.spinner("1/3: Extraindo texto bruto do PDF com OCR..."):
                    command_ocr = [
                        OCRMypdf_PATH,
                        "--force-ocr",
                        "--sidecar",
                        markdown_filepath,
                        input_filepath,
                        output_ocr_filepath
                    ]

                    subprocess.run(command_ocr, check=True, capture_output=True, text=True)
                    st.success("Extração de texto concluída.")

                if os.path.exists(markdown_filepath):
                    with open(markdown_filepath, "r", encoding="utf-8") as f:
                        sidecar_text_raw = f.read()

                    with st.spinner("2/3: Corrigindo ortografia arcaica, removendo cabeçalhos e formatando tabelas via IA..."):
                        sidecar_text_corrected = correct_ocr_text(sidecar_text_raw)

                    with open(markdown_filepath, "w", encoding='utf-8') as f:
                        f.write(sidecar_text_corrected)

                    with st.spinner("3/3: Convertendo Markdown para arquivo ODT do LibreOffice..."):
                        command_pandoc = [
                            PANDOC_PATH,
                            "--standalone",
                            "-s",
                            markdown_filepath,
                            "-o",
                            odt_filepath
                        ]
                        subprocess.run(command_pandoc, check=True, capture_output=True, text=True)
                        st.success("Conversão para ODT concluída! Seu documento está pronto para download.")

                    st.markdown("---")
                    st.subheader("✅ Processo Finalizado com Sucesso")
                    st.info("O download abaixo contém o texto corrigido, com ortografia normalizada e tabelas reestruturadas, pronto para edição no LibreOffice Writer.")

                    with open(odt_filepath, "rb") as f:
                        st.download_button(
                            label="⬇️ Baixar Documento Formatado (.odt)",
                            data=f.read(),
                            file_name="documento_final_formatado.odt",
                            mime="application/vnd.oasis.opendocument.text"
                        )

                    st.markdown("---")

            except subprocess.CalledProcessError as e:
                st.error(f"Erro ao processar o arquivo (OCR ou Pandoc). Detalhes: {e.stderr}")
                st.code(f"Comando tentado: {' '.join(e.cmd)}")
            except Exception as e:
                st.error(f"Ocorreu um erro inesperado: {e}")
            finally:
                for filepath in [input_filepath, output_ocr_filepath, markdown_filepath, odt_filepath]:
                    if os.path.exists(filepath):
                        try:
                            os.unlink(filepath)
                        except Exception:
                            pass


if __name__ == "__main__":
    run_app()
