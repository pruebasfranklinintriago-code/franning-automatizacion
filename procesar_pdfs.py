import os
import re
import json
import io
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from pypdf import PdfReader

# --- CONFIGURACIÓN ---
CREDENTIALS_FILE = 'credenciales.json'
FOLDER_ID = '1sJzDvzF3CjbZ0bbtWiHYSNm0m2tLDd2V' # ID de tu carpeta principal "Franning"
OUTPUT_FILE = 'marcas.json'

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_FILE, 
        scopes=['https://www.googleapis.com/auth/drive.readonly']
    )
    return build('drive', 'v3', credentials=creds)

def get_all_pdfs(service, folder_id):
    pdfs = []
    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id, name, mimeType, size)").execute()
    items = results.get('files', [])
    for item in items:
        if item['mimeType'] == 'application/vnd.google-apps.folder':
            pdfs.extend(get_all_pdfs(service, item['id']))
        elif item['name'].lower().endswith('.pdf'):
            pdfs.append(item)
    return pdfs

# --- EXTRACCIÓN DE OPOSICIONES ---
def extraer_de_oposicion(texto_original, nombre_archivo):
    texto = texto_original.replace('\u00A0', ' ').replace('\t', ' ')
    gaceta_match = re.search(r'(\d{3,4})', nombre_archivo)
    gaceta = gaceta_match.group(1) if gaceta_match else None
    
    regs = []
    patron = r'((?:SENADI|IEPI|SENAD|IEPL)\s*[\.\-:]?\s*\d{4}\s*-\s*\d+)\s+((?:SENADI|IEPI|SENAD|IEPL)\s*[\.\-:]?\s*\d{4}\s*-\s*\d+)\s+(.*?)(?=\s*(?:SENADI|IEPI|SENAD|IEPL)|$)'
    matches = re.finditer(patron, texto, re.DOTALL | re.IGNORECASE)
    
    for match in matches:
        tramite_op = match.group(1).strip()
        tramite = match.group(2).strip()
        bloque_texto = match.group(3).strip()
        
        denom_match = re.search(r'^(.*?)\s+(\d{3,4})\s*$', bloque_texto)
        if denom_match:
            denominacion = ' '.join(denom_match.group(1).split())
            gaceta_encontrada = denom_match.group(2)
        else:
            denominacion = ' '.join(bloque_texto.split())
            gaceta_encontrada = gaceta
            
        if len(denominacion) > 2:
            regs.append({
                "fuente": "oposicion", 
                "gaceta": gaceta_encontrada, 
                "denominacion": denominacion, 
                "tramite": tramite,
                "claseNiza": None,
                "fechaPresentacion": None,
                "solicitante": None,
                "archivo": nombre_archivo
            })
    return regs

# --- EXTRACCIÓN DE GACETAS ---
def extraer_de_gaceta(texto_original, nombre_archivo):
    texto = texto_original.replace('\u00A0', ' ').replace('\t', ' ')
    gaceta_match = re.search(r'(\d{3,4})', nombre_archivo)
    gaceta = gaceta_match.group(1) if gaceta_match else None
    
    bloques = re.split(r'(?=Signo de solicitud\s*:)', texto, flags=re.IGNORECASE)
    regs = []

    for bloque in bloques:
        if len(bloque.strip()) < 50: continue
        sub_bloques = re.split(r'(?=\(210\))', bloque, flags=re.IGNORECASE)
        
        for sub in sub_bloques:
            if len(sub.strip()) < 50: continue
            
            r = {"fuente": "gaceta", "gaceta": gaceta, "denominacion": None, "tipoSigno": None, "pais": None, "claseNiza": None, "fechaPresentacion": None, "solicitante": None, "representante": None, "tramite": None, "contacto": None, "archivo": nombre_archivo}

            lineas = [l.strip() for l in sub.split('\n') if l.strip()]
            try:
                idx_signo = next(i for i, l in enumerate(lineas) if 'Signo de solicitud' in l)
                denom_partes = []
                for i in range(idx_signo + 1, min(idx_signo + 8, len(lineas))):
                    l = lineas[i]
                    if re.match(r'^(Marca de producto|Marca de servicio|País|\(511\)|\(220\)|\(210\)|Lema comercial)', l, re.IGNORECASE): break
                    if len(l) > 1 and not l.isdigit(): denom_partes.append(l)
                    else: break
                if denom_partes: r['denominacion'] = ' '.join(denom_partes).strip()
            except StopIteration: pass

            mm = re.search(r'\(210\)\s*([A-Z0-9\-]+)', sub, re.IGNORECASE)
            if mm: r['tramite'] = mm.group(1).strip()
            mm = re.search(r'\(220\)\s*(\d{4}[-/]\d{2}[-/]\d{2})', sub)
            if mm: r['fechaPresentacion'] = mm.group(1).strip()
            mm = re.search(r'\(511\)\s*(\d+)', sub)
            if mm: r['claseNiza'] = mm.group(1).strip()
            mm = re.search(r'País\s*:\s*([^\n\r]+)', sub, re.IGNORECASE)
            if mm: r['pais'] = re.sub(r'[\.\-]', '', mm.group(1)).strip()
            mm = re.search(r'\(730\)\s*([^\n\r]+)', sub)
            if mm: r['solicitante'] = mm.group(1).strip()
            else: 
                mm = re.search(r'Titular\s*:\s*([^\n\r]+)', sub, re.IGNORECASE)
                if mm: r['solicitante'] = mm.group(1).strip()

            if r['denominacion'] or r['solicitante']: regs.append(r)
    return regs

# --- EXTRACCIÓN DE BÚSQUEDA FONÉTICA ---
def extraer_de_busqueda_fonetica(texto_original, nombre_archivo):
    texto = texto_original.replace('\u00A0', ' ').replace('\t', ' ')
    lineas = [l.strip() for l in texto.split('\n') if len(l.strip()) > 5]
    regs = []
    
    for linea in lineas:
        match = re.search(r'^(\d{1,2})\s+(.+?)\s+([A-Z0-9\-]+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{3,4})', linea)
        if match:
            regs.append({
                "fuente": "revision_fonetica", 
                "gaceta": match.group(5), 
                "claseNiza": match.group(1),
                "denominacion": match.group(2).strip(), 
                "tramite": match.group(3).strip(),
                "fechaPresentacion": match.group(4),
                "solicitante": None,
                "archivo": nombre_archivo
            })
    return regs

def main():
    print("🚀 Iniciando conexión con Google Drive...")
    service = get_drive_service()
    
    print(f"🔎 Buscando TODOS los PDFs...")
    pdfs = get_all_pdfs(service, FOLDER_ID)
    print(f"✅ Encontrados {len(pdfs)} PDFs. Iniciando procesamiento...\n")

    todas_las_marcas = []
    errores = 0

    for idx, pdf in enumerate(pdfs):
        nombre = pdf['name']
        print(f"[{idx+1}/{len(pdfs)}] 📄 Procesando: {nombre}...")
        
        try:
            request = service.files().get_media(fileId=pdf['id'])
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            
            fh.seek(0)
            reader = PdfReader(fh)
            texto_completo = ""
            for page in reader.pages:
                texto_completo += page.extract_text() + "\n"
            
            nombre_lower = nombre.lower()
            if "oposici" in nombre_lower:
                marcas = extraer_de_oposicion(texto_completo, nombre)
            elif "busqueda" in nombre_lower or "fonetica" in nombre_lower or "zoi" in nombre_lower:
                marcas = extraer_de_busqueda_fonetica(texto_completo, nombre)
            else:
                marcas = extraer_de_gaceta(texto_completo, nombre)
                
            todas_las_marcas.extend(marcas)
            print(f"   -> ✅ Se extrajeron {len(marcas)} marcas.")
            
        except Exception as e:
            errores += 1
            print(f"   -> ❌ Error: {e}")

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(todas_las_marcas, f, ensure_ascii=False, indent=2)

    print(f"\n🏁 ¡Proceso terminado! Total: {len(todas_las_marcas)} marcas.")
    if errores > 0: print(f"⚠️ Hubo errores en {errores} archivos.")

if __name__ == '__main__':
    main()