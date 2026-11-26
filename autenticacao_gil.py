"""Controle de acesso antes de qualquer funcionalidade operacional."""
import hmac
import re
import streamlit as st


def exigir_acesso():
    modo = str(st.secrets.get('AUTH_MODE', 'senha_temporaria')).strip().lower()
    if modo == 'senha_temporaria':
        senha = str(st.secrets.get('SENHA_TESTE_STREAMLIT', ''))
        if not senha:
            st.error('Configure a senha temporária nos Secrets.')
            st.stop()
        chave = st.text_input('Senha temporária do aplicativo', type='password', key='gil_senha_temporaria')
        if not chave or not hmac.compare_digest(chave.encode('utf-8'), senha.encode('utf-8')):
            st.info('Informe a senha temporária para continuar.')
            st.stop()
        return
    if modo != 'oidc':
        st.error('Modo de autenticação inválido. Consulte o administrador.')
        st.stop()
    matriculas = st.secrets.get('MATRICULAS_AUTORIZADAS', [])
    if not isinstance(matriculas, (list, tuple)) or not matriculas:
        st.error('Lista de matrículas autorizadas não configurada.')
        st.stop()
    autorizados = {f'{str(m).strip().lower()}@almg.gov.br' for m in matriculas if re.fullmatch(r'm\d{4,5}', str(m).strip().lower())}
    if not hasattr(st, 'login') or not hasattr(st, 'user'):
        st.error('Atualize o Streamlit e instale o Authlib.')
        st.stop()
    if not st.user.is_logged_in:
        st.title('Assistente Virtual da GIL')
        st.caption('Gerência de Informação Legislativa – GIL/GDI')
        st.info('Acesso exclusivo à equipe autorizada. Utilize sua matrícula ou e-mail institucional e a senha do aplicativo. No primeiro acesso, escolha a opção de cadastro na tela de autenticação.')
        if st.button('Entrar / Primeiro acesso', type='primary'):
            st.login('auth0')
        st.stop()
    email = str(st.user.get('email', '')).strip().lower()
    if email not in autorizados or st.user.get('email_verified') is not True:
        st.error('Acesso não autorizado ou e-mail institucional não confirmado.')
        if st.button('Sair e tentar novamente'):
            st.logout()
        st.stop()
    with st.sidebar:
        st.caption(f'Conectado: {email.split("@")[0]}')
        if st.button('Sair', key='gil_oidc_logout'):
            st.logout()
            st.stop()
