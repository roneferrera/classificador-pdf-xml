import streamlit as st
import zipfile
import io
import re
import os
import xml.etree.ElementTree as ET
from PyPDF2 import PdfReader

# ─────────────────────────────────────────────
# CONFIGURAÇÕES
# ─────────────────────────────────────────────
st.set_page_config(page_title="Classificador NF-e", layout="wide", page_icon="📂")

CATEGORIAS = ["Consumo", "Revenda", "Ativo Imobilizado", "Serviços", "Outros"]

NAMESPACE_NFE = "http://www.portalfiscal.inf.br/nfe"

# ─────────────────────────────────────────────
# FUNÇÕES AUXILIARES
# ─────────────────────────────────────────────

def extrair_chave_pdf(pdf_file) -> str:
    """Extrai a chave de acesso (44 dígitos) do texto do PDF."""
    try:
        reader = PdfReader(pdf_file)
        texto_completo = ""
        for page in reader.pages:
            texto_completo += page.extract_text() or ""

        # A chave de acesso tem 44 dígitos numéricos seguidos
        # No DANFE ela aparece como blocos de 4 dígitos separados por espaço (11 blocos)
        # Tentamos os dois formatos
        
        # Formato 1: 44 dígitos contínuos
        match = re.search(r'\b(\d{44})\b', texto_completo)
        if match:
            return match.group(1)

        # Formato 2: blocos "XXXX XXXX XXXX ... " (11 grupos de 4 dígitos)
        match = re.search(r'(\d{4}\s){10}\d{4}', texto_completo)
        if match:
            return re.sub(r'\s+', '', match.group(0))

        return None
    except Exception as e:
        return None


def extrair_chave_xml(xml_file) -> str:
    """Extrai a chave de acesso do XML da NF-e a partir do atributo Id ou tag chNFe."""
    try:
        content = xml_file.read()
        xml_file.seek(0)
        root = ET.fromstring(content)

        # Tenta pelo atributo Id da infNFe (ex: Id="NFe35...")
        ns = {"nfe": NAMESPACE_NFE}
        inf_nfe = root.find(".//{%s}infNFe" % NAMESPACE_NFE)
        if inf_nfe is not None:
            id_attr = inf_nfe.get("Id", "")
            chave = re.sub(r'\D', '', id_attr)
            if len(chave) == 44:
                return chave

        # Tenta pela tag chNFe (dentro de infProt)
        ch_nfe = root.find(".//{%s}chNFe" % NAMESPACE_NFE)
        if ch_nfe is not None and ch_nfe.text:
            chave = re.sub(r'\D', '', ch_nfe.text)
            if len(chave) == 44:
                return chave

        return None
    except Exception:
        return None


def extrair_info_xml(xml_file) -> dict:
    """Extrai informações relevantes do XML para exibição."""
    info = {"numero": "N/A", "emitente": "N/A", "valor": "N/A", "data": "N/A", "cfop": "N/A"}
    try:
        content = xml_file.read()
        xml_file.seek(0)
        root = ET.fromstring(content)

        # Número da NF-e
        nNF = root.find(".//{%s}nNF" % NAMESPACE_NFE)
        if nNF is not None:
            info["numero"] = nNF.text

        # Emitente
        xNome = root.find(".//{%s}emit/{%s}xNome" % (NAMESPACE_NFE, NAMESPACE_NFE))
        if xNome is None:
            xNome = root.find(".//{%s}xNome" % NAMESPACE_NFE)
        if xNome is not None:
            info["emitente"] = xNome.text

        # Valor total
        vNF = root.find(".//{%s}vNF" % NAMESPACE_NFE)
        if vNF is not None:
            info["valor"] = f"R$ {float(vNF.text):,.2f}"

        # Data de emissão
        dhEmi = root.find(".//{%s}dhEmi" % NAMESPACE_NFE)
        if dhEmi is None:
            dhEmi = root.find(".//{%s}dEmi" % NAMESPACE_NFE)
        if dhEmi is not None:
            info["data"] = dhEmi.text[:10] if dhEmi.text else "N/A"

        # CFOP (primeiro item)
        cfop = root.find(".//{%s}CFOP" % NAMESPACE_NFE)
        if cfop is not None:
            info["cfop"] = cfop.text

    except Exception:
        pass
    return info


def gerar_zip(notas_classificadas: dict, xmls_dict: dict) -> bytes:
    """
    Gera um arquivo ZIP com os XMLs organizados por pasta/categoria.
    notas_classificadas: {chave: categoria}
    xmls_dict: {chave: (nome_arquivo, conteudo_bytes)}
    """
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for chave, categoria in notas_classificadas.items():
            if chave in xmls_dict:
                nome_arquivo, conteudo = xmls_dict[chave]
                caminho_no_zip = f"{categoria}/{nome_arquivo}"
                zf.writestr(caminho_no_zip, conteudo)
    zip_buffer.seek(0)
    return zip_buffer.read()


# ─────────────────────────────────────────────
# INTERFACE STREAMLIT
# ─────────────────────────────────────────────

st.title("📂 Classificador de NF-e por Categoria")
st.markdown("Faça o upload dos **PDFs (DANFEs)** e dos **XMLs** das notas fiscais. O sistema irá cruzar as chaves de acesso e permitir a classificação por categoria.")

# Inicializa estado da sessão
if "notas" not in st.session_state:
    st.session_state.notas = {}       # {chave: {info, categoria, xml_nome, xml_bytes, pdf_nome, status}}
if "processado" not in st.session_state:
    st.session_state.processado = False

# ─── PASSO 1: Upload ───────────────────────────────────────────────────────────
st.divider()
col1, col2 = st.columns(2)

with col1:
    st.subheader("📄 1. Upload dos PDFs (DANFEs)")
    pdfs_uploaded = st.file_uploader(
        "Selecione os arquivos PDF",
        type=["pdf"],
        accept_multiple_files=True,
        key="upload_pdfs"
    )

with col2:
    st.subheader("🗂️ 2. Upload dos XMLs das NF-es")
    xmls_uploaded = st.file_uploader(
        "Selecione os arquivos XML",
        type=["xml"],
        accept_multiple_files=True,
        key="upload_xmls"
    )

# ─── PASSO 2: Processar ────────────────────────────────────────────────────────
st.divider()
if st.button("🔍 Processar e Cruzar Arquivos", type="primary", use_container_width=True):
    if not pdfs_uploaded and not xmls_uploaded:
        st.warning("Faça o upload de pelo menos PDFs ou XMLs.")
    else:
        notas = {}

        # Processa XMLs primeiro → dicionário {chave: (nome, bytes, info)}
        xmls_por_chave = {}
        with st.spinner("Lendo XMLs..."):
            for xml_file in (xmls_uploaded or []):
                xml_file.seek(0)
                chave = extrair_chave_xml(xml_file)
                xml_file.seek(0)
                info = extrair_info_xml(xml_file)
                xml_file.seek(0)
                conteudo = xml_file.read()

                if chave:
                    xmls_por_chave[chave] = {
                        "nome": xml_file.name,
                        "bytes": conteudo,
                        "info": info
                    }
                else:
                    st.warning(f"⚠️ Não foi possível extrair chave do XML: {xml_file.name}")

        # Processa PDFs → extrai chave e cruza com XML
        with st.spinner("Lendo PDFs e cruzando chaves..."):
            chaves_encontradas_pdf = set()
            for pdf_file in (pdfs_uploaded or []):
                pdf_file.seek(0)
                chave = extrair_chave_pdf(pdf_file)

                if chave:
                    chaves_encontradas_pdf.add(chave)
                    if chave in xmls_por_chave:
                        notas[chave] = {
                            "pdf_nome": pdf_file.name,
                            "xml_nome": xmls_por_chave[chave]["nome"],
                            "xml_bytes": xmls_por_chave[chave]["bytes"],
                            "info": xmls_por_chave[chave]["info"],
                            "categoria": CATEGORIAS[0],
                            "status": "✅ Cruzado"
                        }
                    else:
                        notas[chave] = {
                            "pdf_nome": pdf_file.name,
                            "xml_nome": None,
                            "xml_bytes": None,
                            "info": {"numero": "N/A", "emitente": "N/A", "valor": "N/A", "data": "N/A", "cfop": "N/A"},
                            "categoria": CATEGORIAS[0],
                            "status": "⚠️ XML não encontrado"
                        }
                else:
                    notas[f"sem_chave_{pdf_file.name}"] = {
                        "pdf_nome": pdf_file.name,
                        "xml_nome": None,
                        "xml_bytes": None,
                        "info": {"numero": "N/A", "emitente": "N/A", "valor": "N/A", "data": "N/A", "cfop": "N/A"},
                        "categoria": CATEGORIAS[0],
                        "status": "❌ Chave não extraída do PDF"
                    }

            # XMLs que não tiveram PDF correspondente
            for chave, dados in xmls_por_chave.items():
                if chave not in chaves_encontradas_pdf:
                    notas[chave] = {
                        "pdf_nome": None,
                        "xml_nome": dados["nome"],
                        "xml_bytes": dados["bytes"],
                        "info": dados["info"],
                        "categoria": CATEGORIAS[0],
                        "status": "⚠️ PDF não encontrado"
                    }

        st.session_state.notas = notas
        st.session_state.processado = True
        st.success(f"✅ Processamento concluído! {len(notas)} nota(s) encontrada(s).")

# ─── PASSO 3: Classificação ────────────────────────────────────────────────────
if st.session_state.processado and st.session_state.notas:
    st.divider()
    st.subheader("🏷️ 3. Classificar as Notas por Categoria")

    # Filtro por status
    filtro = st.selectbox("Filtrar por status:", ["Todos", "✅ Cruzado", "⚠️ XML não encontrado", "⚠️ PDF não encontrado", "❌ Chave não extraída do PDF"])

    # Cabeçalho da tabela
    header_cols = st.columns([2, 2, 2, 1.5, 1.5, 2, 2])
    header_cols[0].markdown("**PDF**")
    header_cols[1].markdown("**XML**")
    header_cols[2].markdown("**Emitente**")
    header_cols[3].markdown("**Nº NF**")
    header_cols[4].markdown("**Valor**")
    header_cols[5].markdown("**Categoria**")
    header_cols[6].markdown("**Status**")
    st.divider()

    notas_atualizadas = {}
    for chave, dados in st.session_state.notas.items():
        if filtro != "Todos" and dados["status"] != filtro:
            notas_atualizadas[chave] = dados
            continue

        cols = st.columns([2, 2, 2, 1.5, 1.5, 2, 2])
        cols[0].caption(dados["pdf_nome"] or "—")
        cols[1].caption(dados["xml_nome"] or "—")
        cols[2].caption(dados["info"]["emitente"][:25] if dados["info"]["emitente"] != "N/A" else "N/A")
        cols[3].caption(dados["info"]["numero"])
        cols[4].caption(dados["info"]["valor"])

        # Selectbox para categoria
        idx_atual = CATEGORIAS.index(dados["categoria"]) if dados["categoria"] in CATEGORIAS else 0
        nova_cat = cols[5].selectbox(
            label="",
            options=CATEGORIAS,
            index=idx_atual,
            key=f"cat_{chave}",
            label_visibility="collapsed"
        )
        dados["categoria"] = nova_cat
        cols[6].caption(dados["status"])
        notas_atualizadas[chave] = dados

    st.session_state.notas = notas_atualizadas

    # ─── Classificação em lote ────────────────────────────────────────────────
    st.divider()
    st.subheader("⚡ Classificação em Lote (opcional)")
    col_lote1, col_lote2 = st.columns(2)
    with col_lote1:
        cat_lote = st.selectbox("Categoria para aplicar em lote:", CATEGORIAS, key="cat_lote")
    with col_lote2:
        st.write("")
        st.write("")
        if st.button("Aplicar para TODOS", use_container_width=True):
            for chave in st.session_state.notas:
                st.session_state.notas[chave]["categoria"] = cat_lote
            st.rerun()

    # ─── Resumo ───────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("📊 Resumo por Categoria")
    resumo = {}
    for dados in st.session_state.notas.values():
        cat = dados["categoria"]
        resumo[cat] = resumo.get(cat, 0) + 1

    cols_resumo = st.columns(len(CATEGORIAS))
    for i, cat in enumerate(CATEGORIAS):
        cols_resumo[i].metric(cat, resumo.get(cat, 0))

    # ─── PASSO 4: Gerar ZIP ───────────────────────────────────────────────────
    st.divider()
    st.subheader("📦 4. Gerar ZIP Organizado por Pasta")

    notas_com_xml = {
        chave: dados["categoria"]
        for chave, dados in st.session_state.notas.items()
        if dados["xml_bytes"] is not None
    }
    xmls_dict = {
        chave: (dados["xml_nome"], dados["xml_bytes"])
        for chave, dados in st.session_state.notas.items()
        if dados["xml_bytes"] is not None
    }

    if notas_com_xml:
        zip_bytes = gerar_zip(notas_com_xml, xmls_dict)
        st.download_button(
            label="⬇️ Baixar XMLs Classificados (.zip)",
            data=zip_bytes,
            file_name="nfe_classificadas.zip",
            mime="application/zip",
            use_container_width=True,
            type="primary"
        )
        st.info(f"O ZIP conterá {len(notas_com_xml)} XML(s) organizados nas pastas: {', '.join(set(notas_com_xml.values()))}")
    else:
        st.warning("Nenhum XML disponível para gerar o ZIP.")

# ─── Rodapé ───────────────────────────────────────────────────────────────────
st.divider()
st.caption("💡 A chave de acesso possui 44 dígitos e identifica unicamente cada NF-e no Brasil.")
