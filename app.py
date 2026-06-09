import pdfplumber
import re
import io

def _buscar_chave_no_texto(texto: str):
    """
    Tenta extrair a chave de acesso NF-e (44 dígitos) de um texto bruto.
    """
    # Normaliza espaços invisíveis
    texto = texto.replace('\xa0', ' ').replace('\t', ' ')

    # Formato 1: 44 dígitos contínuos
    m = re.search(r'\b(\d{44})\b', texto)
    if m:
        return m.group(1)

    # Formato 2: 11 blocos de 4 dígitos com espaço simples
    m = re.search(
        r'\b(\d{4}[ \u00a0]\d{4}[ \u00a0]\d{4}[ \u00a0]\d{4}[ \u00a0]\d{4}[ \u00a0]'
        r'\d{4}[ \u00a0]\d{4}[ \u00a0]\d{4}[ \u00a0]\d{4}[ \u00a0]\d{4}[ \u00a0]\d{4})\b',
        texto
    )
    if m:
        return re.sub(r'\s+', '', m.group(1))

    # Formato 3: blocos com espaços variáveis (1 a 4)
    m = re.search(r'(\d{4}\s{1,4}){10}\d{4}', texto)
    if m:
        return re.sub(r'\s+', '', m.group(0))

    # Formato 4: sequência mista que resulte em 44 dígitos
    candidatos = re.findall(r'\d[\d \t]{40,60}\d', texto)
    for c in candidatos:
        apenas = re.sub(r'\D', '', c)
        if len(apenas) == 44:
            return apenas

    # Formato 5: acumulação progressiva de blocos numéricos
    blocos = re.findall(r'\d+', texto)
    acumulado = ''
    for b in blocos:
        acumulado += b
        if len(acumulado) == 44:
            return acumulado
        elif len(acumulado) > 44:
            acumulado = ''

    return None


def extrair_chave_pdf(pdf_bytes: bytes) -> tuple:
    """
    Tenta extrair a chave de acesso do PDF.
    Ordem: pdfplumber → PyPDF2 → OCR
    """

    # ── Etapa 1: pdfplumber (melhor extração de texto) ────────────────────────
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:

                # 1a. Texto corrido da página inteira
                texto = page.extract_text() or ""
                chave = _buscar_chave_no_texto(texto)
                if chave:
                    return chave, "texto"

                # 1b. Busca na região específica da chave de acesso no DANFE
                # A chave fica tipicamente no terço direito, parte superior
                largura  = page.width
                altura   = page.height
                regioes  = [
                    # Região direita superior (onde fica a chave na maioria dos DANFEs)
                    (largura * 0.35, 0, largura, altura * 0.35),
                    # Região central superior
                    (0, 0, largura, altura * 0.25),
                    # Página inteira como fallback
                    (0, 0, largura, altura * 0.5),
                ]
                for bbox in regioes:
                    recorte = page.within_bbox(bbox)
                    texto_recorte = recorte.extract_text() or ""
                    chave = _buscar_chave_no_texto(texto_recorte)
                    if chave:
                        return chave, "texto"

                # 1c. Extração palavra por palavra e reconstrução
                words = page.extract_words(
                    x_tolerance=3,
                    y_tolerance=3,
                    keep_blank_chars=False
                )
                # Agrupa palavras que são só dígitos e estão na mesma linha (±5px)
                linhas = {}
                for w in words:
                    y_key = round(w['top'] / 5) * 5  # agrupa por linha (tolerância 5px)
                    linhas.setdefault(y_key, []).append(w['text'])

                for y_key in sorted(linhas):
                    linha_texto = ' '.join(linhas[y_key])
                    chave = _buscar_chave_no_texto(linha_texto)
                    if chave:
                        return chave, "texto"

                    # Tenta também sem espaços (palavras concatenadas)
                    linha_sem_espaco = ''.join(re.findall(r'\d+', linha_texto))
                    if len(linha_sem_espaco) == 44 and linha_sem_espaco.isdigit():
                        return linha_sem_espaco, "texto"

    except Exception as e:
        pass

    # ── Etapa 2: PyPDF2 como fallback ─────────────────────────────────────────
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        texto  = ""
        for page in reader.pages:
            texto += (page.extract_text() or "") + "\n"
        chave = _buscar_chave_no_texto(texto)
        if chave:
            return chave, "texto"
    except Exception:
        pass

    # ── Etapa 3: OCR ──────────────────────────────────────────────────────────
    if OCR_DISPONIVEL:
        try:
            from pdf2image import convert_from_bytes
            import pytesseract
            paginas   = convert_from_bytes(pdf_bytes, dpi=300)
            texto_ocr = ""
            for pagina in paginas:
                texto_ocr += pytesseract.image_to_string(
                    pagina,
                    lang="por+eng",
                    config="--oem 3 --psm 6"
                ) + "\n"
            chave = _buscar_chave_no_texto(texto_ocr)
            if chave:
                return chave, "ocr"
        except Exception:
            pass

    return None, "falhou"
