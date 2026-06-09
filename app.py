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
# FUNÇÕES DE EXTRAÇÃO DE ARQUIVOS
# ─────────────────────────────────────────────

def extrair_arquivos_do_zip(zip_file, extensoes: list) -> list:
    """
    Extrai arquivos de um ZIP filtrando por extensão.
    Retorna lista de dicts: {nome, bytes}
    Suporta ZIPs dentro de ZIPs (1 nível).
    """
    arquivos = []
    try:
        with zipfile.ZipFile(zip_file, "r") as zf:
            for entry in zf.namelist():
                # Ignora diretórios e arquivos ocultos (ex: __MACOSX)
                if entry.endswith("/") or "__MACOSX" in entry or entry.startswith("."):
                    continue

                ext = os.path.splitext(entry)[1].lower()

                # ZIP dentro de ZIP (1 nível de profundidade)
                if ext == ".zip":
                    inner_bytes = io.BytesIO(zf.read(entry))
                    arquivos.extend(extrair_arquivos_do_zip(inner_bytes, extensoes))

                elif ext in extensoes:
                    conteudo = zf.read(entry)
                    nome = os.path.basename(entry)  # remove subpastas do nome
                    arquivos.append({"nome": nome, "bytes": conteudo})
    except zipfile.BadZipFile:
        st.error("Arquivo ZIP inválido ou corrompido.")
    return arquivos


def normalizar_uploads(uploads, extensoes: list) -> list:
    """
    Recebe lista de UploadedFile (pode ser .zip, .pdf ou .xml).
    Retorna lista unificada de dicts: {nome, bytes}
    """
    resultado = []
    for f in uploads:
        ext = os.path.splitext(f.name)[1].lower()
        if ext == ".zip":
            f.seek(0)
            arquivos_extraidos = extrair_arquivos_do_zip(io.BytesIO(f.read()), extensoes)
            resultado.extend(arquivos_extraidos)
        elif ext in extensoes:
            f.seek(0)
            resultado.append({"nome": f.name, "bytes": f.read()})
    return resultado


# ─────────────────────────────────────────────
# FUNÇÕES DE LEITURA
# ─────────────────────────────────────────────

def extrair_chave_pdf(pdf_bytes: bytes) -> str:
    """Extrai a chave de acesso (44 dígitos) do texto do PDF."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        texto_completo = ""
        for page in reader.pages:
            texto_completo += page.extract_text() or ""

        # Formato 1: 44 dígitos contínuos
        match = re.search(r'\b(\d{44})\b', texto_completo)
        if match:
            return match.group(1)

        # Formato 2: blocos "XXXX XXXX ... " (11 grupos de 4 dígitos)
        match = re.search(r'(\d{4}\s){10}\d{4}', texto_completo)
        if match:
            return re.sub(r'\s+', '', match.group(0))

        return None
    except Exception:
        return None


def extrair_chave_xml(xml_bytes: bytes) -> str:
    """Extrai a chave de acesso do XML da NF-e."""
    try:
        root = ET.fromstring(xml_bytes)

        # Tenta pelo atributo Id da infNFe
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


def extrair_info_xml(xml_bytes: bytes) -> dict:
    """Extrai informações relevantes do XML para exibição."""
    info = {"numero": "N/A", "emitente": "N/A", "valor": "N/A", "data": "N/A", "cfop": "N/A"}
    try:
        root = ET.fromstring(xml_bytes)

        nNF = root.find(".//{%s}nNF" % NAMESPACE_NFE)
        if nNF is not None:
            info["numero"] = nNF.text

        xNome = root.find(".//{%s}emit/{%s}xNome" % (NAMESPACE_NFE, NAMESPACE_NFE))
        if xNome is None:
            xNome = root.find(".//{%s}xNome" % NAMESPACE_NFE)
        if xNome is not None:
            info["emitente"] = xNome.text

        vNF = root.find(".//{%s}vNF" % NAMESPACE_NFE)
        if vNF is not None:
            info["valor"] = f"R$ {float(vNF.text):,.2f}"

        dhEmi = root.find(".//{%s}dhEmi" % NAMESPACE_NFE)
        if dhEmi is None:
            dhEmi = root.find(".//{%s}dEmi" % NAMESPACE_NFE)
        if dhEmi is not None:
            info["data"] = dhEmi.text[:10] if dhEmi.text else "N/A"

        cfop = root.find(".//{%s}CFOP" % NAMESPACE_NFE)
        if cfop is not None:
            info["cfop"] = cfop.text

    except Exception:
        pass
    return info


def gerar_zip(notas_classificadas: dict, xmls_dict: dict) -> bytes:
    """Gera ZIP com XMLs organizados por pasta/categoria."""
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
st.markdown("Faça o upload dos **PDFs** e **XMLs** — individualmente ou em arquivos **.zip**.")

# Inicializa estado da sessão
if "notas" not in st.session_state:
    st.session_state.notas = {}
if "processado" not in st.session_state:
    st.session_state.processado = False

# ─── PASSO 1: Upload ───────────────────────────────────────────────────────────
st.divider()
col1, col2 = st.columns(2)

with col1:
    st.subheader("📄 1. Upload dos PDFs (DANFEs)")
    st.caption("Aceita arquivos `.pdf` individuais ou um `.zip` contendo PDFs")
    pdfs_uploaded = st.file_uploader(
        "Selecione PDFs ou ZIP de PDFs",
        type=["pdf", "zip"],
        accept_multiple_files=True,
        key="upload_pdfs"
    )

with col2:
    st.subheader("🗂️ 2. Upload dos XMLs das NF-es")
    st.caption("Aceita arquivos `.xml` individuais ou um `.zip` contendo XMLs")
    xmls_uploaded = st.file_uploader(
        "Selecione XMLs ou ZIP de XMLs",
        type=["xml", "zip"],
        accept_multiple_files=True,
        key="upload_xmls"
    )

# ─── Contadores de preview ─────────────────────────────────────────────────────
if pdfs_uploaded:
    total_pdfs = sum(
        len(extrair_arquivos_do_zip(io.BytesIO(f.read()), [".pdf"])) if f.name.endswith(".zip")
        else 1
        for f in pdfs_uploaded
        # reset seek depois do preview
    )
    # Reseta seek para não afetar o processamento
    for f in pdfs_uploaded:
        f.seek(0)
    st.info(f"📄 {total_pdfs} PDF(s) detectado(s) nos uploads")

if xmls_uploaded:
    total_xmls = sum(
        len(extrair_arquivos_do_zip(io.BytesIO(f.read()), [".xml"])) if f.name.endswith(".zip")
        else 1
        for f in xmls_uploaded
    )
    for f in xmls_uploaded:
        f.seek(0)
    st.info(f"🗂️ {total_xmls} XML(s) detectado(s) nos uploads")

# ─── PASSO 2: Processar ────────────────────────────────────────────────────────
st.divider()
if st.button("🔍 Processar e Cruzar Arquivos", type="primary", use_container_width=True):
    if not pdfs_uploaded and not xmls_uploaded:
        st.warning("Faça o upload de pelo menos PDFs ou XMLs.")
    else:
        notas = {}
        progress = st.progress(0, text="Iniciando processamento...")

        # ── Normaliza uploads (extrai ZIPs se necessário) ──────────────────────
        lista_pdfs = normalizar_uploads(pdfs_uploaded or [], [".pdf"])
        lista_xmls = normalizar_uploads(xmls_uploaded or [], [".xml"])

        total = len(lista_pdfs) + len(lista_xmls)
        processados = 0

        # ── Processa XMLs ──────────────────────────────────────────────────────
        xmls_por_chave = {}
        for arq in lista_xmls:
            chave = extrair_chave_xml(arq["bytes"])
            info  = extrair_info_xml(arq["bytes"])
            if chave:
                xmls_por_chave[chave] = {
                    "nome":  arq["nome"],
                    "bytes": arq["bytes"],
                    "info":  info
                }
            else:
                st.warning(f"⚠️ Chave não encontrada no XML: **{arq['nome']}**")
            processados += 1
            progress.progress(processados / max(total, 1), text=f"Lendo XMLs... {processados}/{total}")

        # ── Processa PDFs ──────────────────────────────────────────────────────
        chaves_encontradas_pdf = set()
        for arq in lista_pdfs:
            chave = extrair_chave_pdf(arq["bytes"])
            if chave:
                chaves_encontradas_pdf.add(chave)
                if chave in xmls_por_chave:
                    notas[chave] = {
                        "pdf_nome":  arq["nome"],
                        "xml_nome":  xmls_por_chave[chave]["nome"],
                        "xml_bytes": xmls_por_chave[chave]["bytes"],
                        "info":      xmls_por_chave[chave]["info"],
                        "categoria": CATEGORIAS[0],
                        "status":    "✅ Cruzado"
                    }
                else:
                    notas[chave] = {
                        "pdf_nome":  arq["nome"],
                        "xml_nome":  None,
                        "xml_bytes": None,
                        "info":      {"numero": "N/A", "emitente": "N/A", "valor": "N/A", "data": "N/A", "cfop": "N/A"},
                        "categoria": CATEGORIAS[0],
                        "status":    "⚠️ XML não encontrado"
                    }
            else:
                chave_fake = f"sem_chave_{arq['nome']}"
                notas[chave_fake] = {
                    "pdf_nome":  arq["nome"],
                    "xml_nome":  None,
                    "xml_bytes": None,
                    "info":      {"numero": "N/A", "emitente": "N/A", "valor": "N/A", "data": "N/A", "cfop": "N/A"},
                    "categoria": CATEGORIAS[0],
                    "status":    "❌ Chave não extraída do PDF"
                }
            processados += 1
            progress.progress(processados / max(total, 1), text=f"Lendo PDFs... {processados}/{total}")

        # ── XMLs sem PDF correspondente ────────────────────────────────────────
        for chave, dados in xmls_por_chave.items():
            if chave not in chaves_encontradas_pdf:
                notas[chave] = {
                    "pdf_nome":  None,
                    "xml_nome":  dados["nome"],
                    "xml_bytes": dados["bytes"],
                    "info":      dados["info"],
                    "categoria": CATEGORIAS[0],
                    "status":    "⚠️ PDF não encontrado"
                }

        progress.progress(1.0, text="Concluído!")
        st.session_state.notas = notas
        st.session_state.processado = True
        st.success(f"✅ {len(notas)} nota(s) processada(s) — {len([n for n in notas.values() if n['status'] == '✅ Cruzado'])} cruzada(s) com sucesso.")

# ─── PASSO 3: Classificação ────────────────────────────────────────────────────
if st.session_state.processado and st.session_state.notas:
    st.divider()
    st.subheader("🏷️ 3. Classificar as Notas por Categoria")

    filtro = st.selectbox(
        "Filtrar por status:",
        ["Todos", "✅ Cruzado", "⚠️ XML não encontrado", "⚠️ PDF não encontrado", "❌ Chave não extraída do PDF"]
    )

    header = st.columns([2, 2, 2, 1.2, 1.5, 1, 2, 2])
    for col, label in zip(header, ["**PDF**", "**XML**", "**Emitente**", "**Nº NF**", "**Valor**", "**CFOP**", "**Categoria**", "**Status**"]):
        col.markdown(label)
    st.divider()

    notas_atualizadas = {}
    for chave, dados in st.session_state.notas.items():
        if filtro != "Todos" and dados["status"] != filtro:
            notas_atualizadas[chave] = dados
            continue

        cols = st.columns([2, 2, 2, 1.2, 1.5, 1, 2, 2])
        cols[0].caption(dados["pdf_nome"] or "—")
        cols[1].caption(dados["xml_nome"] or "—")
        cols[2].caption((dados["info"]["emitente"] or "N/A")[:25])
        cols[3].caption(dados["info"]["numero"])
        cols[4].caption(dados["info"]["valor"])
        cols[5].caption(dados["info"]["cfop"])

        idx = CATEGORIAS.index(dados["categoria"]) if dados["categoria"] in CATEGORIAS else 0
        nova_cat = cols[6].selectbox("", CATEGORIAS, index=idx, key=f"cat_{chave}", label_visibility="collapsed")
        dados["categoria"] = nova_cat
        cols[7].caption(dados["status"])
        notas_atualizadas[chave] = dados

    st.session_state.notas = notas_atualizadas

    # ─── Classificação em lote ────────────────────────────────────────────────
    st.divider()
    st.subheader("⚡ Classificação em Lote")
    col_lote1, col_lote2, col_lote3 = st.columns([2, 1, 1])
    with col_lote1:
        cat_lote = st.selectbox("Categoria para aplicar:", CATEGORIAS, key="cat_lote")
    with col_lote2:
        st.write("")
        st.write("")
        if st.button("Aplicar para TODOS", use_container_width=True):
            for chave in st.session_state.notas:
                st.session_state.notas[chave]["categoria"] = cat_lote
            st.rerun()
    with col_lote3:
        st.write("")
        st.write("")
        if st.button("Aplicar apenas aos filtrados", use_container_width=True):
            for chave, dados in st.session_state.notas.items():
                if filtro == "Todos" or dados["status"] == filtro:
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
        pastas = sorted(set(notas_com_xml.values()))
        st.download_button(
            label="⬇️ Baixar XMLs Classificados (.zip)",
            data=zip_bytes,
            file_name="nfe_classificadas.zip",
            mime="application/zip",
            use_container_width=True,
            type="primary"
        )
        st.info(f"📁 Pastas no ZIP: {' | '.join(pastas)}")
    else:
        st.warning("Nenhum XML disponível para gerar o ZIP.")

# ─── Rodapé ───────────────────────────────────────────────────────────────────
st.divider()
st.caption("💡 Formatos aceitos: PDF e XML individuais, ou arquivos .zip contendo PDFs/XMLs (inclusive ZIPs dentro de ZIPs).")
