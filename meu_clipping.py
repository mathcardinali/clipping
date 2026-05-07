import streamlit as st
import feedparser
import requests
import urllib.parse
import re 
import json
import html
import time
from datetime import datetime, timedelta, time as dt_time
from time import mktime
from deep_translator import GoogleTranslator
import trafilatura 
import google.generativeai as genai
from googlenewsdecoder import gnewsdecoder
from fpdf import FPDF # Nova biblioteca de PDF, mais estável

# --- 1. CORE CONFIGURATION ---
st.set_page_config(page_title="🚗 Automotive Pulse Digest", layout="wide")
st.title("🚗 Automotive Pulse Digest")
st.markdown("Automotive Market Intelligence Agent")

# Feishu Webhook
WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/8f561d21-2a4c-4726-bff3-c0bf5d9c35a5"

# --- Funções de Apoio (Completas) ---
def safe_translate(text, target_lang):
    try:
        urls = re.findall(r'(https?://[^\s]+)', text)
        temp_text = text
        for i, url in enumerate(urls):
            temp_text = temp_text.replace(url, f"[[URL_{i}]]")
        translated_text = GoogleTranslator(source='auto', target=target_lang).translate(temp_text)
        for i, url in enumerate(urls):
            translated_text = translated_text.replace(f"[[URL_{i}]]", url)
        return translated_text
    except:
        return text

def extrair_texto_da_noticia(url):
    try:
        if "news.google.com" in url:
            try:
                resultado = gnewsdecoder(url)
                if isinstance(resultado, dict) and resultado.get("status"):
                    url = resultado.get("decoded_url")
                elif isinstance(resultado, str) and resultado.startswith("http"):
                    url = resultado
            except:
                try:
                    resp_g = requests.get(url, timeout=10)
                    match = re.search(r'data-p="([^"]+)"', resp_g.text)
                    if match:
                        data_p = html.unescape(match.group(1))
                        obj = json.loads(data_p.replace('%.@.', '["garturlreq",'))
                        payload = {'f.req': json.dumps([[['Fbv4je', json.dumps(obj[:-6] + obj[-2:]), 'null', 'generic']]])}
                        res_api = requests.post("https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je", data=payload, headers={'content-type': 'application/x-www-form-urlencoded;charset=utf-8'})
                        url_real_match = re.search(r'(https?://[^"]+)', res_api.text)
                        if url_real_match: url = url_real_match.group(1)
                except: pass

        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        session = requests.Session()
        resposta = session.get(url, headers=headers, timeout=15, allow_redirects=True)
        dominio = urllib.parse.urlparse(url).netloc.replace("www.", "")
        
        if resposta.status_code == 200:
            texto = trafilatura.extract(resposta.text)
            return texto if texto and len(texto) > 150 else f"- Texto insuficiente em {dominio} -"
        return f"- Bloqueio no site {dominio} (Erro {resposta.status_code}) -"
    except Exception as e:
        return f"- Erro de conexão: {e} -"

def resumir_noticia_com_gemini(texto, api_key):
    if not api_key: return "- Erro: API Key ausente -"
    if "- " in texto[:2]: return f"- Falha na Extração: {texto} -"
    try:
        genai.configure(api_key=api_key)
        instructions = "Act as a specialized Automotive Strategy and CX Analyst. Output bilingual (English followed by Chinese translation), plain text, under 1000 characters. No bold text."
        model = genai.GenerativeModel(model_name='gemini-1.5-flash', system_instruction=instructions)
        # Trunking para economizar tokens
        response = model.generate_content(texto[:6000])
        return response.text.strip()
    except Exception as e:
        if "429" in str(e): 
            time.sleep(12)
            return "- Limite de velocidade atingido. Tente novamente em instantes. -"
        return f"- Erro Gemini: {e} -"

# --- Função de PDF Estável (FPDF2) ---
def criar_pdf_bytes(dossier_data, session_state):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Automotive Market Intelligence Dossier", ln=True, align="C")
    pdf.set_font("Helvetica", "I", 10)
    pdf.cell(0, 10, f"Date: {datetime.now().strftime('%d/%m/%Y')} | Researcher: Matheus Cardinali", ln=True, align="C")
    pdf.ln(10)

    for brand, items in dossier_data.items():
        kept_items = [it for idx, it in enumerate(items) if session_state.get(f"keep_{brand}_{idx}")]
        if kept_items:
            pdf.set_font("Helvetica", "B", 14)
            pdf.set_fill_color(230, 230, 230)
            pdf.cell(0, 10, brand.upper(), ln=True, fill=True)
            pdf.ln(5)
            for it in kept_items:
                pdf.set_font("Helvetica", "B", 11)
                # Limpa caracteres que o PDF simples não aceita
                titulo = it['title'].encode('latin-1', 'ignore').decode('latin-1')
                pdf.multi_cell(0, 6, txt=titulo)
                pdf.set_font("Helvetica", "", 10)
                resumo = it['summary'].encode('latin-1', 'ignore').decode('latin-1')
                pdf.multi_cell(0, 5, txt=resumo)
                pdf.ln(5)
    
    return pdf.output() # Retorna os bytes do PDF

# --- 2. SESSION STATE ---
if 'dossier_data' not in st.session_state: st.session_state.dossier_data = {}
if 'step1_complete' not in st.session_state: st.session_state.step1_complete = False
gemini_api_key = st.secrets.get("GEMINI_API_KEY", "")

# --- 3. SIDEBAR ---
brands_by_origin = {
    "China": ["Omoda", "Jaecoo", "BYD", "GWM", "Zeekr", "GAC", "Geely", "Leapmotor", "Chery"],
    "Germany": ["Volkswagen", "BMW", "Mercedes-Benz", "Audi", "Porsche"],
    "USA": ["Chevrolet", "Ford", "Tesla", "Ram", "Jeep"],
    "Japan": ["Toyota", "Honda", "Nissan", "Mitsubishi", "Subaru"]
}

with st.sidebar:
    st.header("⚙️ Parameters")
    if gemini_api_key: st.success("✅ IA Conectada")
    else: st.error("⚠️ Configure GEMINI_API_KEY")
    st.divider()
    target_launch = st.checkbox("🎯 Focar em Lançamentos", value=False)
    origins = st.multiselect("Origins:", list(brands_by_origin.keys()), default=["China"])
    available = [b for o in origins for b in brands_by_origin[o]]
    brand_selection = st.multiselect("Brands:", available, default=["Omoda", "BYD"])
    date_range = st.date_input("Period:", value=(datetime.now() - timedelta(days=7), datetime.now()))

    if st.button("🚀 1. Fetch News Links"):
        if gemini_api_key and len(date_range) == 2:
            st.session_state.step1_complete = False
            results = {}
            launch_keywords = " (lançamento OR segredo OR flagra OR novidade)"
            media_filter = " (site:g1.globo.com OR site:uol.com.br OR site:quatrorodas.abril.com.br OR site:autoesporte.globo.com OR site:motor1.uol.com.br)"
            
            with st.spinner("Agent processing news..."):
                for brand in brand_selection:
                    q = f"\"{brand}\" Brasil" + (launch_keywords if target_launch else "") + media_filter
                    full_q = f"{q} after:{date_range[0].strftime('%Y-%m-%d')} before:{date_range[1].strftime('%Y-%m-%d')}"
                    feed = feedparser.parse(f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(full_q)}&hl=pt-BR&gl=BR")
                    
                    brand_news = []
                    for entry in feed.entries[:10]:
                        if brand.lower() in entry.title.lower():
                            texto = extrair_texto_da_noticia(entry.link)
                            resumo = resumir_noticia_com_gemini(texto, gemini_api_key)
                            time.sleep(2)
                            brand_news.append({
                                "title": f"{safe_translate(entry.title, 'en')} / {safe_translate(entry.title, 'zh-CN')}",
                                "link": entry.link, "summary": resumo
                            })
                    if brand_news: results[brand] = brand_news
            st.session_state.dossier_data = results

# --- 4. EDITING AREA ---
if st.session_state.dossier_data:
    st.header("📝 2. Curate Insights")
    for brand, items in st.session_state.dossier_data.items():
        st.subheader(f"🏎️ {brand.upper()}")
        for idx, item in enumerate(items):
            st.checkbox(f"✅ Incluir no Dossiê", value=False, key=f"keep_{brand}_{idx}")
            st.markdown(f"**Source:** [{item['title']}]({item['link']})")
            st.session_state.dossier_data[brand][idx]['summary'] = st.text_area(f"Edit {brand}-{idx}", value=item['summary'], height=200, key=f"edit_{brand}_{idx}", label_visibility="collapsed")

    st.divider()
    if st.button("📄 3. Gerar PDF Final"):
        pdf_bytes = criar_pdf_bytes(st.session_state.dossier_data, st.session_state)
        st.session_state.pdf_bytes = pdf_bytes
        st.session_state.step1_complete = True
        st.success("PDF Gerado! Clique no botão de download abaixo.")

    if st.session_state.get('step1_complete'):
        st.download_button(
            label="📥 Baixar Dossiê PDF",
            data=st.session_state.pdf_bytes,
            file_name=f"Automotive_Dossier_{datetime.now().strftime('%d%m')}.pdf",
            mime="application/pdf"
        )
        
        st.markdown("---")
        st.markdown("### 📤 4. Envio para o Lark")
        user_url = st.text_input("🔗 Link da Nuvem (após subir o arquivo acima):")
        if st.button("🚀 Enviar ao Lark"):
            if user_url:
                # Lógica simplificada do Card
                payload = {
                    "msg_type": "interactive",
                    "card": {
                        "header": {"title": {"tag": "plain_text", "content": "🚗 Market Intelligence"}, "template": "blue"},
                        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"**[ACCESS FULL PDF]({user_url})**"}}]
                    }
                }
                requests.post(WEBHOOK_URL, json=payload)
                st.success("Enviado!")
