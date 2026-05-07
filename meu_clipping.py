import streamlit as st
import feedparser
import requests
import urllib.parse
import re 
import json
import html
import time
import os
from datetime import datetime, timedelta, time as dt_time
from time import mktime
from deep_translator import GoogleTranslator
import trafilatura 
import google.generativeai as genai
from googlenewsdecoder import gnewsdecoder
from fpdf import FPDF # Biblioteca estável para PDF

# --- 1. CORE CONFIGURATION ---
st.set_page_config(page_title="🚗 Automotive Pulse Digest", layout="wide")
st.title("🚗 Automotive Pulse Digest")
st.markdown("🚗 Automotive Pulse Digest")

# Feishu Webhook
WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/8f561d21-2a4c-4726-bff3-c0bf5d9c35a5"

# --- Função segura de tradução ---
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
    except Exception as e:
        return text

# --- FUNÇÃO DO AGENTE VIRTUAL (EXTRAÇÃO COM DESCRIPTOGRAFIA AVANÇADA) ---
def extrair_texto_da_noticia(url):
    try:
        if "news.google.com" in url:
            try:
                # Tenta usar a biblioteca oficial (Plano A)
                resultado = gnewsdecoder(url)
                if isinstance(resultado, dict) and resultado.get("status"):
                    url = resultado.get("decoded_url")
                elif isinstance(resultado, str) and resultado.startswith("http"):
                    url = resultado
            except Exception:
                # Engenharia Reversa do token dinâmico (Plano B)
                try:
                    resp_g = requests.get(url, timeout=10)
                    match = re.search(r'data-p="([^"]+)"', resp_g.text)
                    if match:
                        data_p = html.unescape(match.group(1))
                        obj = json.loads(data_p.replace('%.@.', '["garturlreq",'))
                        payload = {'f.req': json.dumps([[['Fbv4je', json.dumps(obj[:-6] + obj[-2:]), 'null', 'generic']]])}
                        res_api = requests.post("https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je", data=payload, headers={'content-type': 'application/x-www-form-urlencoded;charset=utf-8'})
                        url_real_match = re.search(r'(https?://[^"]+)', res_api.text)
                        if url_real_match:
                            url = url_real_match.group(1)
                except Exception:
                    pass

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7'
        }
        
        session = requests.Session()
        resposta = session.get(url, headers=headers, timeout=15, allow_redirects=True)
        dominio_real = urllib.parse.urlparse(url).netloc.replace("www.", "")
        
        if resposta.status_code == 200:
            texto = trafilatura.extract(resposta.text)
            if texto and len(texto) > 150:
                return texto
            else:
                return f"- Acesso liberado ao site ({dominio_real}), mas a IA não achou texto estruturado para ler. -"
        else:
            return f"- O site ({dominio_real}) bloqueou o robô (Erro HTTP {resposta.status_code}). -"
            
    except requests.exceptions.Timeout:
        return "- O site final demorou muito para responder. -"
    except Exception as e:
        return f"- Erro fatal na conexão com o site: {e} -"

# --- FUNÇÃO DO AGENTE VIRTUAL (RESUMO GEMINI COM OTIMIZAÇÃO) ---
def resumir_noticia_com_gemini(texto, api_key):
    if not api_key:
        return "- Erro: Chave de API não encontrada nos Secrets. -"
    
    if "Erro:" in texto or "- Acesso liberado" in texto or "- O site" in texto:
        return f"- Falha Técnica na Leitura: {texto} -"
        
    try:
        genai.configure(api_key=api_key)
        
        instructions = """
        Role & Instructions:
        Act as a specialized Automotive Strategy and CX Analyst. Your goal is to process news articles and provide high-level, standardized summaries optimized for professional reporting.
        Rules for Output:
        Language: Always respond in both English and Chinese (English text followed immediately by its Chinese translation).
        Formatting: Never use bold text (no asterisks). Use plain text only to ensure easy copy-pasting.
        Length: Keep the total response under 1000 characters.
        Structure: Technical/Performance (if applicable), Market & Strategic Insight, Customer Impact.
        """
        
        model_name = None
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'flash' in m.name.lower():
                    model_name = m.name
                    break
        
        if not model_name: model_name = 'gemini-1.5-flash'

        model = genai.GenerativeModel(model_name=model_name, system_instruction=instructions)
        
        texto_otimizado = texto[:6000]
        
        tentativas = 3
        for tentativa in range(tentativas):
            try:
                response = model.generate_content(texto_otimizado)
                return response.text.strip()
            except Exception as api_error:
                if "429" in str(api_error):
                    time.sleep(12)
                    continue
                return f"- Erro da API: {api_error} -"
                    
    except Exception as e:
        return f"- Erro na configuração do Agente: {e} -"

# --- Função de PDF (Gera em memória para download) ---
def gerar_pdf_dossier(dossier_data, session_state):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Automotive Market Intelligence Dossier", ln=True, align="C")
    pdf.set_font("Helvetica", "I", 10)
    pdf.cell(0, 10, f"Researcher: Matheus Cardinali | Date: {datetime.now().strftime('%d/%m/%Y')}", ln=True, align="C")
    pdf.ln(10)

    for brand, items in dossier_data.items():
        kept_items = [it for idx, it in enumerate(items) if session_state.get(f"keep_{brand}_{idx}")]
        if kept_items:
            pdf.set_font("Helvetica", "B", 14)
            pdf.set_fill_color(240, 240, 240)
            pdf.cell(0, 10, brand.upper(), ln=True, fill=True)
            pdf.ln(4)
            for it in kept_items:
                pdf.set_font("Helvetica", "B", 11)
                # Removendo caracteres especiais para evitar erro no PDF simples
                title_clean = it['title'].encode('latin-1', 'ignore').decode('latin-1')
                pdf.multi_cell(0, 6, txt=title_clean)
                pdf.set_font("Helvetica", "", 10)
                summary_clean = it['summary'].encode('latin-1', 'ignore').decode('latin-1')
                pdf.multi_cell(0, 5, txt=summary_clean)
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
    st.header("⚙️ Market Parameters")
    if gemini_api_key: st.success("✅ Agente de IA Conectado")
    else: st.error("⚠️ Falta GEMINI_API_KEY!")
    st.divider()
    target_launch = st.checkbox("🎯 Focar em Lançamentos/Segredos", value=False)
    origins = st.multiselect("Origins:", list(brands_by_origin.keys()), default=["China"])
    available = [b for o in origins for b in brands_by_origin[o]]
    brand_selection = st.multiselect("Brands:", available, default=["Omoda", "Jaecoo", "BYD"])
    date_range = st.date_input("Period:", value=(datetime.now() - timedelta(days=7), datetime.now()))

    if st.button("🚀 1. Fetch News Links"):
        if gemini_api_key and len(date_range) == 2:
            st.session_state.step1_complete = False
            for key in list(st.session_state.keys()):
                if key.startswith("keep_"): del st.session_state[key]

            d_ini, d_end = date_range
            start_datetime = datetime.combine(d_ini, dt_time.min)
            end_datetime = datetime.combine(d_end, dt_time.max)
            
            results = {}
            launch_keywords = " (lançamento OR segredo OR flagra OR novidade OR \"modelo 2027\" OR \"modelo 2026\")"
            media_filter = " (site:g1.globo.com OR site:uol.com.br OR site:estadao.com.br OR site:folha.uol.com.br OR site:quatrorodas.abril.com.br OR site:autoesporte.globo.com OR site:motor1.uol.com.br)"
            
            with st.spinner("Agent processing news..."):
                for brand in brand_selection:
                    base_q = f"\"{brand}\" Brasil" + (launch_keywords if target_launch else "") + media_filter
                    full_q = f"{base_q} after:{d_ini.strftime('%Y-%m-%d')} before:{d_end.strftime('%Y-%m-%d')}"
                    feed = feedparser.parse(f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(full_q)}&hl=pt-BR&gl=BR")
                    
                    if feed.entries:
                        brand_news = []
                        for entry in feed.entries[:10]:
                            if brand.lower() in entry.title.lower():
                                pub_date = datetime.fromtimestamp(mktime(entry.published_parsed))
                                if not (start_datetime <= pub_date <= end_datetime): continue
                                
                                en_title = safe_translate(entry.title, 'en')
                                zh_title = safe_translate(entry.title, 'zh-CN')
                                
                                texto_full = extrair_texto_da_noticia(entry.link)
                                resumo = resumir_noticia_com_gemini(texto_full, gemini_api_key)
                                time.sleep(2) # Respeita o limite da API

                                brand_news.append({
                                    "title": f"{en_title} / {zh_title}", 
                                    "link": entry.link, 
                                    "summary": resumo
                                })
                        if brand_news: results[brand] = brand_news
            st.session_state.dossier_data = results

# --- 4. EDITING AREA ---
if st.session_state.dossier_data:
    st.header("📝 2. Curate Insights")
    st.info("Resumos gerados automaticamente pelo Agente Virtual.")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ Select All"):
            for brand, items in st.session_state.dossier_data.items():
                for idx in range(len(items)): st.session_state[f"keep_{brand}_{idx}"] = True
    with col2:
        if st.button("❌ Deselect All"):
            for brand, items in st.session_state.dossier_data.items():
                for idx in range(len(items)): st.session_state[f"keep_{brand}_{idx}"] = False
    
    st.divider()

    for brand, items in st.session_state.dossier_data.items():
        st.subheader(f"🏎️ {brand.upper()}")
        for idx, item in enumerate(items):
            st.checkbox(f"✅ Incluir {brand}-{idx}", value=False, key=f"keep_{brand}_{idx}")
            st.markdown(f"**Source:** [{item['title']}]({item['link']})")
            st.session_state.dossier_data[brand][idx]['summary'] = st.text_area(label=f"Edit {brand}-{idx}", value=item['summary'], height=250, key=f"edit_{brand}_{idx}", label_visibility="collapsed")

    # --- 5. EXPORT ---
    st.divider()
    
    if st.button("📄 3. Gerar PDF do Dossiê"):
        pdf_bytes = gerar_pdf_dossier(st.session_state.dossier_data, st.session_state)
        st.download_button(
            label="📥 Clique aqui para Baixar o PDF",
            data=pdf_bytes,
            file_name=f"Automotive_Dossier_{datetime.now().strftime('%d%m')}.pdf",
            mime="application/pdf"
        )
        st.session_state.step1_complete = True

    if st.session_state.get('step1_complete'):
        st.markdown("### 📤 4. Envio para o Lark")
        user_url = st.text_input("🔗 Cole o link público do PDF hospedado na nuvem:")
        if st.button("🚀 Enviar ao Lark"):
            if user_url:
                payload = {
                    "msg_type": "interactive",
                    "card": {
                        "header": {"title": {"tag": "plain_text", "content": "🚗 Automotive Intelligence"}, "template": "blue"},
                        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"**⭐✨ [ACCESS FULL PDF]({user_url}) ✨⭐**"}}]
                    }
                }
                requests.post(WEBHOOK_URL, json=payload)
                st.success("Enviado!")
