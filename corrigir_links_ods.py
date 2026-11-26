# -*- coding: utf-8 -*-
"""Atualiza links das edicoes em uma aba ODS, preservando as demais abas.

Os links herdados de MODELO estao em estilos da celula. Em especial, para
Executivo gravamos tambem um text:a explicito, que e interpretado pelos
editores ODF sem precisar avaliar a formula HYPERLINK sobre uma URL JSON.
"""
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile
from urllib.parse import quote, urlsplit, urlunsplit, parse_qs
from lxml import etree

NS_T = 'urn:oasis:names:tc:opendocument:xmlns:table:1.0'
NS_S = 'urn:oasis:names:tc:opendocument:xmlns:style:1.0'
NS_X = 'http://www.w3.org/1999/xlink'
NS_O = 'urn:oasis:names:tc:opendocument:xmlns:office:1.0'
NS_TEXT = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'


def normalizar_url_executivo(url: str) -> str:
    """Codifica completamente o JSON do parametro dados na URL do diario."""
    partes = urlsplit(url)
    if partes.scheme != 'https' or partes.netloc != 'www.jornalminasgerais.mg.gov.br' or partes.path != '/edicao-do-dia':
        raise ValueError('URL do Diario do Executivo fora do dominio/caminho previsto')
    qs = parse_qs(partes.query, keep_blank_values=True)
    valores = qs.get('dados', [])
    if len(valores) != 1:
        raise ValueError('URL do Executivo sem parametro dados valido')
    import json
    dados = json.loads(valores[0])
    if 'dataPublicacaoSelecionada' not in dados:
        raise ValueError('URL do Executivo sem data de publicacao')
    parametro = quote(json.dumps({'dataPublicacaoSelecionada': dados['dataPublicacaoSelecionada']},
                                 separators=(',', ':'), ensure_ascii=False), safe='')
    return urlunsplit((partes.scheme, partes.netloc, partes.path, 'dados=' + parametro, ''))


def corrigir_links_aba(ods: bytes, aba: str, urls: dict) -> bytes:
    executivo = normalizar_url_executivo(urls['executivo_html'])
    entrada = BytesIO(ods)
    with ZipFile(entrada) as z:
        raiz = etree.fromstring(z.read('content.xml'))
        auto = raiz.find('.//{%s}automatic-styles' % NS_O)
        if auto is None:
            raise ValueError('Estilos automaticos nao encontrados no ODS')
        folhas = raiz.xpath('.//t:table[@t:name=$nome]', namespaces={'t': NS_T}, nome=aba)
        if len(folhas) != 1:
            raise ValueError('Aba %s nao encontrada (ou duplicada)' % aba)
        folha = folhas[0]
        estilos = {
            el.get('{%s}name' % NS_S): el
            for el in raiz.xpath('.//s:style[@s:family="table-cell"]', namespaces={'s': NS_S})
        }
        atuais = set(estilos)
        mapa = {}
        alteradas = 0
        executivos = 0
        for cel in folha.iter():
            if cel.tag != '{%s}table-cell' % NS_T:
                continue
            antigo = cel.get('{%s}style-name' % NS_T)
            if not antigo or antigo not in estilos:
                continue
            estilo = estilos[antigo]
            links = estilo.xpath('./s:table-cell-properties/s:hyperlink', namespaces={'s': NS_S})
            if not links:
                continue
            href = links[0].get('{%s}href' % NS_X, '')
            is_executivo = 'jornalminasgerais.mg.gov.br' in href
            if is_executivo:
                novo_href = executivo
            elif 'diariolegislativo.almg.gov.br' in href:
                novo_href = urls['legislativo']
            elif 'intra.almg.gov.br' in href and 'diario-administrativo' in href:
                novo_href = None
            else:
                continue
            chave = (antigo, novo_href)
            if chave not in mapa:
                novo = deepcopy(estilo)
                i = 1
                nome = 'almg_%s_link_%d' % (aba, i)
                while nome in atuais:
                    i += 1
                    nome = 'almg_%s_link_%d' % (aba, i)
                atuais.add(nome)
                novo.set('{%s}name' % NS_S, nome)
                hiper = novo.xpath('./s:table-cell-properties/s:hyperlink', namespaces={'s': NS_S})
                for link in hiper:
                    if novo_href:
                        link.set('{%s}href' % NS_X, novo_href)
                    else:
                        link.getparent().remove(link)
                auto.append(novo)
                mapa[chave] = nome
            cel.set('{%s}style-name' % NS_T, mapa[chave])
            alteradas += 1
        # O LibreOffice pode converter a celula preenchida em um NOVO estilo
        # sem style:hyperlink. Portanto, a data do Executivo e localizada pelo
        # cabecalho da secao, e nao pelo estilo herdado da aba MODELO.
        linhas = folha.findall('{%s}table-row' % NS_T)
        achou = False
        for i, linha in enumerate(linhas[:-1]):
            primeira = linha.find('{%s}table-cell' % NS_T)
            if primeira is None:
                continue
            if ''.join(primeira.itertext()).strip().upper() != 'DIÁRIO DO EXECUTIVO':
                continue
            alvo = linhas[i + 1].find('{%s}table-cell' % NS_T)
            if alvo is None:
                raise ValueError('Celula de data do Executivo ausente')
            rotulo = ''.join(alvo.itertext()).strip()
            if not rotulo:
                raise ValueError('Rotulo da data do Executivo vazio')
            alvo.attrib.pop('{%s}formula' % NS_T, None)
            alvo.set('{%s}value-type' % NS_O, 'string')
            for filho in list(alvo):
                alvo.remove(filho)
            paragrafo = etree.SubElement(alvo, '{%s}p' % NS_TEXT)
            ancora = etree.SubElement(paragrafo, '{%s}a' % NS_TEXT)
            ancora.set('{%s}type' % NS_X, 'simple')
            ancora.set('{%s}href' % NS_X, executivo)
            ancora.text = rotulo
            executivos += 1
            achou = True
            break
        if not achou:
            raise ValueError('Cabecalho DIARIO DO EXECUTIVO nao encontrado')
        if not alteradas or executivos != 1:
            raise ValueError('Links da nova aba incompletos (total=%d, Executivo=%d)' % (alteradas, executivos))
        xml = etree.tostring(raiz, encoding='UTF-8', xml_declaration=True)
        destino = BytesIO()
        with ZipFile(destino, 'w') as out:
            for info in z.infolist():
                out.writestr(info, xml if info.filename == 'content.xml' else z.read(info.filename))
    return destino.getvalue()
