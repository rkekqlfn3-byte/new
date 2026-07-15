import os
import zipfile
import xml.etree.ElementTree as ET

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    import olefile
except ImportError:
    olefile = None


MAX_PDF_FILE_BYTES = 100 * 1024 * 1024
MAX_PDF_PAGES = 2_000
MAX_PDF_TEXT_CHARS = 5_000_000

def resolve_file_path(file_path):
    if not isinstance(file_path, str) or not file_path.strip():
        return None

    cleaned = os.path.expandvars(os.path.expanduser(file_path.strip().strip('"\'')))
    candidates = [cleaned]
    if not os.path.isabs(cleaned):
        candidates = [
            os.path.join(os.path.expanduser("~"), "Desktop", cleaned),
            os.path.abspath(cleaned),
        ]

    for candidate in candidates:
        absolute = os.path.abspath(candidate)
        if os.path.isfile(absolute):
            return absolute
    return None


def extract_text(file_path):
    resolved_path = resolve_file_path(file_path)
    if resolved_path:
        file_path = resolved_path

    if not resolved_path:
        return f"파일을 찾을 수 없습니다: {file_path}"
        
    ext = os.path.splitext(file_path)[1].lower()
    
    try:
        if ext in ['.txt', '.md', '.csv', '.json', '.py', '.js', '.html']:
            # For Korean environments, try CP949 first if UTF-8 fails
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except UnicodeDecodeError:
                with open(file_path, 'r', encoding='cp949') as f:
                    return f.read()
        elif ext == '.pdf':
            return _read_pdf(file_path)
        elif ext == '.hwp':
            return _read_hwp(file_path)
        elif ext == '.hwpx':
            return _read_hwpx(file_path)
        else:
            # Fallback
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
    except Exception as e:
        return f"파일 읽기 실패 ({ext}): {e}"

def _read_pdf(file_path):
    if PdfReader is None:
        return "pypdf 라이브러리가 설치되지 않아 PDF를 읽을 수 없습니다."
    file_size = os.path.getsize(file_path)
    if file_size > MAX_PDF_FILE_BYTES:
        raise ValueError(
            f"PDF 파일 크기가 안전 제한({MAX_PDF_FILE_BYTES}바이트)을 초과했습니다."
        )

    chunks = []
    total_chars = 0
    with open(file_path, 'rb') as f:
        reader = PdfReader(f, strict=False)
        page_count = len(reader.pages)
        if page_count > MAX_PDF_PAGES:
            raise ValueError(
                f"PDF 페이지 수가 안전 제한({MAX_PDF_PAGES}페이지)을 초과했습니다."
            )
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                total_chars += len(extracted) + 1
                if total_chars > MAX_PDF_TEXT_CHARS:
                    raise ValueError(
                        "PDF 추출 텍스트가 안전 제한"
                        f"({MAX_PDF_TEXT_CHARS}자)을 초과했습니다."
                    )
                chunks.append(extracted)
    return "\n".join(chunks) + ("\n" if chunks else "")

def _read_hwp(file_path):
    if not olefile:
        return "olefile 라이브러리가 설치되지 않아 HWP를 읽을 수 없습니다."
    
    try:
        f = olefile.OleFileIO(file_path)
        dirs = f.listdir()
        
        # HWP5 contains a PrvText stream which is a plain text preview of the document.
        if ["PrvText"] in dirs:
            stream = f.openstream("PrvText")
            data = stream.read()
            return data.decode("utf-16le", errors="ignore")
            
        return "이 HWP 파일은 텍스트 미리보기(PrvText)를 제공하지 않아 단순 텍스트 추출에 실패했습니다. (HWP 문서를 한글에서 txt로 저장 후 다시 시도해 주세요.)"
    except Exception as e:
        return f"HWP 읽기 오류: {e}"

def _read_hwpx(file_path):
    text = ""
    try:
        with zipfile.ZipFile(file_path, 'r') as zf:
            for filename in zf.namelist():
                if filename.startswith("Contents/section") and filename.endswith(".xml"):
                    xml_data = zf.read(filename)
                    root = ET.fromstring(xml_data)
                    for elem in root.iter():
                        # HWPX text nodes
                        if elem.tag.endswith('}t') or elem.tag == 't':
                            if elem.text:
                                text += elem.text + "\n"
        return text
    except Exception as e:
        return f"HWPX 읽기 오류: {e}"
