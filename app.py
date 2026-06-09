import streamlit as st
import zipfile
import io
import re
import os
import xml.etree.ElementTree as ET

try:
    import pdfplumber
    PDFPLUMBER_DISPONIVEL = True
except ImportError:
    PDFPLUMBER_DISPONIVEL = False

try:
    from PyPDF2 import PdfReader
    PYPDF2_DISPONIVEL = True
except ImportError:
    PYPDF2_DISPONIVEL = False

try:
    import pytesseract
    from pdf2image import convert_from_bytes
    OCR_DISPONIVEL = True
except ImportError:
    OCR_DISPONIVEL = False

# ─────────────────────────────────────────────
st.set_page_config(page_title="Classificador NF-e", layout="wide", page_icon="📂")

NAMESPACE_NFE    = "http://www.portalfiscal.inf.br/nfe"
CATEGORIA_PADRAO = "Sem Categoria"

# ─────────────────────────────────────────────
# EXTRAÇÃO DA CHAVE
# ─────────────────────────────────────────────

def _limpar_texto(texto: str) -> str:
    return (texto
        .replace('\xa0', ' ')
        .replace('\t', ' ')
        .replace('\r', '\n')
    )

def _validar_chave_nfe(chave: str) -> bool:
    if not chave or len(chave) != 44 or not chave.isdigit():
        return False
    cuf = int(chave[:2])
    estados_validos = (
        list(range(11, 18)) +
        list(range(21, 30)) +
        list(range(31, 36)) +
        list(range(41, 44)) +
        list(range(50, 54))
    )
    return cuf in estados_validos

def _extrair_chaves_candidatas(texto: str) -> list:
    candidatas = []
    texto = _limpar_texto(texto)

    for m in re.finditer(r'\d{44}', texto):
        candidatas.append(m.group(0))

    for m in re.finditer(
        r'(\d{4} \d{4} \d{4} \d{4} \d{4} \d{4} \d{4} \d{4} \d{4} \d{4} \d{4})',
        texto
    ):
        candidatas.append(re.sub(r'\s+', '', m.group(1)))

    for m in re.finditer(r'(\d{4}\s{1,4}){10}\d{4}', texto):
        candidatas.append(re.sub(r'\D', '', m.group(0)))

    for m in re.finditer(r'\d[\d ]{42,58}\d', texto):
        apenas = re.sub(r'\D', '', m.group(0))
        if len(apenas) == 44:
            candidatas.append(apenas)

    return candidatas

def _buscar_chave_no_texto(texto: str):
    candidatas = _extrair_chaves_candidatas(texto)
    for c in candidatas:
        if _validar_chave_nfe(c):
            return c
    for c in candidatas:
        if len(c) == 44 and c.isdigit():
            return c
    return None

def _buscar_chave_por_contexto(texto: str):
    texto = _limpar_texto(texto)
    linhas = texto.split('\n')
    for i, linha in enumerate(linhas):
        if 'CHAVE DE ACESSO' in linha.upper():
            chave = _buscar_chave_no_texto(linha)
            if chave:
                return chave
            for j in range(i + 1, min(i + 6, len(linhas))):
                chave = _buscar_chave_no_texto(linhas[j])
                if chave:
                    return chave
            bloco = ' '.join(linhas[i:min(i + 6, len(linhas))])
            chave = _buscar_chave_no_texto(bloco)
            if chave:
                return chave
    return None

def extrair_chave_pdf(pdf_bytes: bytes) -> tuple:
    if PDFPLUMBER_DISPONIVEL:
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                texto_completo = ""
                for page in pdf.pages:
                    texto_pag = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
                    texto_completo += texto_pag + "\n"

                    chave = _buscar_chave_por_contexto(texto_pag)
                    if chave: return chave, "texto"

                    chave = _buscar_chave_no_texto(texto_pag)
                    if chave: return chave, "texto"

                    try:
                        words = page.extract_words(x_tolerance=3, y_tolerance=5, keep_blank_chars=False)
                        linhas_palavras = {}
                        for w in words:
                            y_key = round(float(w['top']) / 8) * 8
                            linhas_palavras.setdefault(y_key, []).append(w['text'])
                        for y_key in sorted(linhas_palavras):
                            linha = ' '.join(linhas_palavras[y_key])
                            chave = _buscar_chave_no_texto(linha)
                            if chave: return chave, "texto"
                            so_digitos = re.sub(r'\D', '', linha)
                            if len(so_digitos) >= 44:
                                for start in range(len(so_digitos) - 43):
                                    candidata = so_digitos[start:start + 44]
                                    if _validar_chave_nfe(candidata):
                                        return candidata, "texto"
                    except Exception:
                        pass

                    try:
                        w, h = float(page.width), float(page.height)
                        regioes = [
                            (w * 0.35, 0,        w, h * 0.30),
                            (0,        0,        w, h * 0.25),
                            (w * 0.35, h * 0.05, w, h * 0.40),
                        ]
                        for bbox in regioes:
                            try:
                                recorte = page.within_bbox(bbox)
                                txt = recorte.extract_text() or ""
                                chave = _buscar_chave_por_contexto(txt) or _buscar_chave_no_texto(txt)
                                if chave: return chave, "texto"
                            except Exception:
                                pass
                    except Exception:
                        pass

                chave = _buscar_chave_por_contexto(texto_completo)
                if chave: return chave, "texto"
                chave = _buscar_chave_no_texto(texto_completo)
                if chave: return chave, "texto"
        except Exception:
            pass

    if PYPDF2_DISPONIVEL:
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            texto  = ""
            for page in reader.pages:
                texto += (page.extract_text() or "") + "\n"
            chave = _buscar_chave_por_contexto(texto) or _buscar_chave_no_texto(texto)
            if chave: return chave, "texto"
        except Exception:
            pass

    if OCR_DISPONIVEL:
        try:
            paginas   = convert_from_bytes(pdf_bytes, dpi=300)
            texto_ocr = ""
            for pagina in paginas:
                texto_ocr += pytesseract.image_to_string(
                    pagina, lang="por+eng", config="--oem 3 --psm 6"
                ) + "\n"
            chave = _buscar_chave_por_contexto(texto_ocr) or _buscar_chave_no_texto(texto_ocr)
            if chave: return chave, "ocr"
        except Exception:
            pass

    return None, "falhou"

# ─────────────────────────────────────────────
# LEITURA DO ZIP
# ─────────────────────────────────────────────

def ler_estrutura_zip(zip_bytes: bytes) -> list:
    resultado = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            for entry in zf.namelist():
                if entry.endswith("/") or "__MACOSX" in entry or os.path.basename(entry).startswith("."):
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
                        "nome": nome, "bytes": zf.read(entry),
                        "categoria": categoria, "caminho": entry
                    })
    except zipfile.BadZipFile:
        st.error("ZIP inválido ou corrompido.")
    return resultado

def ler_xmls_zip(zip_bytes: bytes) -> list:
    resultado = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            for entry in zf.namelist():
                if entry.endswith("/") or "__MACOSX" in entry or os.path.basename(entry).startswith("."):
                    continue
                ext = os.path.splitext(entry)[1].lower()
                if ext == ".zip":
                    resultado.extend(ler_xmls_zip(zf.read(entry)))
                elif ext == ".xml":
                    resultado.append({"nome": os.path.basename(entry), "bytes": zf.read(entry)})
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
                "nome": f.name, "bytes": f.read(),
                "categoria": CATEGORIA_PADRAO, "caminho": f.name
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
            if len(chave) == 44: return chave
        ch = root.find(".//{%s}chNFe" % NAMESPACE_NFE)
        if ch is not None and ch.text:
            chave = re.sub(r'\D', '', ch.text)
            if len(chave) == 44: return chave
    except Exception:
        pass
    return None

def extrair_info_xml(xml_bytes: bytes) -> dict:
    info = {"numero":"N/A","emitente":"N/A","valor":"N/A","data":"N/A","cfop":"N/A"}
    try:
        root = ET.fromstring(xml_bytes)
        def tag(t): return root.find(".//{%s}%s" % (NAMESPACE_NFE, t))
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

status_libs = []
if PDFPLUMBER_DISPONIVEL: status_libs.append("✅ pdfplumber")
else:                      status_libs.append("❌ pdfplumber")
if PYPDF2_DISPONIVEL:      status_libs.append("✅ PyPDF2")
else:                      status_libs.append("❌ PyPDF2")
if OCR_DISPONIVEL:         status_libs.append("✅ OCR (Tesseract)")
else:                      status_libs.append("⚠️ OCR indisponível")
st.info("  |  ".join(status_libs))

with st.expander("📖 Estrutura esperada do ZIP de PDFs"):
    st.code(
        "📦 pdfs.zip\n"
        "├── Consumo/\n"
        "│   └── nfe001.pdf   →  categoria = Consumo\n"
        "├── Revenda/\n"
        "│   └── nfe002.pdf   →  categoria = Revenda\n"
        "└── nfe003.pdf        →  categoria = Sem Categoria\n\n"
        "📦 xmls.zip\n"
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
    ("aplicar_versao", 0),   # ← NOVO: controla re-render dos selectbox
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── Upload ─────────────────────────────────────────────────────────────────────
st.divider()
col1, col2 = st.columns(2)
with col1:
    st.subheader("📄 1. PDFs (DANFEs)")
    pdfs_uploaded = st.file_uploader(
        "ZIP com subpastas por categoria, ou PDFs avulsos",
        type=["pdf","zip"], accept_multiple_files=True, key="upload_pdfs"
    )
with col2:
    st.subheader("🗂️ 2. XMLs das NF-es")
    xmls_uploaded = st.file_uploader(
        "ZIP com XMLs ou XMLs avulsos",
        type=["xml","zip"], accept_multiple_files=True, key="upload_xmls"
    )

# ── Processar ──────────────────────────────────────────────────────────────────
st.divider()
if st.button("🔍 Processar", type="primary", use_container_width=True):
    if not pdfs_uploaded and not xmls_uploaded:
        st.warning("Faça o upload de PDFs e/ou XMLs.")
    else:
        notas    = {}
        progress = st.progress(0, text="Extraindo arquivos...")

        lista_pdfs = normalizar_pdfs(pdfs_uploaded or [])
        lista_xmls = normalizar_xmls(xmls_uploaded or [])

        categorias_detectadas = sorted(set(
            p["categoria"] for p in lista_pdfs if p["categoria"] != CATEGORIA_PADRAO
        ))
        st.session_state.categorias    = categorias_detectadas if categorias_detectadas else [CATEGORIA_PADRAO]
        st.session_state.cat_sem_pdf   = categorias_detectadas[0] if categorias_detectadas else CATEGORIA_PADRAO
        st.session_state.aplicar_versao = 0

        with st.expander("🗂️ Estrutura detectada no ZIP de PDFs", expanded=True):
            if lista_pdfs:
                for p in lista_pdfs:
                    st.caption(f"📄 `{p['caminho']}`  →  📁 **{p['categoria']}**")
            else:
                st.info("Nenhum PDF enviado.")

        total       = max(len(lista_pdfs) + len(lista_xmls), 1)
        processados = 0
        lidos_texto = 0
        lidos_ocr   = 0
        falhou_leit = 0

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
                st.warning(f"⚠️ Chave não encontrada no XML: **{arq['nome']}**")
            processados += 1
            progress.progress(processados / total, text=f"Indexando XMLs... {processados}/{total}")

        chaves_pdf = set()
        for arq in lista_pdfs:
            progress.progress(processados / total, text=f"Lendo PDF: {arq['nome']}...")
            chave, metodo = extrair_chave_pdf(arq["bytes"])
            categoria     = arq["categoria"]

            if metodo == "texto":  lidos_texto += 1
            elif metodo == "ocr":  lidos_ocr   += 1
            else:                  falhou_leit += 1

            badge = {"texto":"📄 texto","ocr":"🔍 OCR","falhou":"❌ falhou"}.get(metodo,"")

            if chave:
                chaves_pdf.add(chave)
                if chave in xmls_por_chave:
                    notas[chave] = {
                        "pdf_nome":   arq["nome"],
                        "xml_nome":   xmls_por_chave[chave]["nome"],
                        "xml_bytes":  xmls_por_chave[chave]["bytes"],
                        "info":       xmls_por_chave[chave]["info"],
                        "categoria":  categoria,
                        "tem_pdf":    True,
                        "metodo_pdf": badge,
                        "status":     "✅ Cruzado"
                    }
                else:
                    notas[chave] = {
                        "pdf_nome":   arq["nome"],
                        "xml_nome":   None,
                        "xml_bytes":  None,
                        "info":       {"numero":"N/A","emitente":"N/A","valor":"N/A","data":"N/A","cfop":"N/A"},
                        "categoria":  categoria,
                        "tem_pdf":    True,
                        "metodo_pdf": badge,
                        "status":     "⚠️ XML não encontrado"
                    }
            else:
                notas[f"sem_chave_{arq['nome']}"] = {
                    "pdf_nome":   arq["nome"],
                    "xml_nome":   None,
                    "xml_bytes":  None,
                    "info":       {"numero":"N/A","emitente":"N/A","valor":"N/A","data":"N/A","cfop":"N/A"},
                    "categoria":  categoria,
                    "tem_pdf":    True,
                    "metodo_pdf": badge,
                    "status":     "❌ Chave não extraída do PDF"
                }
            processados += 1
            progress.progress(processados / total, text=f"Cruzando... {processados}/{total}")

        for chave, dados in xmls_por_chave.items():
            if chave not in chaves_pdf:
                notas[chave] = {
                    "pdf_nome":   None,
                    "xml_nome":   dados["nome"],
                    "xml_bytes":  dados["bytes"],
                    "info":       dados["info"],
                    "categoria":  CATEGORIA_PADRAO,
                    "tem_pdf":    False,
                    "metodo_pdf": "—",
                    "status":     "⚠️ PDF não encontrado"
                }

        progress.progress(1.0, text="Concluído!")
        st.session_state.notas      = notas
        st.session_state.processado = True

        # ── Métricas ───────────────────────────────────────────────────────────
        total_pdfs = len(lista_pdfs)
        total_xmls = len(lista_xmls)
        cruzados   = sum(1 for n in notas.values() if n["status"] == "✅ Cruzado")
        sem_pdf    = sum(1 for n in notas.values() if not n["tem_pdf"])
        sem_xml    = sum(1 for n in notas.values() if n["tem_pdf"] and n["xml_bytes"] is None)
        sem_chave  = sum(1 for n in notas.values() if n["status"] == "❌ Chave não extraída do PDF")

        st.divider()
        st.subheader("📊 Resultado do Processamento")

        col_a, col_b, col_c = st.columns(3)
        col_a.metric("📄 PDFs recebidos", total_pdfs)
        col_b.metric("🗂️ XMLs recebidos", total_xmls)
        col_c.metric("✅ Cruzamentos OK",  cruzados)

        col_d, col_e, col_f = st.columns(3)
        col_d.metric("❌ Chave não lida",  sem_chave)
        col_e.metric("⚠️ PDF sem XML",     sem_xml)
        col_f.metric("⚠️ XML sem PDF",     sem_pdf)

        st.divider()
        if cruzados == total_pdfs == total_xmls:
            st.success(f"✅ Tudo certo! {cruzados} PDF(s) cruzados com {cruzados} XML(s). Nenhuma divergência.")
        else:
            st.warning(
                f"⚠️ **Divergência:** {total_pdfs} PDF(s) × "
                f"{total_xmls} XML(s) × {cruzados} cruzamento(s) OK."
            )
            if sem_chave > 0: st.error(f"❌ {sem_chave} PDF(s) com chave não extraída.")
            if sem_xml   > 0: st.warning(f"⚠️ {sem_xml} PDF(s) sem XML correspondente.")
            if sem_pdf   > 0: st.warning(f"⚠️ {sem_pdf} XML(s) sem PDF correspondente.")

        if lidos_ocr   > 0: st.info(f"🔍 {lidos_ocr} PDF(s) lidos via OCR.")
        if falhou_leit > 0: st.warning(f"❌ {falhou_leit} PDF(s) não tiveram a chave extraída.")

# ── XMLs sem PDF ───────────────────────────────────────────────────────────────
if st.session_state.processado and st.session_state.notas:
    sem_pdf_notas = {k: v for k, v in st.session_state.notas.items() if not v["tem_pdf"]}
    if sem_pdf_notas:
        st.divider()
        st.warning(f"⚠️ **{len(sem_pdf_notas)} XML(s) sem PDF**. Escolha a categoria:")
        categorias_opcoes = st.session_state.categorias
        col_sel, col_btn  = st.columns([3, 1])
        with col_sel:
            cat_escolhida = st.selectbox(
                "📁 Classificar XMLs sem PDF em:",
                options=categorias_opcoes,
                index=(categorias_opcoes.index(st.session_state.cat_sem_pdf)
                       if st.session_state.cat_sem_pdf in categorias_opcoes else 0),
                key="sel_cat_sem_pdf"
            )
        with col_btn:
            st.write(""); st.write("")
            if st.button("✅ Aplicar", use_container_width=True):
                # ── CORREÇÃO PRINCIPAL ──────────────────────────────────────
                # Atualiza direto no session_state E incrementa versão
                # para forçar re-render dos selectbox na Revisão Geral
                for chave in list(st.session_state.notas.keys()):
                    if not st.session_state.notas[chave]["tem_pdf"]:
                        st.session_state.notas[chave]["categoria"] = cat_escolhida
                st.session_state.cat_sem_pdf    = cat_escolhida
                st.session_state.aplicar_versao += 1   # ← invalida cache dos selectbox
                st.success(f"**{cat_escolhida}** aplicado a {len(sem_pdf_notas)} XML(s).")
                st.rerun()

# ── Revisão geral ──────────────────────────────────────────────────────────────
if st.session_state.processado and st.session_state.notas:
    st.divider()
    st.subheader("🏷️ 3. Revisão Geral")
    categorias_opcoes = st.session_state.categorias
    versao = st.session_state.aplicar_versao   # ← usado na key dos selectbox

    col_f1, col_f2 = st.columns(2)
    filtro_status = col_f1.selectbox(
        "Status:", ["Todos","✅ Cruzado","⚠️ XML não encontrado","⚠️ PDF não encontrado","❌ Chave não extraída do PDF"]
    )
    filtro_cat = col_f2.selectbox("Categoria:", ["Todas"] + categorias_opcoes)

    cols_h = st.columns([2,2,2.5,1,1.5,1,1.2,2,1.5])
    for col, lbl in zip(cols_h, ["**PDF**","**XML**","**Emitente**","**Nº**","**Valor**","**CFOP**","**Leitura**","**Categoria**","**Status**"]):
        col.markdown(lbl)
    st.divider()

    notas_att = {}
    for chave, dados in st.session_state.notas.items():
        if filtro_status != "Todos" and dados["status"] != filtro_status:
            notas_att[chave] = dados; continue
        if filtro_cat != "Todas" and dados["categoria"] != filtro_cat:
            notas_att[chave] = dados; continue

        cols = st.columns([2,2,2.5,1,1.5,1,1.2,2,1.5])
        cols[0].caption(dados["pdf_nome"] or "—")
        cols[1].caption(dados["xml_nome"] or "—")
        cols[2].caption((dados["info"]["emitente"] or "")[:30])
        cols[3].caption(dados["info"]["numero"])
        cols[4].caption(dados["info"]["valor"])
        cols[5].caption(dados["info"]["cfop"])
        cols[6].caption(dados.get("metodo_pdf","—"))

        cat_atual = dados["categoria"]
        if cat_atual not in categorias_opcoes:
            cat_atual = categorias_opcoes[0]
        idx = categorias_opcoes.index(cat_atual)

        nova_cat = cols[7].selectbox(
            "", categorias_opcoes,
            index=idx,
            # ── CORREÇÃO: key inclui versão para forçar re-render ──
            key=f"cat_{chave}_v{versao}",
            label_visibility="collapsed"
        )
        dados["categoria"] = nova_cat

        badge = {"✅":"🟢","⚠️":"🟡","❌":"🔴"}.get(dados["status"][0],"⚪")
        cols[8].caption(f"{badge} {dados['status'][2:].strip()}")
        notas_att[chave] = dados

    st.session_state.notas = notas_att

    # ── Resumo por categoria ────────────────────────────────────────────────────
    st.divider()
    st.subheader("📊 Resumo por Categoria")
    resumo = {}
    for d in st.session_state.notas.values():
        resumo[d["categoria"]] = resumo.get(d["categoria"], 0) + 1
    cats = [c for c in categorias_opcoes if c in resumo]
    if cats:
        cols_r = st.columns(len(cats))
        for i, cat in enumerate(cats):
            cols_r[i].metric(cat, resumo[cat])

    # ── Download ───────────────────────────────────────────────────────────────
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
                f"⚠️ {ainda_sem_cat} XML(s) ainda em **'{CATEGORIA_PADRAO}'**. "
                "Aplique uma categoria acima antes de baixar."
            )
        zip_bytes = gerar_zip_saida(com_xml)
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
st.caption("💡 Categorias pelas pastas dos PDFs | Chave lida por texto nativo (pdfplumber/PyPDF2) ou OCR")
