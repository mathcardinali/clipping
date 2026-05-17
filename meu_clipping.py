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
from fpdf import FPDF

# --- 1. CORE CONFIGURATION ---
st.set_page_config(page_title="🚗 Automotive Pulse Digest", layout="wide")
st.title("🚗 Automotive Pulse Digest")

# Lark/Feishu Webhook
WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/8f561d21-2a4c-4726-bff3-c0bf5d9c35a5"

# --- BRANDS & RESEARCH TOPICS ---
brands_by_origin = {
    "China": ["OMODA&JAECOO", "Lepas", "BYD", "GWM", "Zeekr", "GAC", "Geely", "Leapmotor", "Chery", "Dongfeng", "Jetour"],
    "Germany": ["Volkswagen", "BMW", "Mercedes-Benz", "Audi", "Porsche"],
    "USA": ["Chevrolet", "Ford", "Tesla", "Ram", "Jeep"],
    "Japan": ["Toyota", "Honda", "Nissan", "Mitsubishi", "Subaru"]
}

# Strategic Research Topics (non-brand based searches focused on Brazil context)
research_topics = {
    "EV Charging & Batteries": {
        "emoji": "🔋",
        "query": '("carro elétrico" OR "veículo elétrico" OR "bateria automotiva" OR "ponto de recarga" OR carregador OR "estação de recarga" OR "cadeia de suprimento" OR "supply chain")'
    },
    "Tax & ICMS Changes": {
        "emoji": "💰",
        "query": '(ICMS OR IPI OR "carga tributária" OR "reforma tributária" OR tributação OR "imposto sobre veículos") (automotivo OR veículo OR carro OR montadora OR automóvel)'
    },
    "New Factories Progress": {
        "emoji": "🏭",
        "query": '(fábrica OR planta OR "nova fábrica" OR "linha de produção" OR "construção da fábrica" OR investimento) (montadora OR automotivo OR automotiva OR veículo)'
    }
}

# Brands always pinned at the top of the selection
PRIORITY_BRANDS = ["OMODA&JAECOO", "Omoda", "Jaecoo"]

def sort_priority_brands(brands):
    priority = [b for b in PRIORITY_BRANDS if b in brands]
    others = [b for b in brands if b not in PRIORITY_BRANDS]
    return priority + others

def is_topic(key):
    return key in research_topics

def get_section_emoji(key):
    if is_topic(key):
        return research_topics[key]["emoji"]
    return "🏎️"

# --- SUPPORT FUNCTIONS ---

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
                pass

        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'}
        session = requests.Session()
        resposta = session.get(url, headers=headers, timeout=15, allow_redirects=True)
        dominio_real = urllib.parse.urlparse(url).netloc.replace("www.", "")
        
        if resposta.status_code == 200:
            texto = trafilatura.extract(resposta.text)
            return texto if texto and len(texto) > 150 else f"- Insufficient text extracted from {dominio_real} -"
        return f"- Site blocked by security wall {dominio_real} (Error {resposta.status_code}) -"
    except Exception as e:
        return f"- Connection error: {e} -"

def resumir_noticia_com_gemini(texto, api_key):
    if not api_key: return "- Error: Missing API Key -"
    if "- " in texto[:2]: return f"- Extraction Failure: {texto} -"
    
    try:
        genai.configure(api_key=api_key)
        
        # DYNAMIC MODEL SEARCH (Anti 404 Error)
        model_name = None
        try:
            available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            for m in available_models:
                if 'flash' in m.lower():
                    model_name = m
                    break
            if not model_name: model_name = available_models[0]
        except:
            model_name = 'models/gemini-1.5-flash'

        # --- CX ANALYST INSTRUCTIONS ---
        system_instruction = """
        Role & Instructions:
        Act as a specialized Automotive Strategy and CX Analyst. Your goal is to process news articles and provide high-level, standardized summaries optimized for professional reporting.

        Rules for Output:
        Language: Always respond in both English and Chinese (English text followed immediately by its Chinese translation).
        Formatting: Never use bold text (no asterisks). Use plain text only to ensure easy copy-pasting.
        Length: Keep the total response under 1000 characters (including both languages).
        Structure:
        Technical/Performance (Bilingual) — Include this section ONLY if the news is directly related to vehicle launches, physical products, or technical specifications. Otherwise, omit it entirely.
        Market & Strategic Insight (Bilingual) — A single combined section.
        Customer Impact (Bilingual) — A final short paragraph.
        """
        
        model = genai.GenerativeModel(model_name=model_name, system_instruction=system_instruction)
        
        for _ in range(3):
            try:
                response = model.generate_content(texto[:6000])
                return response.text.strip()
            except Exception as e:
                if "429" in str(e):
                    time.sleep(12)
                    continue
                return f"- Gemini API Error: {e} -"
    except Exception as e:
        return f"- Configuration Error: {e} -"

# --- PDF GENERATION WITH CHINESE SUPPORT & EMBEDDED LINKS ---
def gerar_pdf_bytes(dossier_data, session_state):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    # FONT REGISTRATION
    font_path = "fireflysung.ttf"
    if os.path.exists(font_path):
        pdf.add_font('Firefly', '', font_path)
        font_main = 'Firefly'
    else:
        font_main = 'Helvetica'

    largura_util = pdf.epw 

    # Header
    pdf.set_font(font_main, 'B' if font_main == 'Helvetica' else '', 20)
    pdf.set_text_color(26, 35, 126) # Navy Blue
    pdf.cell(largura_util, 15, "Automotive Pulse Digest", ln=True, align="C")
    
    pdf.set_font(font_main, '', 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(largura_util, 6, f"Researcher: Matheus Cardinali | Date: {datetime.now().strftime('%m/%d/%Y')}", ln=True, align="C")
    pdf.ln(8)

    for brand, items in dossier_data.items():
        kept_items = [it for idx, it in enumerate(items) if session_state.get(f"keep_{brand}_{idx}")]
        
        if kept_items:
            label = "TOPIC" if is_topic(brand) else "BRAND"
            pdf.set_font(font_main, 'B' if font_main == 'Helvetica' else '', 14)
            pdf.set_fill_color(240, 242, 246)
            pdf.set_text_color(0, 0, 0)
            pdf.cell(largura_util, 10, f"  {label}: {brand.upper()}", ln=True, fill=True)
            pdf.ln(4)

            for it in kept_items:
                pdf.set_x(pdf.l_margin)
                
                # News Title with EMBEDDED LINK
                pdf.set_font(font_main, 'B' if font_main == 'Helvetica' else '', 11)
                pdf.set_text_color(0, 86, 179)
                clean_title = it['title'].encode('latin-1', 'replace').decode('latin-1') if font_main == 'Helvetica' else it['title']
                
                # Use write() to wrap text while embedding the clickable URL
                pdf.write(7, txt=clean_title, link=it['link'])
                pdf.ln(8) # Line break after title
                pdf.set_x(pdf.l_margin)

                # Strategic Summary
                pdf.set_font(font_main, '', 10)
                pdf.set_text_color(33, 33, 33)
                clean_summary = it['summary'].encode('latin-1', 'replace').decode('latin-1') if font_main == 'Helvetica' else it['summary']
                pdf.multi_cell(largura_util, 6, txt=clean_summary, align='L')
                
                # Divider
                pdf.ln(4)
                pdf.set_draw_color(220, 220, 220)
                pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + largura_util, pdf.get_y())
                pdf.ln(4)
    
    return pdf.output()

# --- 2. SESSION STATE ---
if 'raw_fetched_news' not in st.session_state: st.session_state.raw_fetched_news = {}
if 'dossier_data' not in st.session_state: st.session_state.dossier_data = {}

# Salva as datas do período para usar no Header do Lark
if 'd_ini_str' not in st.session_state: st.session_state.d_ini_str = ""
if 'd_end_str' not in st.session_state: st.session_state.d_end_str = ""

gemini_api_key = st.secrets.get("GEMINI_API_KEY", "")

# --- 3. SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Settings")
    if gemini_api_key: st.success("✅ AI Connected")
    else: st.error("⚠️ Missing API KEY!")
    st.divider()
    
    target_launch = st.checkbox("🎯 Focus on Launches", value=False)
    origins = st.multiselect("Origins:", list(brands_by_origin.keys()), default=["China"])
    available = [b for o in origins for b in brands_by_origin[o]]
    brand_selection = st.multiselect("Brands:", available, default=["OMODA&JAECOO", "BYD"])
    
    st.divider()
    st.subheader("📊 Research Topics")
    topic_selection = st.multiselect(
        "Strategic Themes:",
        list(research_topics.keys()),
        default=[],
        format_func=lambda x: f"{research_topics[x]['emoji']} {x}"
    )
    
    st.divider()
    # PERÍODO: Default = Ontem a Ontem
    yesterday = datetime.now() - timedelta(days=1)
    date_range = st.date_input("Period:", value=(yesterday, yesterday))

    # STEP 1: ONLY FETCH LINKS
    if st.button("🚀 1. Fetch News Links"):
        if gemini_api_key and len(date_range) == 2:
            st.session_state.dossier_data = {} # Clear old AI summaries
            
            d_ini, d_end = date_range
            
            # Salva no estado para o Lark
            st.session_state.d_ini_str = d_ini.strftime('%m/%d')
            st.session_state.d_end_str = d_end.strftime('%m/%d')

            results_raw = {}
            launch_keywords = " (lançamento OR segredo OR flagra OR novidade OR \"modelo 2027\" OR \"modelo 2026\")"
            media_filter = " (site:g1.globo.com OR site:uol.com.br OR site:estadao.com.br OR site:folha.uol.com.br OR site:quatrorodas.abril.com.br OR site:autoesporte.globo.com OR site:motor1.uol.com.br)"
            
            with st.spinner("Fetching RSS feeds..."):
                # === BRANDS (Omoda & Jaecoo always first) ===
                sorted_brands = sort_priority_brands(brand_selection)
                
                for brand in sorted_brands:
                    # Special combined search for OMODA&JAECOO
                    if brand == "OMODA&JAECOO":
                        q_base = '("Omoda" OR "Jaecoo") Brasil'
                        title_match = lambda t: ("omoda" in t.lower()) or ("jaecoo" in t.lower())
                    else:
                        q_base = f"\"{brand}\" Brasil"
                        title_match = lambda t, b=brand: b.lower() in t.lower()
                    
                    q = q_base + (launch_keywords if target_launch else "") + media_filter
                    full_q = f"{q} after:{d_ini.strftime('%Y-%m-%d')} before:{d_end.strftime('%Y-%m-%d')}"
                    feed = feedparser.parse(f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(full_q)}&hl=pt-BR&gl=BR")
                    
                    brand_news = []
                    for entry in feed.entries:
                        if title_match(entry.title):
                            if hasattr(entry, 'published_parsed'):
                                # STRICT DATE CHECK
                                pub_date = datetime.fromtimestamp(mktime(entry.published_parsed)).date()
                                if not (d_ini <= pub_date <= d_end): continue
                            else:
                                continue
                            
                            en_title = safe_translate(entry.title, 'en')
                            zh_title = safe_translate(entry.title, 'zh-CN')
                            brand_news.append({
                                "title": f"{en_title} / {zh_title}",
                                "link": entry.link
                            })
                    if brand_news: results_raw[brand] = brand_news[:10] # Limit to 10 max
                
                # === RESEARCH TOPICS (no brand-name title filter) ===
                for topic in topic_selection:
                    topic_q = research_topics[topic]["query"] + media_filter
                    full_q = f"{topic_q} after:{d_ini.strftime('%Y-%m-%d')} before:{d_end.strftime('%Y-%m-%d')}"
                    feed = feedparser.parse(f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(full_q)}&hl=pt-BR&gl=BR")
                    
                    topic_news = []
                    for entry in feed.entries:
                        if hasattr(entry, 'published_parsed'):
                            pub_date = datetime.fromtimestamp(mktime(entry.published_parsed)).date()
                            if not (d_ini <= pub_date <= d_end): continue
                        else:
                            continue
                        
                        en_title = safe_translate(entry.title, 'en')
                        zh_title = safe_translate(entry.title, 'zh-CN')
                        topic_news.append({
                            "title": f"{en_title} / {zh_title}",
                            "link": entry.link
                        })
                    if topic_news: results_raw[topic] = topic_news[:10]
            
            st.session_state.raw_fetched_news = results_raw

# --- 4. SELECTION & AI PROCESSING AREA ---
if st.session_state.raw_fetched_news and not st.session_state.dossier_data:
    st.header("📝 2. Select News for AI Processing")
    st.info("Check the news you want to analyze. This prevents unnecessary API costs.")
    
    selected_to_process = {}
    
    for brand, items in st.session_state.raw_fetched_news.items():
        emoji = get_section_emoji(brand)
        st.subheader(f"{emoji} {brand.upper()}")
        selected_to_process[brand] = []
        for idx, item in enumerate(items):
            # TELA 2: Checkbox de um lado, Título EMBEDDADO COM LINK do outro
            col_cb, col_text = st.columns([0.05, 0.95])
            with col_cb:
                if st.checkbox("", value=False, key=f"raw_select_{brand}_{idx}"):
                    selected_to_process[brand].append(item)
            with col_text:
                st.markdown(f"**[{item['title']}]({item['link']})**")

    st.divider()
    if st.button("🧠 3. Summarize Selected with AI"):
        has_selections = sum(len(news) for news in selected_to_process.values())
        if has_selections == 0:
            st.warning("Please select at least one article to process.")
        else:
            final_dossier = {}
            with st.spinner("Agent is extracting and summarizing..."):
                for brand, items in selected_to_process.items():
                    if items:
                        final_dossier[brand] = []
                        for it in items:
                            texto_raw = extrair_texto_da_noticia(it['link'])
                            resumo = resumir_noticia_com_gemini(texto_raw, gemini_api_key)
                            time.sleep(2)
                            final_dossier[brand].append({
                                "title": it['title'],
                                "link": it['link'],
                                "summary": resumo
                            })
            st.session_state.dossier_data = final_dossier
            st.rerun()

# --- 5. EDITING & UNIFIED PDF EXPORT ---
if st.session_state.dossier_data:
    st.header("📑 4. Curate Insights & Download")
    st.info("Review the summaries below before exporting.")
    
    for brand, items in st.session_state.dossier_data.items():
        emoji = get_section_emoji(brand)
        st.subheader(f"{emoji} {brand.upper()}")
        for idx, item in enumerate(items):
            st.checkbox(f"✅ Include in Final PDF ({brand}-{idx+1})", value=True, key=f"keep_{brand}_{idx}")
            st.markdown(f"**Source:** [{item['title']}]({item['link']})")
            st.session_state.dossier_data[brand][idx]['summary'] = st.text_area(f"Edit {brand}-{idx}", value=item['summary'], height=200, key=f"edit_{brand}_{idx}", label_visibility="collapsed")

    st.divider()
    
    # UNIFICAÇÃO: Calcula dados na hora e já oferece o Botão de Download (Elimina o click extra)
    count = sum(1 for brand, items in st.session_state.dossier_data.items() for idx in range(len(items)) if st.session_state.get(f"keep_{brand}_{idx}"))
    
    if count == 0:
        st.warning("Please check at least one article to include in the PDF.")
    else:
        try:
            pdf_bytes = gerar_pdf_bytes(st.session_state.dossier_data, st.session_state)
            
            # Botão 5 de Download gerando arquivo com Nome = Automotive_Pulse_DDMMYY.pdf
            nome_arquivo = f"Automotive_Pulse_{datetime.now().strftime('%d%m%y')}.pdf"
            
            st.download_button(
                label="📥 5. Download Final PDF Dossier", 
                data=bytes(pdf_bytes), 
                file_name=nome_arquivo, 
                mime="application/pdf"
            )
        except Exception as e:
            st.error(f"Failed to generate PDF: {e}")

    # --- LARK SYNC (HEADER RESTAURADO) ---
    st.markdown("---")
    st.markdown("### 📤 6. Send to Lark / Feishu")
    link_nuvem = st.text_input("🔗 Paste the public cloud link here:")
    
    if st.button("🚀 Push to Lark"):
        if link_nuvem:
            # LARK CARD BILINGUAL & COM CABEÇALHO RESTAURADO
            lark_elements = [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"📍 **Focus Country / 重点国家:** Brazil / 巴西\n📅 **Period / 期间:** {st.session_state.d_ini_str} to {st.session_state.d_end_str}"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**⭐✨ [CLICK TO ACCESS FULL DOSSIER / 点击获取完整档案]({link_nuvem}) ✨⭐**"}},
                {"tag": "hr"}
            ]
            
            for brand, items in st.session_state.dossier_data.items():
                kept = [it for idx, it in enumerate(items) if st.session_state.get(f"keep_{brand}_{idx}")]
                if kept:
                    # Add topic emoji prefix when it's a research theme
                    if is_topic(brand):
                        section_title = f"{research_topics[brand]['emoji']} {brand.upper()}"
                    else:
                        section_title = brand.upper()
                    
                    links_md = [f"• [{it['title']}]({it['link']})" for it in kept]
                    lark_elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**{section_title}**\n" + "\n".join(links_md)}})
                    lark_elements.append({"tag": "hr"})

            payload = {
                "msg_type": "interactive",
                "card": {
                    "header": {"title": {"tag": "plain_text", "content": "🚗 Automotive Market Intelligence"}, "template": "blue"},
                    "elements": lark_elements
                }
            }
            
            requests.post(WEBHOOK_URL, json=payload)
            st.success("Successfully sent to Lark!")
            st.balloons()
