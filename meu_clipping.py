import streamlit as st
import feedparser
import requests
import urllib.parse
import re 
from datetime import datetime, timedelta, time as dt_time
from time import mktime
from deep_translator import GoogleTranslator
import trafilatura 
import google.generativeai as genai # <-- IMPORT DO GEMINI

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

# --- FUNÇÃO DO AGENTE VIRTUAL (EXTRAÇÃO AVANÇADA) ---
def extrair_texto_da_noticia(url):
    try:
        # Criando o nosso "disfarce" de navegador real
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
            'Referer': 'https://news.google.com/'
        }
        
        # O requests tenta entrar no site com o disfarce (espera no máximo 15 segundos)
        resposta = requests.get(url, headers=headers, timeout=15)
        
        # Se o site deixou a gente entrar (Status 200 = OK)
        if resposta.status_code == 200:
            # Passamos o HTML sujo para o trafilatura limpar e pegar só a notícia
            texto = trafilatura.extract(resposta.text)
            return texto if texto else "Erro: Conteúdo não encontrado no HTML."
        else:
            return f"Erro: O site bloqueou o robô. Código HTTP: {resposta.status_code}"
            
    except requests.exceptions.Timeout:
        return "Erro: O site demorou muito para responder."
    except Exception as e:
        return f"Erro na extração: {e}"
# -----------------------------------------------------------------------------

# --- FUNÇÃO DO AGENTE VIRTUAL (RESUMO GEMINI) ---
def resumir_noticia_com_gemini(texto, api_key):
    if not api_key:
        return "- Erro: Chave de API não encontrada nos Secrets. -"
    
    if "Erro:" in texto:
        return "- Não foi possível extrair texto legível para resumir. O site pode ter bloqueado o acesso. -"
        
    try:
        genai.configure(api_key=api_key)
        # O modelo flash é a melhor opção para curadoria rápida de textos
        model = genai.GenerativeModel('gemini-1.5-flash')
        
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
        
        prompt = f"{system_instruction}\n\n--- NEWS ARTICLE TEXT ---\n{texto}"
        
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"- Erro ao gerar resumo com a IA: {e} -"

# --- 2. SESSION STATE ---
if 'dossier_data' not in st.session_state:
    st.session_state.dossier_data = {}

if 'step1_complete' not in st.session_state:
    st.session_state.step1_complete = False

# --- CAPTURA DA CHAVE DE API VIA SECRETS ---
# Tenta buscar a chave. Se não achar, retorna string vazia.
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
    
    # Validação visual da API Key
    if gemini_api_key:
        st.success("✅ Agente de IA Conectado (API Key Segura)")
    else:
        st.error("⚠️ Falta configurar a GEMINI_API_KEY nos secrets do Streamlit!")
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
            st.error("Por favor, configure sua chave de API nos Secrets antes de rodar o agente.")
        elif len(date_range) == 2:
            st.session_state.step1_complete = False
            for key in list(st.session_state.keys()):
                if key.startswith("keep_"):
                    del st.session_state[key]

            d_ini, d_end = date_range
            start_datetime = datetime.combine(d_ini, dt_time.min)
            end_datetime = datetime.combine(d_end, dt_time.max)
            
            results = {}
            launch_keywords = " (lançamento OR segredo OR flagra OR novidade OR \"modelo 2027\" OR \"modelo 2026\")"
            media_filter = " (site:g1.globo.com OR site:uol.com.br OR site:estadao.com.br OR site:folha.uol.com.br OR site:quatrorodas.abril.com.br OR site:autoesporte.globo.com OR site:motor1.uol.com.br)"
            
            with st.spinner("Agent is working: Fetching links, extracting full text, and generating AI summaries..."):
                for brand in brand_selection:
                    base_q = f"\"{brand}\" Brasil"
                    if target_launch:
                        base_q += launch_keywords
                    
                    base_q += media_filter
                    full_q = f"{base_q} after:{d_ini.strftime('%Y-%m-%d')} before:{d_end.strftime('%Y-%m-%d')}"
                    
                    safe_q = urllib.parse.quote_plus(full_q)
                    feed = feedparser.parse(f"https://news.google.com/rss/search?q={safe_q}&hl=pt-BR&gl=BR")
                    
                    if feed.entries:
                        brand_news = []
                        safe_brand_name = brand.lower()
                        
                        for entry in feed.entries:
                            if safe_brand_name in entry.title.lower():
                                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                                    pub_date = datetime.fromtimestamp(mktime(entry.published_parsed))
                                    if not (start_datetime <= pub_date <= end_datetime):
                                        continue
                                else:
                                    continue
                                
                                en_title = safe_translate(entry.title, 'en')
                                zh_title = safe_translate(entry.title, 'zh-CN')
                                final_title = f"{en_title} / {zh_title}"

                                # AGENTE VIRTUAL: Extrai o texto da matéria
                                conteudo_completo = extrair_texto_da_noticia(entry.link)
                                
                                # AGENTE VIRTUAL: Gera o resumo com o Gemini
                                resumo_gerado = resumir_noticia_com_gemini(conteudo_completo, gemini_api_key)

                                brand_news.append({
                                    "title": final_title, 
                                    "link": entry.link, 
                                    "full_text": conteudo_completo, 
                                    "summary": resumo_gerado 
                                })
                                
                        if brand_news: 
                            results[brand] = brand_news
                            
            st.session_state.dossier_data = results

# --- 4. EDITING AREA ---
if st.session_state.dossier_data:
    
    st.header("📝 2. Curate Insights")
    st.info("O Agente Virtual preencheu os resumos abaixo. Selecione as melhores notícias para o dossiê, revise o texto e avance.")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ Select All / 全选"):
            for brand, items in st.session_state.dossier_data.items():
                for idx in range(len(items)):
                    st.session_state[f"keep_{brand}_{idx}"] = True
    with col2:
        if st.button("❌ Deselect All / 取消全选"):
            for brand, items in st.session_state.dossier_data.items():
                for idx in range(len(items)):
                    st.session_state[f"keep_{brand}_{idx}"] = False
    
    st.divider()

    for brand, items in st.session_state.dossier_data.items():
        st.subheader(f"🏎️ {brand.upper()}")
        
        for idx, item in enumerate(items):
            keep_checkbox = st.checkbox(f"✅ Incluir no Dossiê Final / 包含在最终档案中", value=False, key=f"keep_{brand}_{idx}")
            
            st.markdown(f"**Source / 来源:** [{item['title']}]({item['link']})")
            
            # Text area agora vem populado com o resumo bilíngue do Gemini
            st.session_state.dossier_data[brand][idx]['summary'] = st.text_area(
                label=f"Edit Notes: {brand} - {idx}",
                value=item['summary'],
                height=250, 
                key=f"edit_{brand}_{idx}",
                label_visibility="collapsed"
            )

    # --- 5. FINALIZATION & EXPORT ---
    st.divider()
    
    if st.button("📄 3. ETAPA 1: Gerar Relatório HTML"):
        d_ini_str = date_range[0].strftime('%m/%d')
        d_end_str = date_range[1].strftime('%m/%d')
        
        html_content = f"""
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: 'Segoe UI', Arial; padding: 40px; color: #333; line-height: 1.6; }}
                .header-box {{ border-bottom: 3px solid #1a237e; padding-bottom: 20px; margin-bottom: 30px; }}
                .brand-sec {{ margin-top: 40px; border-left: 5px solid #1a237e; padding-left: 20px; }}
                h2 {{ color: #1a237e; text-transform: uppercase; border-bottom: 1px solid #ccc; padding-bottom: 5px; }}
                .news-card {{ background: #f8f9fa; padding: 20px; margin-bottom: 20px; border-radius: 8px; border: 1px solid #eee; }}
                .news-title {{ font-weight: bold; font-size: 18px; color: #0056b3; text-decoration: none; display: block; margin-bottom: 10px; }}
                p {{ text-align: justify; font-size: 15px; white-space: pre-wrap; }}
            </style>
        </head>
        <body>
            <div class="header-box">
                <h1 style="color: #1a237e; margin-bottom: 10px;">Automotive Market Intelligence Dossier / 汽车市场情报档案</h1>
                <p style="margin: 5px 0; font-size: 16px;"><strong>📍 Focus Country / 重点国家:</strong> Brazil / 巴西</p>
                <p style="margin: 5px 0; font-size: 16px;"><strong>📅 Period / 期间:</strong> {d_ini_str} to {d_end_str}</p>
                <p style="margin: 5px 0; font-size: 16px;"><strong>👤 Researcher / 研究员:</strong> Matheus Cardinali</p>
            </div>
        """
        
        feishu_elements_base = [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"📍 **Focus Country / 重点国家:** Brazil / 巴西\n📅 **Period / 期间:** {d_ini_str} to {d_end_str}"}
            },
            {"tag": "hr"}
        ]
        
        has_any_content = False
        
        for brand, items in st.session_state.dossier_data.items():
            kept_items = [item for idx, item in enumerate(items) if st.session_state.get(f"keep_{brand}_{idx}", False)]
            
            if kept_items: 
                has_any_content = True
                html_content += f"<div class='brand-sec'><h2>{brand}</h2>"
                links_md = []
                
                for item in kept_items:
                    item_title = item.get('title', 'Link da Notícia / 新闻链接')
                    item_link = item.get('link', '#')
                    item_summary = item.get('summary', '')

                    html_content += f"""
                    <div class='news-card'>
                        <a class='news-title' href='{item_link}'>{item_title}</a>
                        <p>{item_summary}</p>
                    </div>
                    """
                    links_md.append(f"• [{item_title}]({item_link})")
                
                html_content += "</div>"
                feishu_elements_base.append({
                    "tag": "div", 
                    "text": {"tag": "lark_md", "content": f"**{brand.upper()}**\n" + "\n".join(links_md)}
                })
                feishu_elements_base.append({"tag": "hr"})

        html_content += "</body></html>"
        
        if has_any_content:
            st.session_state.html_content = html_content
            st.session_state.feishu_elements_base = feishu_elements_base
            st.session_state.filename = f"Automotive_Dossier_{d_ini_str.replace('/','')}.html"
            st.session_state.step1_complete = True
        else:
            st.warning("No news selected. Please check the boxes of the news you want to include. / 未选择任何新闻。请勾选您要包含在档案中的新闻。")
            st.session_state.step1_complete = False

    if st.session_state.get('step1_complete', False):
        st.success("👉 Relatório HTML gerado com sucesso! Salve-o como PDF, faça o upload na sua nuvem e cole o link público abaixo para gerar o Lark Card:")
        
        st.download_button("📥 Download Final Dossier (HTML)", st.session_state.html_content, file_name=st.session_state.filename, mime="text/html")
        
        st.markdown("### 📤 4. ETAPA 2: Envio para o Lark / Feishu")
        
        user_report_url = st.text_input("🔗 Cole o link público do PDF hospedado na nuvem:")
        
        if st.button("🚀 Confirmar Link e Enviar Lark Card"):
            if user_report_url.strip() == "":
                st.error("Por favor, cole um link válido antes de enviar.")
            else:
                top_link_element = {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md", 
                        "content": f"**⭐✨ [CLICK HERE TO ACCESS THE FULL NEWS SUMMARY]({user_report_url.strip()}) ✨⭐**\n**⭐✨ [点击此处访问完整新闻摘要]({user_report_url.strip()}) ✨⭐**"
                    }
                }
                
                final_feishu_elements = [top_link_element, {"tag": "hr"}] + st.session_state.feishu_elements_base
                
                payload = {
                    "msg_type": "interactive",
                    "card": {
                        "header": {"title": {"tag": "plain_text", "content": "🚗 Automotive Market Intelligence Dossier / 汽车市场情报档案"}, "template": "blue"},
                        "elements": final_feishu_elements
                    }
                }
                
                res = requests.post(WEBHOOK_URL, json=payload)
                if res.status_code == 200:
                    st.success("Dossier finalized and links synced to Feishu! / 档案已完成并同步至Feishu！")
                    st.balloons()
                else:
                    st.error(f"Erro ao enviar para o Lark. Código: {res.status_code}")
