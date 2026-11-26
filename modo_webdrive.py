# -*- coding: utf-8 -*-
"""Processamento dos três diários e gravação ODS via WebDrive, com proteção ETag."""
import hashlib
import zipfile as zipfile_archive
import hmac
import io
import json
import re
import subprocess
import tempfile
import zipfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree as ET

import pandas as pd
import requests
import streamlit as st

import extracao_original as original
from corrigir_links_ods import corrigir_links_aba
from lxml import etree


HOST = 'webdrive.almg.gov.br'
PASTA_PERMITIDA = 'GDI-GIL/Testes'
ARQUIVO_PERMITIDO = 'Controle de Atividades 2026.ods'
MIME_ODS = 'application/vnd.oasis.opendocument.spreadsheet'
MAX_ODS = 30 * 1024 * 1024
MAX_PDF_ADM = 100 * 1024 * 1024
CALDAV_URL = 'https://webdrive.almg.gov.br/remote.php/dav/'
CALENDARIO_AFASTAMENTOS = '(GIL) Afastamentos, Férias e Licenças'

PASTA_DIARIO_ADMINISTRATIVO = 'GDI-GIL/Gerencial/diario_administrativo'


def segredo(nome):
    valor = str(st.secrets.get(nome, '')).strip()
    if not valor:
        raise ValueError('Configure o Secret ' + nome)
    return valor


def endereco_ods():
    base = str(st.secrets.get('WEB_DAV_BASE', 'https://webdrive.almg.gov.br/remote.php/webdav')).rstrip('/')
    usuario = segredo('WEB_DAV_USUARIO')
    pasta = str(st.secrets.get('WEB_DAV_PASTA', PASTA_PERMITIDA)).strip('/')
    arquivo = str(st.secrets.get('WEB_DAV_ARQUIVO', ARQUIVO_PERMITIDO))
    partes = urlsplit(base)
    if partes.scheme != 'https' or partes.hostname != HOST or partes.port not in (None, 443):
        raise ValueError('O WebDAV deve estar hospedado em https://webdrive.almg.gov.br')
    if re.search(r'[^a-zA-Z0-9_\-./]', pasta) or pasta != PASTA_PERMITIDA or arquivo != ARQUIVO_PERMITIDO:
        raise ValueError('Por segurança, este aplicativo somente altera a cópia GDI-GIL/Testes/Controle de Atividades 2026.ods')
    if not re.fullmatch(r'm\d{5,6}', usuario, re.I):
        raise ValueError('WEB_DAV_USUARIO deve conter a matrícula (m + cinco ou seis dígitos).')
    if partes.path == '/remote.php/webdav':
        prefixo = base
    elif partes.path == '/remote.php/dav/files':
        prefixo = base + '/' + quote(usuario, safe='')
    else:
        raise ValueError('WEB_DAV_BASE deve terminar em /remote.php/webdav ou /remote.php/dav/files')
    relativo = '/'.join(quote(item, safe='') for item in (pasta + '/' + arquivo).split('/'))
    return prefixo + '/' + relativo


def sessao():
    s = requests.Session()
    s.auth = (segredo('WEB_DAV_USUARIO'), segredo('WEB_DAV_SENHA_APP'))
    s.headers['User-Agent'] = 'ALMG-ODS-WebDAV-Teste-Real/1.0'
    return s


def baixar_ods():
    url = endereco_ods()
    s = sessao()
    with s.get(url, stream=True, timeout=(15, 120), allow_redirects=False) as resposta:
        if resposta.status_code != 200:
            raise RuntimeError('Leitura WebDAV: HTTP ' + str(resposta.status_code))
        etag = resposta.headers.get('ETag')
        if not etag:
            raise RuntimeError('Servidor não retornou ETag; gravação bloqueada por segurança.')
        buf = io.BytesIO()
        for parte in resposta.iter_content(262144):
            if parte:
                if buf.tell() + len(parte) > MAX_ODS:
                    raise RuntimeError('ODS maior que 30 MB. Ajuste o limite após análise.')
                buf.write(parte)
    conteudo = buf.getvalue()
    abas = nomes_abas(conteudo)
    if 'MODELO' not in abas:
        raise RuntimeError('Aba MODELO ausente no arquivo remoto.')
    return conteudo, etag, abas


def baixar_pdf_administrativo_webdav(data_jornal):
    # Reutiliza a URL já validada e a mesma credencial da planilha.
    prefixo = endereco_ods().rsplit('/', 3)[0]
    nome = original.nome_arquivo_diario_administrativo(data_jornal)
    relativo = '/'.join(quote(x, safe='') for x in (PASTA_DIARIO_ADMINISTRATIVO + '/' + nome).split('/'))
    url = prefixo + '/' + relativo
    with sessao() as s:
        with s.get(url, stream=True, timeout=(15, 120), allow_redirects=False) as r:
            if r.status_code == 404:
                raise FileNotFoundError('Diário Administrativo não encontrado: ' + nome)
            if r.status_code != 200:
                raise RuntimeError('Leitura do Administrativo: HTTP ' + str(r.status_code))
            buf = io.BytesIO()
            for parte in r.iter_content(262144):
                if parte:
                    if buf.tell() + len(parte) > MAX_PDF_ADM:
                        raise RuntimeError('PDF administrativo acima de 100 MB.')
                    buf.write(parte)
    pdf = buf.getvalue()
    if not pdf.startswith(b'%PDF-'):
        raise ValueError('O arquivo administrativo não é um PDF válido.')
    return pdf


def nomes_abas(conteudo):
    if not zipfile.is_zipfile(io.BytesIO(conteudo)):
        raise RuntimeError('O servidor não retornou um arquivo ODS válido.')
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        if z.read('mimetype').decode('ascii', errors='replace').strip() != MIME_ODS:
            raise RuntimeError('O arquivo remoto não é uma planilha ODS.')
        ns = '{urn:oasis:names:tc:opendocument:xmlns:table:1.0}'
        with z.open('content.xml') as xml:
            return [el.attrib.get(ns+'name', '') for _, el in ET.iterparse(xml, events=('start',)) if el.tag == ns+'table']


def gravar_ods(conteudo, etag):
    resposta = sessao().put(endereco_ods(), data=conteudo,
        headers={'If-Match': etag, 'Content-Type': MIME_ODS},
        timeout=(15, 180), allow_redirects=False)
    if resposta.status_code in (409, 412, 423):
        raise RuntimeError('A planilha foi modificada/bloqueada no WebDrive (HTTP %s). Nada foi sobrescrito: faça nova extração.' % resposta.status_code)
    if resposta.status_code not in (200, 201, 204):
        raise RuntimeError('Gravação não confirmada: HTTP ' + str(resposta.status_code))
    return resposta.status_code


def converter_valor(valor):
    """Converte as fórmulas HIPERLINK geradas no código original para UNO."""
    if isinstance(valor, str) and valor.startswith('=HIPERLINK('):
        m = re.fullmatch(r'=HIPERLINK\("((?:[^"]|"")*)";"((?:[^"]|"")*)"\)', valor)
        if m:
            return {'url': m.group(1).replace('""','"'), 'texto': m.group(2).replace('""','"')}
    if pd.isna(valor) if not isinstance(valor, (list, dict)) else False:
        return ''
    return valor


def converter_linhas(linhas):
    return [[converter_valor(v) for v in linha] for linha in linhas]


def linhas_reais(data_str, urls, dfs):
    a, b, c, d, e, f = dfs
    return {
        'executivo': converter_linhas(original.montar_linhas_normas(data_str, a, urls['executivo_html'])),
        'administrativo': converter_linhas(original.montar_linhas_normas(data_str, b, urls['administrativo'], True)),
        'legislativo': converter_linhas(original.montar_linhas_normas(data_str, c, urls['legislativo'], True)),
        'proposicoes': converter_linhas(original.montar_linhas_proposicoes(data_str, d, urls['legislativo'], True)),
        'requerimentos': converter_linhas(original.montar_linhas_requerimentos(data_str, e, urls['legislativo'], True)),
        'pareceres': converter_linhas(original.montar_linhas_pareceres(data_str, f, urls['legislativo'], True)),
        'justica': converter_linhas(original.montar_linhas_normas(data_str, pd.DataFrame(), '')),
    }


def criar_payload(data_jornal, urls, dfs):
    a,b,c,d,e,f = dfs
    qtd_alt = [original.somar_quantidade_vides(x) for x in (a,b,c)]
    return {
        'modelo': 'MODELO',
        'aba': data_jornal.strftime('%d%m'),
        'secoes': linhas_reais(data_jornal.strftime('%d/%m/%Y'), urls, dfs),
        'totais': {
            'normas': sum(original.contar_normas_principais(x) for x in (a,b,c)),
            'alteracoes': sum(x[0] for x in qtd_alt),
            'vides': sum(x[1] for x in qtd_alt),
            'proposicoes': len(d), 'requerimentos': len(e),
            'pareceres': len(f), 'justica': 0,
        }
    }


def preencher_com_libreoffice(ods_origem, payload):
    with tempfile.TemporaryDirectory(prefix='almg_ods_') as temp:
        pasta = Path(temp)
        origem, destino, dados = (pasta/'origem.ods', pasta/'saida.ods', pasta/'payload.json')
        origem.write_bytes(ods_origem)
        dados.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding='utf-8')
        r = subprocess.run(['/usr/bin/python3', str(Path(__file__).with_name('ods_worker.py')),
                            str(origem), str(destino), str(dados)], capture_output=True,
                            text=True, timeout=240)
        if r.returncode or not destino.exists():
            raise RuntimeError('Falha na edição do ODS. ' + (r.stdout+' '+r.stderr)[-1600:])
        return destino.read_bytes()


def corrigir_link_administrativo(ods, aba, url):
    # A correção anterior remove o estilo antigo do Administrativo.
    # Reaplica um link explícito, como já é feito no Executivo.
    T = '{urn:oasis:names:tc:opendocument:xmlns:table:1.0}'
    O = '{urn:oasis:names:tc:opendocument:xmlns:office:1.0}'
    X = '{http://www.w3.org/1999/xlink}'
    TEXT = '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}'
    destino = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(ods)) as z:
        raiz = etree.fromstring(z.read('content.xml'))
        folhas = [f for f in raiz.iter(T+'table') if f.get(T+'name') == aba]
        if len(folhas) != 1:
            raise ValueError('Aba não encontrada para link administrativo.')
        linhas = folhas[0].findall(T+'table-row')
        alvos = []
        for i, linha in enumerate(linhas[:-1]):
            cel = linha.find(T+'table-cell')
            if cel is not None and ''.join(cel.itertext()).strip().upper() == 'DIÁRIO ADMINISTRATIVO':
                alvos.append(linhas[i+1].find(T+'table-cell'))
        if len(alvos) != 1 or alvos[0] is None:
            raise ValueError('Célula de data do Administrativo não encontrada.')
        alvo = alvos[0]
        rotulo = ''.join(alvo.itertext()).strip()
        if not rotulo:
            raise ValueError('Data do Administrativo vazia.')
        alvo.attrib.pop(T+'formula', None)
        for atributo in ('value', 'date-value', 'string-value', 'time-value', 'boolean-value'):
            alvo.attrib.pop(O+atributo, None)
        alvo.set(O+'value-type', 'string')
        for filho in list(alvo):
            alvo.remove(filho)
        ancora = etree.SubElement(etree.SubElement(alvo, TEXT+'p'), TEXT+'a')
        ancora.set(X+'type', 'simple')
        ancora.set(X+'href', url)
        ancora.text = rotulo
        xml = etree.tostring(raiz, encoding='UTF-8', xml_declaration=True)
        with zipfile.ZipFile(destino, 'w') as out:
            for info in z.infolist():
                out.writestr(info, xml if info.filename == 'content.xml' else z.read(info.filename))
    return destino.getvalue()


def listar_indisponiveis_webdrive(data_ref):
    # Consulta somente leitura. Falhas nunca equivalem a calendario vazio.
    import caldav
    tz = ZoneInfo('America/Sao_Paulo')
    inicio = datetime.combine(data_ref, datetime.min.time(), tzinfo=tz)
    fim = inicio + timedelta(days=1)
    indisponiveis = set()
    nao_reconhecidos = []
    def instante(valor):
        if isinstance(valor, datetime):
            return valor.replace(tzinfo=tz) if valor.tzinfo is None else valor.astimezone(tz)
        return datetime.combine(valor, datetime.min.time(), tzinfo=tz)
    with caldav.DAVClient(url=CALDAV_URL, username=segredo('WEB_DAV_USUARIO'),
                          password=segredo('WEB_DAV_SENHA_APP'), timeout=30) as client:
        calendarios = client.principal().calendars()
        encontrados = [c for c in calendarios if str(c.name or '').strip() == CALENDARIO_AFASTAMENTOS]
        if len(encontrados) != 1:
            raise RuntimeError('Calendário de afastamentos não encontrado ou duplicado. Confira o nome e as permissões.')
        eventos = encontrados[0].search(start=inicio, end=fim, event=True, expand=True)
        for evento in eventos:
            for comp in evento.icalendar_instance.walk('VEVENT'):
                if str(comp.get('STATUS', '')).upper() == 'CANCELLED':
                    continue
                titulo = str(comp.get('SUMMARY', '')).strip()
                m = original.PADRAO_AFASTAMENTO.match(titulo)
                if not m:
                    continue
                bruto = comp.decoded('DTSTART')
                ini = instante(bruto)
                if 'DTEND' in comp:
                    termino = instante(comp.decoded('DTEND'))
                elif 'DURATION' in comp:
                    termino = ini + comp.decoded('DURATION')
                else:
                    termino = ini if isinstance(bruto, datetime) else ini + timedelta(days=1)
                if not (ini < fim and (termino > inicio or (termino == ini and inicio <= ini < fim))):
                    continue
                nome = re.sub(r'\s*\(.*?\)\s*$', '', m.group(2)).strip()
                chave = original.normalizar_nome_chave(nome)
                mapa = original.MAPA_NOMES_EQUIPE
                pessoa = mapa.get(chave) or mapa.get(chave.split()[0] if chave.split() else '')
                if pessoa:
                    indisponiveis.add(pessoa)
                else:
                    nao_reconhecidos.append(nome)
    return indisponiveis, sorted(set(nao_reconhecidos))


def extrair_data_real(data_jornal, data_trabalho=None):
    texto_data = data_jornal.strftime('%d/%m/%Y')
    urls = original.montar_urls(original.preparar_datas(texto_data))
    erros = {}
    resultados = {}
    dfs = [pd.DataFrame() for _ in range(6)]
    # Igual ao aplicativo original: Diário do Executivo via Playwright e API.
    try:
        original.garantir_playwright_chromium()
        pdf = original.baixar_pdf_jornal_mg_por_link(urls['executivo_html'])
        df = original.ExecutiveProcessor(pdf).process_pdf()
        if df is None: df = pd.DataFrame()
        if not df.empty:
            df = df.copy()
            df['Ano'] = df['Sanção'].fillna('').astype(str).str[-4:] if 'Sanção' in df.columns else ''
        dfs[0] = df
        resultados['Executivo'] = 'OK — %d registros' % len(df)
    except Exception as e:
        erros['Executivo'] = '%s: %s' % (type(e).__name__, e)
    try:
        pdf = original.baixar(urls['legislativo'])
        dados = original.LegislativeProcessor(pdf).process_all()
        dfs[2] = dados['Normas'].rename(columns={'Sigla':'Tipo'}).copy()
        dfs[3] = dados['Proposicoes'].rename(columns={'Sigla':'Tipo', 'Categoria':'Observação'}).copy()
        dfs[4] = dados['Requerimentos'].rename(columns={'Sigla':'Tipo', 'Classificação':'Observação'}).copy()
        dfs[5] = dados['Pareceres'].rename(columns={'Sigla':'Tipo', 'Tipo':'Subtipo'}).copy()
        resultados['Legislativo'] = 'OK — %d normas, %d proposições, %d requerimentos, %d pareceres' % tuple(len(dfs[i]) for i in (2,3,4,5))
    except Exception as e:
        erros['Legislativo'] = '%s: %s' % (type(e).__name__, e)
    try:
        pdf_adm = baixar_pdf_administrativo_webdav(data_jornal)
        df_adm = original.AdministrativeProcessor(pdf_adm).process_pdf()
        if df_adm is None:
            raise RuntimeError('Falha no processamento do PDF administrativo.')
        dfs[1] = df_adm.rename(columns={'Sigla': 'Tipo'}).copy()
        resultados['Administrativo'] = 'OK — %d registros' % len(df_adm)
    except Exception as e:
        erros['Administrativo'] = '%s: %s' % (type(e).__name__, e)
    # Usa a data da execução das atividades, não a data de publicação.
    if not erros:
        try:
            data_trabalho = data_trabalho or datetime.now(ZoneInfo('America/Sao_Paulo')).date()
            indisponiveis, desconhecidos = listar_indisponiveis_webdrive(data_trabalho)
            if desconhecidos:
                st.warning('Nomes de afastamentos não reconhecidos: ' + ', '.join(desconhecidos))
            avisos = original.validar_pools_distribuicao(indisponiveis)
            if avisos:
                raise RuntimeError(' '.join(avisos))
            resultados['Calendário'] = 'OK — %s — %s' % (data_trabalho.strftime('%d/%m/%Y'), ', '.join(sorted(indisponiveis)) or 'Nenhum integrante afastado')
            dfs = original.distribuir_tarefas_extraidas_em_blocos(*dfs, indisponiveis=indisponiveis)
        except Exception as e:
            erros['Calendário'] = '%s: %s' % (type(e).__name__, e)
    return urls, dfs, resultados, erros


def criar_relatorio_csv(dfs):
    nomes = ['normas_executivo','normas_administrativo','normas_legislativo','proposicoes','requerimentos','pareceres']
    saida = io.BytesIO()
    with zipfile_archive.ZipFile(saida, mode='w', compression=zipfile_archive.ZIP_DEFLATED) as zip_out:
        for nome, df in zip(nomes, dfs):
            zip_out.writestr(nome + '.csv', df.to_csv(index=False, sep=';').encode('utf-8-sig'))
    return saida.getvalue()


def tabela_resumo(dfs):
    nomes = ['Normas Executivo','Normas Administrativo','Normas Legislativo','Proposições','Requerimentos','Pareceres']
    return pd.DataFrame({'Seção': nomes, 'Registros': [len(x) for x in dfs]})



def executar_interface():
    hoje = datetime.now(ZoneInfo('America/Sao_Paulo')).date()
    st.caption('Selecione a data de trabalho')
    data_trabalho = st.date_input('Data', value=hoje, format='DD/MM/YYYY',
                                  max_value=hoje, key='webdrive_data_jornal')
    jornal = original.ajustar_data_operacional(data_trabalho)
    aba = jornal.strftime('%d%m')
    if jornal != data_trabalho:
        st.info(f"Diário a processar: {jornal:%d/%m/%Y}. Afastamentos considerados: {data_trabalho:%d/%m/%Y}.")
    if str(st.secrets.get('ENABLE_REMOTE_WRITE', 'false')).lower() != 'true':
        st.warning('Gravação no WebDrive desativada pelo administrador.')
        return
    if st.button('Processar', type='primary', use_container_width=True, key='webdrive_processar'):
        try:
            with st.spinner('Verificando a planilha do WebDrive...'):
                origem, etag, abas_anteriores = baixar_ods()
            if aba in abas_anteriores:
                st.info(f'A aba {aba} já existe. Nenhuma alteração foi realizada.')
                return
            with st.spinner('Extraindo os diários e distribuindo as tarefas...'):
                urls, dfs, resultados, erros = extrair_data_real(jornal, data_trabalho)
            if erros:
                for fonte, mensagem in erros.items():
                    st.error(f'{fonte}: {mensagem}')
                st.error('Processamento incompleto. A planilha não foi alterada.')
                return
            payload = criar_payload(jornal, urls, dfs)
            with st.spinner('Preenchendo a planilha do WebDrive...'):
                saida = preencher_com_libreoffice(origem, payload)
                saida = corrigir_links_aba(saida, aba, urls)
                saida = corrigir_link_administrativo(saida, aba, urls['administrativo'])
                abas_depois = nomes_abas(saida)
                if (len(abas_depois) != len(abas_anteriores)+1 or abas_depois[1] != aba
                        or set(abas_depois) != set(abas_anteriores) | {aba}):
                    raise RuntimeError('Validação do ODS falhou: abas originais não foram preservadas corretamente.')
                codigo = gravar_ods(saida, etag)
                try:
                    remoto, _, abas_remotas = baixar_ods()
                except Exception:
                    st.warning(f'Envio aceito (HTTP {codigo}), mas a conferência remota falhou. Verifique a planilha antes de repetir.')
                    return
            if aba not in abas_remotas:
                st.error('Envio aceito, mas a nova aba não foi localizada. Confira a planilha antes de repetir.')
                return
            if hashlib.sha256(remoto).hexdigest() != hashlib.sha256(saida).hexdigest():
                st.warning('A nova aba existe, mas o arquivo remoto difere do enviado. Confira seu conteúdo.')
            else:
                st.success(f'Planilha do WebDrive preenchida com sucesso. Aba {aba}.')
            st.dataframe(tabela_resumo(dfs), hide_index=True)
        except Exception as ex:
            st.error(f'{type(ex).__name__}: {ex}')
