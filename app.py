import streamlit as st
import zipfile
import io
import re
import os
import xml.etree.ElementTree as ET
from PyPDF2 import PdfReader

# OCR — importação segura (não quebra se não estiver instalado)
try:
    import pytesseract
    from pdf2image import convert_from_bytes
    OCR_DISPONIVEL = True
except ImportError:
    OCR_DISPONIVEL = False

# ─────────────────────────────────────────────
# CONFIGURAÇÕES
# ─────────────────────────────────────────────
st.set_page_config(page_title="Classificador NF-e", layout="wide", page_icon="📂")

NAMESPACE_NFE    = "http://www.portalfiscal.inf.br/nfe"
CATEGORIA_PADRAO = "Sem Categoria"

# Regex da chave de acesso NF-e (44 dígitos)
REGEX_CHAVE_CONTINUA = re.compile(r'\b(\d{44})\b')
REGEX_CHAVE_BLOCOS   = re.compile(r'(\d{4}[\s]{1,3}){10}\d{4}')

# ─────────────────────────────────────────────
# EXTRAÇÃO DE CHAVE DO PDF
# ─────────────────────────────────────────────

def _buscar_chave_no_texto(texto: str):
    """Tenta extrair chave de 44 dígitos de um texto bruto."""
    # Formato 1: 44 dígitos contínuos
    m = REGEX_CHAVE_CONTINUA.search(texto)
    if m:
        return m.group(1)

    # Formato 2: blocos de 4 dígitos separados por espaço (DANFE padrão)
    m = REGEX_CHAVE_BLOCOS.search(texto)
    if m:
        return re.sub(r'\s+', '', m.group(0))

    return None


def extrair_chave_pdf(pdf_bytes: bytes) -> tuple:
    """
    Tenta extrair a chave de acesso do PDF em duas etapas:
      1. Leitura de texto nativo (PyPDF2) — PDFs digitais
      2. OCR via pytesseract              — PDFs escaneados / imagem

    Retorna: (chave_str | None, metodo_str)
      metodo pode ser: "texto", "ocr", "falhou"
    """
    # ── Etapa 1: texto nativo ──────────────────────────────────────────────────
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        texto  = "".join(p.extract_text() or "" for p in reader.pages)
        chave  = _buscar_chave_no_texto(texto)
        if chave:
            return chave, "texto"
    except Exception:
        pass

    # ── Etapa 2: OCR (somente se pytesseract estiver disponível) ───────────────
    if OCR_DISPONIVEL:
        try:
            # Converte cada página do PDF em imagem (300 DPI para melhor precisão)
            paginas = convert_from_bytes(pdf_bytes, dpi=300)
            texto_ocr = ""
            for pagina in paginas:
                # lang="por+eng" reconhece português e inglês
                texto_ocr += pytesseract.image_to_string(
                    pagina,
                    lang="por+eng",
                    config="--oem 3 --psm 6"
                )
            chave = _buscar_chave_no_texto(texto_ocr)
            if chave:
                return chave, "ocr"
        except Exception:
            pass

    return None, "falhou"


# ─────────────────────────────────────────────
# LEITURA DO ZIP
# ─────────────────────────────────────────────

def ler_estrutura_zip(zip_bytes: bytes) -> list:
    """
    Percorre o ZIP e para cada PDF encontrado,
    lê o nome da pasta mãe (1º nível) como categoria.

    Consumo/nfe001.pdf           → categoria = "Consumo"
    Revenda/nfe002.pdf           → categoria = "Revenda"
    Ativo Imobilizado/nfe003.pdf → categoria = "Ativo Imobilizado"
    Consumo/Jan/nfe004.pdf       → categoria = "Consumo"
    nfe005.pdf                   → categoria = "Sem Categoria"
    """
    resultado = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            for entry in zf.namelist():
                if (entry.endswith("/")
                        or "__MACOSX" in entry
                        or os.path.basename(entry).startswith(".")):
                    continue

                ext    = os.path.splitext(entry)[1].lower()
                nome   = os.path.basename(entry)
                partes = [p for p in entry.replace("\\", "/").split("/") if p.strip()]

                if ext == ".zip":
                    inner     = ler_estrutura_zip(zf.read(entry))
                    pasta_pai = partes[-2] if len(partes) >= 2 else CATEGORIA_PADRAO
                    for arq in inner:
                        if arq["categoria"] == CATEGORIA_PADRAO:
                            arq["categoria"] = pasta_pai
                    resultado.extend(inner)

                elif ext == ".pdf":
                    categoria = partes[0] if len(partes) >= 2 else CATEGORIA_PADRAO
                    resultado.append({
                        "nome":      nome,
                        "bytes":     zf.read(entry),
                        "categoria": categoria,
                        "caminho":   entry
                    })
    except zipfile.BadZipFile:
        st.error("ZIP inválido ou corrompido.")
    return resultado


def ler_xmls_zip(zip_bytes: bytes) -> list:
    resultado = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            for entry in zf.namelist():
                if (entry.endswith("/")
                        or "__MACOSX" in entry
                        or os.path.basename(entry).startswith(".")):
                    continue
                ext = os.path.splitext(entry)[1].lower()
                if ext == ".zip":
                    resultado.extend(ler_xmls_zip(zf.read(entry)))
                elif ext == ".xml":
                    resultado.append({
                        "nome":  os.path.basename(entry),
                        "bytes": zf.read(entry)
                    })
    except zipfile.BadZipFile:
        st.error("ZIP de XMLs inválido ou corrompido.")
    return resultado


def normalizar_pdfs(uploads) -> list:
    resultado = []
    for f in uploads:
        f.seek(0)
        ext = os.path.splitext(f.name)[1].lower()
        if ext == ".zip":
            resultado.extend(ler_estrutura_zip(f.read()))
        elif ext == ".pdf":
            resultado.append({
                "nome":      f.name,
                "bytes":     f.read(),
                "categoria": CATEGORIA_PADRAO,
                "caminho":   f.name
            })
    return resultado


def normalizar_xmls(uploads) -> list:
    resultado = []
    for f in uploads:
        f.seek(0)
        ext = os.path.splitext(f.name)[1].lower()
        if ext == ".zip":
            resultado.extend(ler_xmls_zip(f.read()))
        elif ext == ".xml":
            resultado.append({"nome": f.name, "bytes": f.read()})
    return resultado


# ─────────────────────────────────────────────
# FUNÇÕES NF-e
# ─────────────────────────────────────────────

def extrair_chave_xml(xml_bytes: bytes) -> str:
    try:
        root = ET.fromstring(xml_bytes)
        inf  = root.find(".//{%s}infNFe" % NAMESPACE_NFE)
        if inf is not None:
            chave = re.sub(r'\D', '', inf.get("Id", ""))
            if len(chave) == 44:
                return chave
        ch = root.find(".//{%s}chNFe" % NAMESPACE_NFE)
        if ch is not None and ch.text:
            chave = re.sub(r'\D', '', ch.text)
            if len(chave) == 44:
                return chave
        return None
    except Exception:
        return None


def extrair_info_xml(xml_bytes: bytes) -> dict:
    info = {"numero": "N/A", "emitente": "N/A", "valor": "N/A", "data": "N/A", "cfop": "N/A"}
    try:
        root = ET.fromstring(xml_bytes)

        def tag(t):
            return root.find(".//{%s}%s" % (NAMESPACE_NFE, t))

        if tag("nNF")  is not None: info["numero"]  = tag("nNF").text
        if tag("vNF")  is not None: info["valor"]   = f"R$ {float(tag('vNF').text):,.2f}"
        if tag("CFOP") is not None: info["cfop"]    = tag("CFOP").text

        xNome = root.find(".//{%s}emit/{%s}xNome" % (NAMESPACE_NFE, NAMESPACE_NFE)) or tag("xNome")
        if xNome is not None: info["emitente"] = xNome.text

        dhEmi = tag("dhEmi") or tag("dEmi")
        if dhEmi is not None: info["data"] = (dhEmi.text or "")[:10]
    except Exception:
        pass
    return info


def gerar_zip_saida(notas: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dados in notas.values():
            if dados["xml_bytes"]:
                zf.writestr(
                    f"{dados['categoria']}/{dados['xml_nome']}",
                    dados["xml_bytes"]
                )
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────
# INTERFACE
# ─────────────────────────────────────────────

st.title("📂 Classificador de NF-e por Categoria")
st.markdown(
    "A categoria é definida automaticamente pelo **nome da pasta mãe** do PDF dentro do ZIP. "
    "XMLs sem PDF correspondente recebem a categoria que você escolher."
)

# Aviso sobre OCR
if OCR_DISPONIVEL:
    st.success("🔍 OCR ativo — PDFs escaneados (imagem) também serão lidos automaticamente via Tesseract.")
else:
    st.warning(
        "⚠️ OCR não disponível. Apenas PDFs com texto nativo serão lidos. "
        "Para habilitar OCR instale: `pip install pytesseract pdf2image Pillow` "
        "e os binários **Tesseract** e **Poppler**."
    )

with st.expander("📖 Estrutura esperada do ZIP"):
    st.code(
        "📦 pdfs.zip\n"
        "├── Consumo/\n"
        "│   └── nfe001.pdf   →  categoria = Consumo\n"
        "├── Revenda/\n"
        "│   └── nfe002.pdf   →  categoria = Revenda\n"
        "└── nfe003.pdf        →  categoria = Sem Categoria\n\n"
        "📦 xmls.zip  (estrutura de pastas ignorada)\n"
        "├── nfe001.xml\n"
        "└── nfe002.xml",
        language=None
    )

# Estado da sessão
for key, default in [
    ("notas", {}),
    ("processado", False),
    ("categorias", []),
    ("cat_sem_pdf", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ─── Upload ────────────────────────────────────────────────────────────────────
st.divider()
col1, col2 = st.columns(2)

with col1:
    st.subheader("📄 1. PDFs (DANFEs)")
    pdfs_uploaded = st.file_uploader(
        "ZIP com subpastas por categoria, ou PDFs avulsos",
        type=["pdf", "zip"],
        accept_multiple_files=True,
        key="upload_pdfs"
    )

with col2:
    st.subheader("🗂️ 2. XMLs das NF-es")
    xmls_uploaded = st.file_uploader(
        "ZIP com XMLs ou XMLs avulsos",
        type=["xml", "zip"],
        accept_multiple_files=True,
        key="upload_xmls"
    )

# ─── Processar ─────────────────────────────────────────────────────────────────
st.divider()
if st.button("🔍 Processar", type="primary", use_container_width=True):
    if not pdfs_uploaded and not xmls_uploaded:
        st.warning("Faça o upload de PDFs e/ou XMLs.")
    else:
        notas    = {}
        progress = st.progress(0, text="Extraindo arquivos...")

        lista_pdfs = normalizar_pdfs(pdfs_uploaded or [])
        lista_xmls = normalizar_xmls(xmls_uploaded or [])

        # Categorias detectadas pelas pastas dos PDFs
        categorias_detectadas = sorted(set(
            p["categoria"] for p in lista_pdfs
            if p["categoria"] != CATEGORIA_PADRAO
        ))
        st.session_state.categorias  = categorias_detectadas if categorias_detectadas else [CATEGORIA_PADRAO]
        st.session_state.cat_sem_pdf = categorias_detectadas[0] if categorias_detectadas else CATEGORIA_PADRAO

        # Log da estrutura detectada
        with st.expander("🗂️ Estrutura detectada no ZIP de PDFs", expanded=True):
            if lista_pdfs:
                for p in lista_pdfs:
                    st.caption(f"📄 `{p['caminho']}`  →  📁 **{p['categoria']}**")
            else:
                st.info("Nenhum PDF enviado.")

        total       = max(len(lista_pdfs) + len(lista_xmls), 1)
        processados = 0

        # Contadores de método de leitura
        lidos_texto = 0
        lidos_ocr   = 0
        falhou_leit = 0

        # Indexa XMLs pela chave
        xmls_por_chave = {}
        for arq in lista_xmls:
            chave = extrair_chave_xml(arq["bytes"])
            if chave:
                xmls_por_chave[chave] = {
                    "nome":  arq["nome"],
                    "bytes": arq["bytes"],
                    "info":  extrair_info_xml(arq["bytes"])
                }
            else:
                st.warning(f"⚠️ Chave não encontrada: **{arq['nome']}**")
            processados += 1
            progress.progress(processados / total, text=f"Indexando XMLs... {processados}/{total}")

        # Cruza PDFs → XMLs
        chaves_pdf = set()
        for arq in lista_pdfs:
            progress.progress(
                processados / total,
                text=f"Lendo PDF: {arq['nome']}..."
            )

            chave, metodo = extrair_chave_pdf(arq["bytes"])
            categoria     = arq["categoria"]

            # Contabiliza método
            if metodo == "texto":
                lidos_texto += 1
            elif metodo == "ocr":
                lidos_ocr += 1
            else:
                falhou_leit += 1

            # Define badge do método de leitura
            badge_metodo = {
                "texto":  "📄 texto",
                "ocr":    "🔍 OCR",
                "falhou": "❌ falhou"
            }.get(metodo, "")

            if chave:
                chaves_pdf.add(chave)
                if chave in xmls_por_chave:
                    notas[chave] = {
                        "pdf_nome":    arq["nome"],
                        "xml_nome":    xmls_por_chave[chave]["nome"],
                        "xml_bytes":   xmls_por_chave[chave]["bytes"],
                        "info":        xmls_por_chave[chave]["info"],
                        "categoria":   categoria,
                        "tem_pdf":     True,
                        "metodo_pdf":  badge_metodo,
                        "status":      "✅ Cruzado"
                    }
                else:
                    notas[chave] = {
                        "pdf_nome":    arq["nome"],
                        "xml_nome":    None,
                        "xml_bytes":   None,
                        "info":        {"numero":"N/A","emitente":"N/A","valor":"N/A","data":"N/A","cfop":"N/A"},
                        "categoria":   categoria,
                        "tem_pdf":     True,
                        "metodo_pdf":  badge_metodo,
                        "status":      "⚠️ XML não encontrado"
                    }
            else:
                notas[f"sem_chave_{arq['nome']}"] = {
                    "pdf_nome":    arq["nome"],
                    "xml_nome":    None,
                    "xml_bytes":   None,
                    "info":        {"numero":"N/A","emitente":"N/A","valor":"N/A","data":"N/A","cfop":"N/A"},
                    "categoria":   categoria,
                    "tem_pdf":     True,
                    "metodo_pdf":  badge_metodo,
                    "status":      "❌ Chave não extraída do PDF"
                }
            processados += 1
            progress.progress(processados / total, text=f"Cruzando PDFs... {processados}/{total}")

        # XMLs sem PDF correspondente
        for chave, dados in xmls_por_chave.items():
            if chave not in chaves_pdf:
                notas[chave] = {
                    "pdf_nome":    None,
                    "xml_nome":    dados["nome"],
                    "xml_bytes":   dados["bytes"],
                    "info":        dados["info"],
                    "categoria":   CATEGORIA_PADRAO,
                    "tem_pdf":     False,
                    "metodo_pdf":  "—",
                    "status":      "⚠️ PDF não encontrado"
                }

        progress.progress(1.0, text="Concluído!")
        st.session_state.notas      = notas
        st.session_state.processado = True

        # Resumo do processamento
        cruzados = sum(1 for n in notas.values() if n["status"] == "✅ Cruzado")
        sem_pdf  = sum(1 for n in notas.values() if not n["tem_pdf"])

        col_a, col_b, col_c, col_d = st.columns(4)
        col_a.metric("Total de notas",   len(notas))
        col_b.metric("✅ Cruzadas",       cruzados)
        col_c.metric("🔍 Lidas via OCR",  lidos_ocr)
        col_d.metric("⚠️ Sem PDF",        sem_pdf)

        if lidos_ocr > 0:
            st.info(f"🔍 {lidos_ocr} PDF(s) eram imagem e foram lidos via OCR.")
        if falhou_leit > 0:
            st.warning(f"❌ {falhou_leit} PDF(s) não tiveram a chave extraída nem por texto nem por OCR.")

# ─── Seletor para XMLs sem PDF ─────────────────────────────────────────────────
if st.session_state.processado and st.session_state.notas:
    sem_pdf_notas = {k: v for k, v in st.session_state.notas.items() if not v["tem_pdf"]}

    if sem_pdf_notas:
        st.divider()
        st.warning(
            f"⚠️ **{len(sem_pdf_notas)} XML(s) sem PDF** encontrado(s). "
            "Escolha em qual categoria classificá-los:"
        )

        categorias_opcoes = st.session_state.categorias

        col_sel, col_btn = st.columns([3, 1])
        with col_sel:
            cat_escolhida = st.selectbox(
                "📁 Classificar XMLs sem PDF em:",
                options=categorias_opcoes,
                index=(
                    categorias_opcoes.index(st.session_state.cat_sem_pdf)
                    if st.session_state.cat_sem_pdf in categorias_opcoes else 0
                ),
                key="sel_cat_sem_pdf",
                help="As opções são as pastas detectadas no ZIP de PDFs."
            )
        with col_btn:
            st.write("")
            st.write("")
            if st.button("✅ Aplicar", use_container_width=True):
                for chave in sem_pdf_notas:
                    st.session_state.notas[chave]["categoria"] = cat_escolhida
                st.session_state.cat_sem_pdf = cat_escolhida
                st.success(f"**{cat_escolhida}** aplicado a {len(sem_pdf_notas)} XML(s).")
                st.rerun()

        with st.expander(f"📋 Ver os {len(sem_pdf_notas)} XML(s) sem PDF"):
            cols_h = st.columns([3, 2.5, 1.5, 1.5, 1, 2])
            for col, lbl in zip(cols_h, ["**XML**","**Emitente**","**Nº NF**","**Valor**","**CFOP**","**Categoria Atual**"]):
                col.markdown(lbl)
            st.divider()
            for dados in sem_pdf_notas.values():
                cols = st.columns([3, 2.5, 1.5, 1.5, 1, 2])
                cols[0].caption(dados["xml_nome"] or "—")
                cols[1].caption((dados["info"]["emitente"] or "")[:30])
                cols[2].caption(dados["info"]["numero"])
                cols[3].caption(dados["info"]["valor"])
                cols[4].caption(dados["info"]["cfop"])
                cols[5].caption(f"📁 **{dados['categoria']}**")

# ─── Revisão geral ─────────────────────────────────────────────────────────────
if st.session_state.processado and st.session_state.notas:
    st.divider()
    st.subheader("🏷️ 3. Revisão Geral")
    st.caption("A coluna **Leitura** indica se a chave foi extraída por texto nativo ou OCR.")

    categorias_opcoes = st.session_state.categorias

    col_f1, col_f2 = st.columns(2)
    filtro_status = col_f1.selectbox(
        "Status:",
        ["Todos","✅ Cruzado","⚠️ XML não encontrado","⚠️ PDF não encontrado","❌ Chave não extraída do PDF"]
    )
    filtro_cat = col_f2.selectbox("Categoria:", ["Todas"] + categorias_opcoes)

    # Cabeçalho com coluna extra "Leitura"
    cols_h = st.columns([2, 2, 2.5, 1, 1.5, 1, 1.5, 2, 1.5])
    for col, lbl in zip(cols_h, ["**PDF**","**XML**","**Emitente**","**Nº**","**Valor**","**CFOP**","**Leitura**","**Categoria**","**Status**"]):
        col.markdown(lbl)
    st.divider()

    notas_att = {}
    for chave, dados in st.session_state.notas.items():
        if filtro_status != "Todos" and dados["status"] != filtro_status:
            notas_att[chave] = dados; continue
        if filtro_cat != "Todas" and dados["categoria"] != filtro_cat:
            notas_att[chave] = dados; continue

        cols = st.columns([2, 2, 2.5, 1, 1.5, 1, 1.5, 2, 1.5])
        cols[0].caption(dados["pdf_nome"] or "—")
        cols[1].caption(dados["xml_nome"] or "—")
        cols[2].caption((dados["info"]["emitente"] or "")[:30])
        cols[3].caption(dados["info"]["numero"])
        cols[4].caption(dados["info"]["valor"])
        cols[5].caption(dados["info"]["cfop"])
        cols[6].caption(dados.get("metodo_pdf", "—"))   # ← 📄 texto | 🔍 OCR

        idx      = categorias_opcoes.index(dados["categoria"]) if dados["categoria"] in categorias_opcoes else 0
        nova_cat = cols[7].selectbox("", categorias_opcoes, index=idx, key=f"cat_{chave}", label_visibility="collapsed")
        dados["categoria"] = nova_cat

        badge = {"✅":"🟢","⚠️":"🟡","❌":"🔴"}.get(dados["status"][0],"⚪")
        cols[8].caption(f"{badge} {dados['status'][2:].strip()}")
        notas_att[chave] = dados

    st.session_state.notas = notas_att

    # ─── Resumo ────────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("📊 Resumo por Categoria")
    resumo = {}
    for d in st.session_state.notas.values():
        resumo[d["categoria"]] = resumo.get(d["categoria"], 0) + 1

    cats_com_notas = [c for c in categorias_opcoes if c in resumo]
    if cats_com_notas:
        cols_r = st.columns(len(cats_com_notas))
        for i, cat in enumerate(cats_com_notas):
            cols_r[i].metric(cat, resumo[cat])

    # ─── Download ──────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("📦 4. Download")

    com_xml = {k: v for k, v in st.session_state.notas.items() if v["xml_bytes"]}

    if com_xml:
        ainda_sem_cat = sum(
            1 for v in com_xml.values()
            if not v["tem_pdf"] and v["categoria"] == CATEGORIA_PADRAO
        )
        if ainda_sem_cat:
            st.warning(
                f"⚠️ {ainda_sem_cat} XML(s) sem PDF ainda estão como **'{CATEGORIA_PADRAO}'**. "
                "Aplique uma categoria acima antes de baixar."
            )

        zip_bytes = gerar_zip_saida(com_xml)
        pastas    = sorted(set(v["categoria"] for v in com_xml.values()))

        estrutura = ""
        for pasta in pastas:
            xmls_pasta = [v["xml_nome"] for v in com_xml.values() if v["categoria"] == pasta]
            estrutura += f"📁 {pasta}/  ({len(xmls_pasta)} arquivo(s))\n"
            for nome in xmls_pasta:
                estrutura += f"   └── {nome}\n"
        st.code(estrutura, language=None)

        st.download_button(
            "⬇️ Baixar XMLs Classificados (.zip)",
            data=zip_bytes,
            file_name="nfe_classificadas.zip",
            mime="application/zip",
            use_container_width=True,
            type="primary"
        )
    else:
        st.warning("Nenhum XML disponível para exportar.")

st.divider()
st.caption("💡 PDFs digitais → leitura direta | PDFs escaneados → OCR automático via Tesseract")
