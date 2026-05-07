import streamlit as st
import feedparser
import requests
import urllib.parse
import re # Importação de Regex para tratamento de links
from datetime import datetime, timedelta, time as dt_time
from time import mktime
from deep_translator import GoogleTranslator
import trafilatura # <-- IMPORT ADICIONADO PARA O AGENTE VIRTUAL

# --- 1. CORE CONFIGURATION ---
st.set_page_config(page_title="🚗 Automotive Pulse Digest", layout="wide")
st.title("🚗 Automotive Pulse Digest")
st.markdown("🚗 Automotive Pulse Digest")

# Feishu Webhook
WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/8f561d21-2a4c-4726-bff3-c0bf5d9c35a5"

# --- MODIFICAÇÃO: Variável REPORT_URL removida do escopo global ---
# O link agora será inserido dinamicamente pelo usuário na ETAPA 2.
# ------------------------------------------------------------------

# --- Função segura de tradução (Ignora links e trata erros) ---
def safe_translate(text, target_lang):
    try:
        # Encontra todos os links brutos no texto (começando com http ou https)
        urls = re.findall(r'(https?://[^\s]+)', text)
        
        # Substitui os links por placeholders temporários para proteger na tradução
        temp_text = text
        for i, url in enumerate(urls):
            temp_text = temp_text.replace(url, f"[[URL_{i}]]")
        
        # Realiza a tradução do texto limpo
        translated_text = GoogleTranslator(source='auto', target=target_lang).translate(temp_text)
        
        # Restaura os links originais no texto traduzido
        for i, url in enumerate(urls):
            translated_text = translated_text.replace(f"[[URL_{i}]]", url)
            
        return translated_text
    except Exception as e:
        # Fallback: retorna o texto original em caso de qualquer falha de API ou parsing
        return text
# -----------------------------------------------------------------------------

# --- FUNÇÃO DO AGENTE VIRTUAL (EXTRAÇÃO DE TEXTO) ---
def extrair_texto_da_noticia(url):
    """Acessa o link e extrai apenas o corpo da notícia."""
    try:
        html_baixado = trafilatura.fetch_url(url)
        if html_baixado:
            # extract() limpa os menus e propagandas
            texto = trafilatura.extract(html_baixado)
            return texto if texto else "Erro: Conteúdo não encontrado no HTML."
        return "Erro: Falha ao acessar a página (possível bloqueio/paywall)."
    except Exception as e:
        return f"Erro na extração: {e}"
# -----------------------------------------------------------------------------

# --- 2. SESSION STATE ---
if 'dossier_data' not in st.session_state:
    st.session_state.dossier_data = {}

# --- MODIFICAÇÃO: Estado para controlar a "Pausa" entre a Etapa 1 e Etapa 2 ---
if 'step1_complete' not in st.session_state:
    st.session_state.step1_complete = False
# ------------------------------------------------------------------------------

# --- 3. SIDEBAR ---
brands_by_origin = {
    "China": ["Omoda", "Jaecoo", "BYD", "GWM", "Zeekr", "GAC", "Geely", "Leapmotor", "Chery"],
    "Germany": ["Volkswagen", "BMW", "Mercedes-Benz", "Audi", "Porsche"],
    "USA": ["Chevrolet", "Ford", "Tesla", "Ram", "Jeep"],
    "Japan": ["Toyota", "Honda", "Nissan", "Mitsubishi", "Subaru"]
}

with st.sidebar:
    st.header("⚙️ Market Parameters")
    
    # --- CORREÇÃO AQUI: Mudado para value=False ---
    target_launch = st.checkbox("🎯 Focar em Lançamentos/Segredos", value=False)
    
    origins = st.multiselect("Origins:", list(brands_by_origin.keys()), default=["China"])
    available = []
    for o in origins: available.extend(brands_by_origin[o])
    brand_selection = st.multiselect("Brands:", available, default=["Omoda", "Jaecoo", "BYD"])
    today = datetime.now()
    date_range = st.date_input("Period:", value=(today - timedelta(days=7), today))

    if st.button("🚀 1. Fetch News Links"):
        if len(date_range) == 2:
            # Limpa os estados de seleção e redefine a etapa 1 ao iniciar nova busca
            st.session_state.step1_complete = False
            for key in list(st.session_state.keys()):
                if key.startswith("keep_"):
                    del st.session_state[key]

            d_ini, d_end = date_range
            
            # Criação dos limites exatos de data (00:00:00 até 23:59:59)
            start_datetime = datetime.combine(d_ini, dt_time.min)
            end_datetime = datetime.combine(d_end, dt_time.max)
            
            results = {}
            
            launch_keywords = " (lançamento OR segredo OR flagra OR novidade OR \"modelo 2027\" OR \"modelo 2026\")"
            # Filtro de Grandes Mídias
            media_filter = " (site:g1.globo.com OR site:uol.com.br OR site:estadao.com.br OR site:folha.uol.com.br OR site:quatrorodas.abril.com.br OR site:autoesporte.globo.com OR site:motor1.uol.com.br)"
            
            with st.spinner("Fetching headlines, filtering dates, translating, and reading content..."):
                for brand in brand_selection:
                    base_q = f"\"{brand}\" Brasil"
                    if target_launch:
                        base_q += launch_keywords
                    
                    # Aplicando o filtro de mídia à query base
                    base_q += media_filter
                    
                    # O Google News ainda recebe o filtro para reduzir o volume inicial
                    full_q = f"{base_q} after:{d_ini.strftime('%Y-%m-%d')} before:{d_end.strftime('%Y-%m-%d')}"
                    
                    safe_q = urllib.parse.quote_plus(full_q)
                    feed = feedparser.parse(f"https://news.google.com/rss/search?q={safe_q}&hl=pt-BR&gl=BR")
                    
                    if feed.entries:
                        brand_news = []
                        safe_brand_name = brand.lower()
                        
                        for entry in feed.entries:
                            # STRICT FILTER: The brand must actually be in the headline to avoid Google News "spiraling"
                            if safe_brand_name in entry.title.lower():
                                
                                # Validação Rigorosa de Datas no Python
                                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                                    pub_date = datetime.fromtimestamp(mktime(entry.published_parsed))
                                    # Descarte da notícia se estiver fora do intervalo estrito
                                    if not (start_datetime <= pub_date <= end_datetime):
                                        continue
                                else:
                                    # Ignorar se a notícia não trouxer a data por alguma falha do RSS
                                    continue
                                
                                # Uso da nova função segura de tradução
                                en_title = safe_translate(entry.title, 'en')
                                zh_title = safe_translate(entry.title, 'zh-CN')
                                final_title = f"{en_title} / {zh_title}"

                                # --- AÇÃO DO AGENTE: Extraindo o texto completo da notícia ---
                                conteudo_completo = extrair_texto_da_noticia(entry.link)

                                brand_news.append({
                                    "title": final_title, 
                                    "link": entry.link, 
                                    "full_text": conteudo_completo, # <-- TEXTO ARMAZENADO AQUI
                                    "summary": "- Insert Comments Here -"
                                })
                                
                        if brand_news: # Only add the brand if we found actual relevant news
                            results[brand] = brand_news
                            
            st.session_state.dossier_data = results

# --- 4. EDITING AREA ---
if st.session_state.dossier_data:
    
    st.header("📝 2. Curate Insights")
    st.info("Selecione as notícias que servirão para o dossiê e cole suas análises nas caixas de texto.")
    
    # Botões: Selecionar/Desmarcar Tudo
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
            # Checkbox para permitir ao usuário vetar/incluir a notícia (Padrão: False/Desmarcado)
            keep_checkbox = st.checkbox(f"✅ Incluir no Dossiê Final / 包含在最终档案中", value=False, key=f"keep_{brand}_{idx}")
            
            st.markdown(f"**Source / 来源:** [{item['title']}]({item['link']})")
            
            # Text area remains the same, but we save its output to session state dynamically
            st.session_state.dossier_data[brand][idx]['summary'] = st.text_area(
                label=f"Edit Notes: {brand} - {idx}",
                value=item['summary'],
                height=150, 
                key=f"edit_{brand}_{idx}",
                label_visibility="collapsed"
            )

    # --- 5. FINALIZATION & EXPORT (NOVO FLUXO EM 2 ETAPAS) ---
    st.divider()
    
    # --- MODIFICAÇÃO: ETAPA 1 - Apenas gera o HTML ---
    if st.button("📄 3. ETAPA 1: Gerar Relatório HTML"):
        d_ini_str = date_range[0].strftime('%m/%d')
        d_end_str = date_range[1].strftime('%m/%d')
        
        # Cabeçalhos Bilíngues
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
        
        # Payload base do Feishu Bilíngue (sem o link do topo ainda)
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
            # Salva os dados gerados na sessão para usar na Etapa 2
            st.session_state.html_content = html_content
            st.session_state.feishu_elements_base = feishu_elements_base
            st.session_state.filename = f"Automotive_Dossier_{d_ini_str.replace('/','')}.html"
            st.session_state.step1_complete = True
        else:
            st.warning("No news selected. Please check the boxes of the news you want to include. / 未选择任何新闻。请勾选您要包含在档案中的新闻。")
            st.session_state.step1_complete = False

    # --- MODIFICAÇÃO: ETAPA 2 - Input manual do link e envio para o Lark ---
    if st.session_state.get('step1_complete', False):
        st.success("👉 Relatório HTML gerado com sucesso! Salve-o como PDF, faça o upload na sua nuvem e cole o link público abaixo para gerar o Lark Card:")
        
        # O botão de download fica disponível na tela
        st.download_button("📥 Download Final Dossier (HTML)", st.session_state.html_content, file_name=st.session_state.filename, mime="text/html")
        
        st.markdown("### 📤 4. ETAPA 2: Envio para o Lark / Feishu")
        
        # Captura o input do usuário
        user_report_url = st.text_input("🔗 Cole o link público do PDF hospedado na nuvem:")
        
        if st.button("🚀 Confirmar Link e Enviar Lark Card"):
            if user_report_url.strip() == "":
                st.error("Por favor, cole um link válido antes de enviar.")
            else:
                # --- MODIFICAÇÃO: Design do cabeçalho Lark Card ---
                # O link de acesso ganha total destaque no TOPO da mensagem.
                top_link_element = {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md", 
                        "content": f"**⭐✨ [CLICK HERE TO ACCESS THE FULL NEWS SUMMARY]({user_report_url.strip()}) ✨⭐**\n**⭐✨ [点击此处访问完整新闻摘要]({user_report_url.strip()}) ✨⭐**"
                    }
                }
                
                # O elemento de topo é adicionado ANTES da lista base gerada na Etapa 1
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
