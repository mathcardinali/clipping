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
from fpdf import FPDF

# --- 1. CORE CONFIGURATION ---
st.set_page_config(page_title="🚗 Automotive Pulse Digest", layout="wide")
st.title("🚗 Automotive Pulse Digest")

# Webhook do Lark/Feishu
WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/8f561d21-2a4c-4726-bff3-c0bf5d9c35a5"

# --- FUNÇÕES DE SUPORTE ---

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
        # BYPASS DEFINITIVO GOOGLE NEWS
        if "news.google.com" in url:
            try:
                resultado = gnewsdecoder(url)
                if isinstance(resultado, dict) and resultado.get("status"):
                    url = resultado.get("decoded_url")
                elif isinstance(resultado, str) and resultado.startswith("http"):
                    url = resultado
            except:
                pass

        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'}
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
    if not api_key: return "- Erro: Sem API Key -"
    if "- " in texto[:2]: return f"- Falha na extração: {texto} -"
    
    try:
        genai.configure(api_key=api_key)
        
        # RESOLUÇÃO DO ERRO 404: Busca dinâmica de modelos ativos na sua conta
        model_name = None
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'flash' in m.name.lower():
                    model_name = m.name
                    break
        if not model_name: model_name = 'models/gemini-1.5-flash'

        instructions = """
        Act as a specialized Automotive Strategy and CX Analyst. 
        Output: Bilingual (English followed by Chinese). Plain text only. 
        Structure: Technical/Performance (if applicable), Market & Strategic Insight, Customer Impact.
        """
        
        model = genai.GenerativeModel(model_name=model_name, system_instruction=instructions)
        
        # Retry loop para erro 429 (Quota)
        for _ in range(3):
            try:
                response = model.generate_content(texto[:6000])
                return response.text.strip()
            except Exception as e:
                if "429" in str(e):
                    time.sleep(12)
                    continue
                return f"- Erro Gemini: {e} -"
    except Exception as e:
        return f"- Erro Configuração: {e} -"

def gerar_pdf_bytes(dossier_data, session_state):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Automotive Market Intelligence Dossier", ln=True, align="C")
    pdf.ln(10)

    for brand, items in dossier_data.items():
        # Filtra apenas o que o usuário marcou o checkbox
        kept_items = [it for idx, it in enumerate(items) if session_state.get(f"keep_{brand}_{idx}")]
        if kept_items:
            pdf.set_font("Helvetica", "B", 14)
            pdf.set_fill_color(230, 230, 230)
            pdf.cell(0, 10, brand.upper(), ln=True, fill=True)
            pdf.ln(5)
            for it in kept_items:
                pdf.set_font("Helvetica", "B", 11)
                # Limpeza para evitar erro de Encoding no PDF (Remove Chinês apenas no PDF)
                t = it['title'].encode('latin-1', 'ignore').decode('latin-1')
                pdf.multi_cell(0, 6, txt=t)
                pdf.set_font("Helvetica", "", 10)
                s = it['summary'].encode('latin-1', 'ignore').decode('latin-1')
                pdf.multi_cell(0, 5, txt=s)
                pdf.ln(5)
    return pdf.output()

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
    else: st.error("⚠️ Sem API KEY!")
    st.divider()
    target_launch = st.checkbox("🎯 Lançamentos", value=False)
    origins = st.multiselect("Origins:", list(brands_by_origin.keys()), default=["China"])
    available = [b for o in origins for b in brands_by_origin[o]]
    brand_selection = st.multiselect("Brands:", available, default=["Omoda", "BYD"])
    date_range = st.date_input("Period:", value=(datetime.now() - timedelta(days=7), datetime.now()))

    if st.button("🚀 1. Fetch News"):
        if gemini_api_key and len(date_range) == 2:
            st.session_state.step1_complete = False
            results = {}
            launch_keywords = " (lançamento OR segredo OR flagra OR novidade)"
            media_filter = " (site:g1.globo.com OR site:uol.com.br OR site:quatrorodas.abril.com.br OR site:autoesporte.globo.com OR site:motor1.uol.com.br)"
            
            with st.spinner("Agente trabalhando..."):
                for brand in brand_selection:
                    q = f"\"{brand}\" Brasil" + (launch_keywords if target_launch else "") + media_filter
                    full_q = f"{q} after:{date_range[0].strftime('%Y-%m-%d')} before:{date_range[1].strftime('%Y-%m-%d')}"
                    feed = feedparser.parse(f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(full_q)}&hl=pt-BR&gl=BR")
                    
                    brand_news = []
                    for entry in feed.entries[:10]:
                        if brand.lower() in entry.title.lower():
                            texto_raw = extrair_texto_da_noticia(entry.link)
                            resumo = resumir_noticia_com_gemini(texto_raw, gemini_api_key)
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
    
    if st.button("✅ Selecionar Tudo"):
        for brand, items in st.session_state.dossier_data.items():
            for idx in range(len(items)): st.session_state[f"keep_{brand}_{idx}"] = True
        st.rerun()

    for brand, items in st.session_state.dossier_data.items():
        st.subheader(f"🏎️ {brand.upper()}")
        for idx, item in enumerate(items):
            st.checkbox(f"✅ Incluir no Dossiê", key=f"keep_{brand}_{idx}")
            st.markdown(f"**Source:** [{item['title']}]({item['link']})")
            st.session_state.dossier_data[brand][idx]['summary'] = st.text_area(f"Edit {brand}-{idx}", value=item['summary'], height=200, key=f"edit_{brand}_{idx}", label_visibility="collapsed")

    st.divider()
    if st.button("📄 3. Finalizar e Gerar PDF"):
        try:
            pdf_bytes = gerar_pdf_bytes(st.session_state.dossier_data, st.session_state)
            st.session_state.pdf_output = bytes(pdf_bytes)
            st.session_state.step1_complete = True
            st.success("PDF Gerado com Sucesso!")
        except Exception as e:
            st.error(f"Erro ao gerar PDF: {e}")

    if st.session_state.get('step1_complete'):
        st.download_button("📥 Baixar PDF Agora", data=st.session_state.pdf_output, file_name="Automotive_Dossier.pdf", mime="application/pdf")
        
        st.markdown("### 📤 4. Enviar ao Lark")
        link_nuvem = st.text_input("Cole o link da nuvem:")
        if st.button("🚀 Disparar Lark"):
            payload = {
                "msg_type": "interactive",
                "card": {
                    "header": {"title": {"tag": "plain_text", "content": "🚗 Automotive Intelligence"}, "template": "blue"},
                    "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"**[CLICK TO ACCESS DOSSIER]({link_nuvem})**"}}]
                }
            }
            requests.post(WEBHOOK_URL, json=payload)
            st.success("Enviado!")
