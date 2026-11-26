# -*- coding: utf-8 -*-
"""Operacoes ODS pelo LibreOffice UNO (executar com /usr/bin/python3)."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def executar(entrada, saida, payload):
    import uno
    from com.sun.star.beans import PropertyValue

    def prop(nome, valor):
        p = PropertyValue()
        p.Name, p.Value = nome, valor
        return p

    def fileurl(p):
        return uno.systemPathToFileUrl(str(Path(p).resolve()))

    # Perfil de usuario exclusivo: evita interagir com uma sessao do LibreOffice alheia.
    with tempfile.TemporaryDirectory(prefix='lo_ods_') as perfil:
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        porta = sock.getsockname()[1]
        sock.close()
        processo = subprocess.Popen([
            'soffice', '-env:UserInstallation=' + fileurl(perfil),
            '--headless', '--norestore', '--nodefault', '--nofirststartwizard',
            '--accept=socket,host=127.0.0.1,port=%d;urp;StarOffice.ComponentContext' % porta,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        desktop = None
        doc = None
        try:
            local_ctx = uno.getComponentContext()
            resolver = local_ctx.ServiceManager.createInstanceWithContext('com.sun.star.bridge.UnoUrlResolver', local_ctx)
            ultimo = None
            for _ in range(60):
                try:
                    ctx = resolver.resolve('uno:socket,host=127.0.0.1,port=%d;urp;StarOffice.ComponentContext' % porta)
                    sm = ctx.ServiceManager
                    desktop = sm.createInstanceWithContext('com.sun.star.frame.Desktop', ctx)
                    break
                except Exception as e:
                    ultimo = e
                    if processo.poll() is not None:
                        raise RuntimeError('LibreOffice encerrou antes de iniciar.')
                    time.sleep(.15)
            if desktop is None:
                raise RuntimeError('Nao foi possivel iniciar LibreOffice: %s' % ultimo)

            doc = desktop.loadComponentFromURL(fileurl(entrada), '_blank', 0,
                (prop('Hidden', True), prop('ReadOnly', False), prop('UpdateDocMode', 0)))
            if doc is None:
                raise RuntimeError('Falha ao abrir arquivo ODS. Confira se e valido.')
            sheets = doc.getSheets()
            modelo = payload.get('modelo', 'MODELO')
            aba = payload['aba']
            nomes = list(sheets.getElementNames())
            if aba in nomes:
                raise ValueError('Ja existe aba para esta data; nada foi gravado.')
            if modelo not in nomes:
                raise ValueError('Aba MODELO nao encontrada: abas=' + ', '.join(nomes))
            sheets.copyByName(modelo, aba, 1)
            sh = sheets.getByName(aba)

            def achar(texto, ocorrencia=1, obrigatorio=True):
                alvo = texto.strip().upper()
                n = 0
                # 10000 linhas e suficiente para esta planilha diaria.
                for i in range(10000):
                    v = sh.getCellByPosition(0, i).getString().strip().upper()
                    if v == alvo:
                        n += 1
                        if n == ocorrencia:
                            return i
                if obrigatorio:
                    raise ValueError('Marcador nao encontrado na coluna A: %s (ocorrencia %d)' % (texto, ocorrencia))
                return None

            def escreve(celula, valor):
                if isinstance(valor, dict) and valor.get('url'):
                    url = str(valor['url']).replace('"', '""')
                    rotulo = str(valor.get('texto', '')).replace('"', '""')
                    celula.setFormula('=HYPERLINK("%s";"%s")' % (url, rotulo))
                elif isinstance(valor, (int, float)) and not isinstance(valor, bool):
                    celula.setValue(float(valor))
                else:
                    celula.setString(str(valor if valor is not None else ''))

            def inserir_bloco(marcador, linhas, mesclar_linhas_cols=None, cols_responsaveis=()):
                pos = achar(marcador) + 1
                if not linhas:
                    return
                # Nao replicamos linhas iniciais: a primeira ja existe no MODELO.
                excedente = len(linhas) - 1
                if excedente > 0:
                    sh.getRows().insertByIndex(pos + 1, excedente)
                for i, linha in enumerate(linhas):
                    for col, val in enumerate(linha):
                        escreve(sh.getCellByPosition(col, pos + i), val)
                    # O layout das seções de proposicoes usa espaco ate a coluna O.
                    if mesclar_linhas_cols:
                        ci, cf = mesclar_linhas_cols
                        rng = sh.getCellRangeByPosition(ci - 1, pos+i, cf - 1, pos+i)
                        try:
                            rng.merge(False)
                        except Exception:
                            pass
                        rng.merge(True)
                    for c in cols_responsaveis:
                        if c <= len(linha) and str(linha[c - 1]).strip() not in ('', '-'):
                            cell = sh.getCellByPosition(c-1, pos+i)
                            cell.CharColor = 0xD00000
                            cell.CharWeight = 150.0
                # Coluna A: data mostrada uma vez para todo o bloco.
                if len(linhas) > 1:
                    rng = sh.getCellRangeByPosition(0, pos, 0, pos+len(linhas)-1)
                    try:
                        rng.merge(False)
                    except Exception:
                        pass
                    rng.merge(True)
                return pos

            # Layout conferido no Controle de Atividades 2026.ods.
            # O MODELO usa quatro digitos DDMM e posicoes fixas, nao marcadores
            # PROPOSICOES/REQUERIMENTOS/PARECERES na coluna A.
            # Inserir de baixo para cima conserva as posicoes do MODELO.
            secoes = [
                (20, 'pareceres', (8,15), (6,7)),
                (17, 'requerimentos', (7,15), (5,6)),
                (14, 'proposicoes', (7,15), (5,6)),
                (10, 'legislativo', None, (7,8,12,13,14,15)),
                (8, 'administrativo', None, (7,8,12,13,14,15)),
                (6, 'justica', None, (7,8,12,13,14,15)),
                (4, 'executivo', None, (7,8,12,13,14,15)),
            ]
            for pos_original, chave, merged, responsaveis in secoes:
                linhas = payload['secoes'][chave]
                if not linhas:
                    continue
                excedente = len(linhas) - 1
                if excedente:
                    sh.getRows().insertByIndex(pos_original + 1, excedente)
                for i, linha in enumerate(linhas):
                    for col, val in enumerate(linha):
                        escreve(sh.getCellByPosition(col, pos_original + i), val)
                    if merged:
                        ci, cf = merged
                        rng = sh.getCellRangeByPosition(ci-1, pos_original+i, cf-1, pos_original+i)
                        try: rng.merge(False)
                        except Exception: pass
                        rng.merge(True)
                    for c in responsaveis:
                        if c <= len(linha) and str(linha[c-1]).strip() not in ('','-'):
                            cell = sh.getCellByPosition(c-1, pos_original+i)
                            cell.CharColor = 0xD00000
                            cell.CharWeight = 150.0
                if len(linhas)>1:
                    rng=sh.getCellRangeByPosition(0,pos_original,0,pos_original+len(linhas)-1)
                    try: rng.merge(False)
                    except Exception: pass
                    rng.merge(True)

            totais = payload.get('totais', {})
            for ocorrencia, coluna, chave in [
                (1, 6, 'normas'), (1, 9, 'alteracoes'), (1, 11, 'vides'),
                (2, 3, 'proposicoes'), (3, 3, 'requerimentos'),
                (4, 3, 'pareceres'), (5, 3, 'justica'),
            ]:
                linha = achar('TOTAL', ocorrencia, obrigatorio=False)
                if linha is not None and chave in totais:
                    escreve(sh.getCellByPosition(coluna-1, linha), totais[chave])
            doc.calculateAll()
            doc.storeAsURL(fileurl(saida), (prop('FilterName', 'calc8'), prop('Overwrite', True)))
            print(json.dumps({'ok': True, 'aba': aba, 'arquivo': saida}, ensure_ascii=False))
        finally:
            if doc:
                try: doc.close(True)
                except Exception: pass
            try: processo.terminate(); processo.wait(timeout=5)
            except Exception: processo.kill()


if __name__ == '__main__':
    try:
        with open(sys.argv[3], encoding='utf-8') as f:
            dados = json.load(f)
        executar(sys.argv[1], sys.argv[2], dados)
    except Exception as e:
        print(json.dumps({'ok': False, 'erro': str(e)}, ensure_ascii=False))
        sys.exit(1)
