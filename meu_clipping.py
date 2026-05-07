import streamlit as st
import feedparser
import requests
import urllib.parse
import re 
import json
import html
import time
import os # Para gerenciar caminhos de pastas no Windows
from datetime import datetime, timedelta, time as dt_time
from time import mktime
from deep_translator import GoogleTranslator
import trafilatura 
import google.generativeai as genai
from googlenewsdecoder import gnewsdecoder
from xhtml2pdf import pisa # Biblioteca para conversão de PDF

# --- 1. CORE CONFIGURATION ---
st.set_page_config(page_title="🚗 Automotive Pulse Digest", layout="wide")
st.title("🚗 Automotive Pulse Digest")
st.markdown("🚗 Automotive Pulse Digest")

# Caminho específico para o seu usuário
DOWNLOAD_PATH = r"C:\Users\BR000073\Downloads"
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
                resultado = gnewsdecoder(url)
                if isinstance(resultado, dict) and resultado.get("status"):
                    url = resultado.get("decoded_url")
                elif isinstance(resultado, str) and resultado.startswith("http"):
                    url = resultado
            except Exception:
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
                return f"- Acesso liberado ao site ({dominio_real}), mas a IA não achou texto longo o suficiente. -"
        else:
            return f"- O site ({dominio_real}) bloqueou o robô (Erro HTTP {resposta.status_code}). -"
            
    except requests.exceptions.Timeout:
        return "- O site final demorou muito para responder e derrubou a conexão do Agente. -"
    except Exception as e:
        return f"- Erro fatal na conexão com o site: {e} -"

# --- FUNÇÃO DO AGENTE VIRTUAL (RESUMO GEMINI COM OTIMIZAÇÃO DE CUSTO) ---
def resumir_noticia_com_gemini(texto, api_key):
    if not api_key:
        return "- Erro: Chave de API não encontrada nos Secrets. -"
    
    if "Erro:" in texto or "- Acesso liberado" in texto or "- O site" in texto:
        return f"- Falha Técnica na Leitura: {texto} -"
        
    try:
        genai.configure(api_key=api_key)
        
        # Role & Instructions estáticas (Otimiza Caching/Custo)
        instructions = """
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
        
        # Busca dinâmica do melhor modelo (Prioriza Flash que é mais barato)
        model_name = None
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'flash' in m.name.lower():
                    model_name = m.name
                    break
        
        if not model_name: model_name = 'gemini-1.5-flash'

        # Instancia com as instruções de sistema
        model = genai.GenerativeModel(model_name=model_name, system_instruction=instructions)
        
        # Trunking: Envia apenas os primeiros 6000 caracteres (economia de tokens)
        texto_otimizado = texto[:6000]
        
        # Lógica de retentativa para erro 429
        for tentativa in range(3):
            try:
                response = model.generate_content(texto_otimizado)
                return response.text.strip()
            except Exception as api_error:
                if "429" in str(api_error):
                    time.sleep(12)
                    continue
                return f"- Erro da API: {api_error} -"
                    
    except Exception as e:
        return f"- Erro na configuração: {e} -"

# --- Função para salvar o PDF fisicamente no Windows ---
def salvar_pdf_localmente(html_content, filename):
    if not os.path.exists(DOWNLOAD_PATH):
        return False, f"Caminho {DOWNLOAD_PATH} não encontrado no seu computador."
    
    full_path = os.path.join(DOWNLOAD_PATH, filename.replace(".html", ".pdf"))
    try:
        with open(full_path, "wb") as f:
            pisa_status = pisa.CreatePDF(html_content, dest=f)
        return not pisa_status.err, full_path
    except Exception as e:
        return False, str(e)

# --- 2. SESSION STATE ---
if 'dossier_data' not in st.session_state:
    st.session_state.dossier_data = {}

if 'step1_complete' not in st.session_state:
    st.session_state.step1_complete = False

# --- CAPTURA DA CHAVE DE API VIA SECRETS ---
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
    if gemini_api_key: st.success("✅ IA Conectada")
    else: st.error("⚠️ Sem GEMINI_API_KEY!")
    st.divider()
    target_launch = st.checkbox("🎯 Focar em Lançamentos/Segredos", value=False)
    origins = st.multiselect("Origins:", list(brands_by_origin.keys()), default=["China"])
    available = []
    for o in origins: available.extend(brands_by_origin[o])
    brand_selection = st.multiselect("Brands:", available, default=["Omoda", "Jaecoo", "BYD"])
    today = datetime.now()
    date_range = st.date_input("Period:", value=(today - timedelta(days=7), today))

    if st.button("🚀 1. Fetch News Links"):
        if not gemini_api_key:
            st.error("Configure sua chave de API nos Secrets.")
        elif len(date_range) == 2:
            st.session_state.step1_complete = False
            for key in list(st.session_state.keys()):
                if key.startswith("keep_"): del st.session_state[key]

            d_ini, d_end = date_range
            start_datetime = datetime.combine(d_ini, dt_time.min)
            end_datetime = datetime.combine(d_end, dt_time.max)
            results = {}
            launch_keywords = " (lançamento OR segredo OR flagra OR novidade OR \"modelo 2027\" OR \"modelo 2026\")"
            media_filter = " (site:g1.globo.com OR site:uol.com.br OR site:estadao.com.br OR site:folha.uol.com.br OR site:quatrorodas.abril.com.br OR site:autoesporte.globo.com OR site:motor1.uol.com.br)"
            
            with st.spinner("Agent is working: Fetching, Reading and Summarizing..."):
                for brand in brand_selection:
                    base_q = f"\"{brand}\" Brasil"
                    if target_launch: base_q += launch_keywords
                    base_q += media_filter
                    full_q = f"{base_q} after:{d_ini.strftime('%Y-%m-%d')} before:{d_end.strftime('%Y-%m-%d')}"
                    safe_q = urllib.parse.quote_plus(full_q)
                    feed = feedparser.parse(f"https://news.google.com/rss/search?q={safe_q}&hl=pt-BR&gl=BR")
                    
                    if feed.entries:
                        brand_news = []
                        for entry in feed.entries:
                            if brand.lower() in entry.title.lower():
                                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                                    pub_date = datetime.fromtimestamp(mktime(entry.published_parsed))
                                    if not (start_datetime <= pub_date <= end_datetime): continue
                                else: continue
                                
                                en_title = safe_translate(entry.title, 'en')
                                zh_title = safe_translate(entry.title, 'zh-CN')
                                
                                # AÇÃO DO AGENTE VIRTUAL
                                texto_full = extrair_texto_da_noticia(entry.link)
                                resumo = resumir_noticia_com_gemini(texto_full, gemini_api_key)
                                time.sleep(2) # Pausa de segurança

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
    st.info("Resumos gerados automaticamente. Revise e selecione os itens para o PDF.")
    
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
            keep_checkbox = st.checkbox(f"✅ Incluir no Dossiê Final", value=False, key=f"keep_{brand}_{idx}")
            st.markdown(f"**Source:** [{item['title']}]({item['link']})")
            st.session_state.dossier_data[brand][idx]['summary'] = st.text_area(label=f"Edit {brand}-{idx}", value=item['summary'], height=250, key=f"edit_{brand}_{idx}", label_visibility="collapsed")

    # --- 5. FINALIZATION & PDF (A MUDANÇA ESTÁ AQUI) ---
    st.divider()
    
    if st.button("📄 3. Gerar PDF e Salvar em Downloads"):
        # Estilo HTML para o PDF
        html_content = """<html><head><meta charset="UTF-8"><style>body { font-family: Arial, sans-serif; padding: 30px; } h1 { color: #1a237e; } h2 { color: #0d47a1; border-bottom: 2px solid #eee; } .card { margin-bottom: 25px; padding: 15px; border: 1px solid #eee; }</style></head><body>"""
        html_content += f"<h1>Automotive Market Intelligence Dossier</h1><p><b>Researcher:</b> Matheus Cardinali</p><hr>"
        
        feishu_base = []
        has_any_content = False
        
        for brand, items in st.session_state.dossier_data.items():
            kept_items = [item for idx, item in enumerate(items) if st.session_state.get(f"keep_{brand}_{idx}", False)]
            if kept_items: 
                has_any_content = True
                html_content += f"<h2>{brand.upper()}</h2>"
                links_md = []
                for item in kept_items:
                    html_content += f"<div class='card'><p><b>{item['title']}</b></p><p>{item['summary']}</p></div>"
                    links_md.append(f"• [{item['title']}]({item['link']})")
                feishu_base.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**{brand.upper()}**\n" + "\n".join(links_md)}})

        html_content += "</body></html>"
        
        if has_any_content:
            filename = f"Automotive_Dossier_{datetime.now().strftime('%d%m_%H%M')}.pdf"
            # CHAMADA DA FUNÇÃO DE SALVAMENTO AUTOMÁTICO
            sucesso, caminho_ou_erro = salvar_pdf_localmente(html_content, filename)
            
            if sucesso:
                st.success(f"✅ PDF salvo com sucesso em: {caminho_ou_erro}")
                st.session_state.feishu_elements_base = feishu_base
                st.session_state.step1_complete = True
            else:
                st.error(f"Erro ao salvar PDF: {caminho_ou_erro}")
        else:
            st.warning("Selecione notícias antes de gerar o relatório.")

    # --- ETAPA LARK ---
    if st.session_state.get('step1_complete', False):
        st.markdown("### 📤 4. Envio para o Lark / Feishu")
        user_report_url = st.text_input("🔗 Cole o link público do PDF hospedado na nuvem:")
        
        if st.button("🚀 Confirmar Link e Enviar Lark Card"):
            if user_report_url.strip() != "":
                payload = {
                    "msg_type": "interactive",
                    "card": {
                        "header": {"title": {"tag": "plain_text", "content": "🚗 Automotive Market Intelligence Dossier"}, "template": "blue"},
                        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"**⭐✨ [ACCESS FULL NEWS SUMMARY]({user_report_url.strip()}) ✨⭐**"}}, {"tag": "hr"}] + st.session_state.feishu_elements_base
                    }
                }
                res = requests.post(WEBHOOK_URL, json=payload)
                if res.status_code == 200:
                    st.success("Dossier enviado ao Lark!")
                    st.balloons()
