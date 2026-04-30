import streamlit as st
import feedparser
import requests
import urllib.parse
from datetime import datetime, timedelta

# --- 1. CORE CONFIGURATION ---
st.set_page_config(page_title="🚗 Automotive Pulse Digest", layout="wide")
st.title("🚗 Automotive Pulse Digest")
st.markdown("🚗 Automotive Pulse Digest")

# Feishu Webhook
WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/8f561d21-2a4c-4726-bff3-c0bf5d9c35a5"

# --- 2. SESSION STATE ---
if 'dossier_data' not in st.session_state:
    st.session_state.dossier_data = {}

# --- 3. SIDEBAR ---
brands_by_origin = {
    "China": ["Omoda", "Jaecoo", "BYD", "GWM", "Zeekr", "GAC", "Geely", "Leapmotor", "Chery"],
    "Germany": ["Volkswagen", "BMW", "Mercedes-Benz", "Audi", "Porsche"],
    "USA": ["Chevrolet", "Ford", "Tesla", "Ram", "Jeep"],
    "Japan": ["Toyota", "Honda", "Nissan", "Mitsubishi", "Subaru"]
}

with st.sidebar:
    st.header("⚙️ Market Parameters")
    
    target_launch = st.checkbox("🎯 Focar em Lançamentos/Segredos", value=True)
    
    origins = st.multiselect("Origins:", list(brands_by_origin.keys()), default=["China"])
    available = []
    for o in origins: available.extend(brands_by_origin[o])
    brand_selection = st.multiselect("Brands:", available, default=["Omoda", "Jaecoo", "BYD"])
    today = datetime.now()
    date_range = st.date_input("Period:", value=(today - timedelta(days=7), today))

    if st.button("🚀 1. Fetch News Links"):
        if len(date_range) == 2:
            d_ini, d_end = date_range
            results = {}
            
            launch_keywords = " (lançamento OR segredo OR flagra OR novidade OR \"modelo 2027\" OR \"modelo 2026\")"
            
            with st.spinner("Fetching latest headlines and filtering noise..."):
                for brand in brand_selection:
                    base_q = f"\"{brand}\" Brasil"
                    if target_launch:
                        base_q += launch_keywords
                    
                    full_q = f"{base_q} after:{d_ini.strftime('%Y-%m-%d')} before:{d_end.strftime('%Y-%m-%d')}"
                    
                    safe_q = urllib.parse.quote_plus(full_q)
                    feed = feedparser.parse(f"https://news.google.com/rss/search?q={safe_q}&hl=pt-BR&gl=BR")
                    
                    if feed.entries:
                        brand_news = []
                        safe_brand_name = brand.lower()
                        
                        for entry in feed.entries:
                            # STRICT FILTER: The brand must actually be in the headline to avoid Google News "spiraling"
                            if safe_brand_name in entry.title.lower():
                                brand_news.append({
                                    "title": entry.title, 
                                    "link": entry.link, 
                                    "summary": "- Insert Comments Here -"
                                })
                            
                            # Keep only the top 3 highly relevant ones
                            if len(brand_news) == 3:
                                break
                                
                        if brand_news: # Only add the brand if we found actual relevant news
                            results[brand] = brand_news
                            
            st.session_state.dossier_data = results

# --- 4. EDITING AREA ---
if st.session_state.dossier_data:
    
    st.header("📋 2. Clipboard para IA Externa")
    st.info("Copie os links abaixo para processar no Gemini/ChatGPT com foco em Inovação e Produto.")
    
    links_text = "Analise os seguintes links com foco em LANÇAMENTOS, CX e estratégia de mercado no Brasil:\n\n"
    for brand, items in st.session_state.dossier_data.items():
        for item in items:
            links_text += f"[{brand}] {item['title']}\nLink: {item['link']}\n\n"
    
    st.code(links_text, language="text")
    
    st.header("📝 3. Curate Insights")
    st.info("Desmarque as notícias que não servem para o dossiê e cole suas análises nas caixas de texto.")
    
    for brand, items in st.session_state.dossier_data.items():
        st.subheader(f"🏎️ {brand.upper()}")
        
        for idx, item in enumerate(items):
            # Checkbox para permitir ao usuário vetar a notícia do dossiê final
            keep_checkbox = st.checkbox(f"✅ Incluir no Dossiê Final", value=True, key=f"keep_{brand}_{idx}")
            
            st.markdown(f"**Source:** [{item['title']}]({item['link']})")
            
            # Text area remains the same, but we save its output to session state dynamically
            st.session_state.dossier_data[brand][idx]['summary'] = st.text_area(
                label=f"Edit Notes: {brand} - {idx}",
                value=item['summary'],
                height=150, 
                key=f"edit_{brand}_{idx}",
                label_visibility="collapsed"
            )

    # --- 5. FINALIZATION & EXPORT ---
    st.divider()
    if st.button("📄 4. Finalize Dossier & Sync Feishu"):
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
                <h1 style="color: #1a237e; margin-bottom: 10px;">Automotive Market Intelligence Dossier</h1>
                <p style="margin: 5px 0; font-size: 16px;"><strong>📍 Focus Country:</strong> Brazil</p>
                <p style="margin: 5px 0; font-size: 16px;"><strong>📅 Period:</strong> {d_ini_str} to {d_end_str}</p>
                <p style="margin: 5px 0; font-size: 16px;"><strong>👤 Researcher:</strong> Matheus Cardinali</p>
            </div>
        """
        
        feishu_elements = [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"📍 **Focus Country:** Brazil\n📅 **Period:** {d_ini_str} to {d_end_str}"}
            },
            {"tag": "hr"}
        ]
        
        has_any_content = False
        
        for brand, items in st.session_state.dossier_data.items():
            # Filtrar apenas os itens que o usuário deixou marcados no Checkbox
            kept_items = [item for idx, item in enumerate(items) if st.session_state.get(f"keep_{brand}_{idx}", True)]
            
            if kept_items: # Só adiciona a marca se sobrar alguma notícia válida
                has_any_content = True
                html_content += f"<div class='brand-sec'><h2>{brand}</h2>"
                links_md = []
                
                for item in kept_items:
                    item_title = item.get('title', 'Link da Notícia')
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
                feishu_elements.append({
                    "tag": "div", 
                    "text": {"tag": "lark_md", "content": f"**{brand.upper()}**\n" + "\n".join(links_md)}
                })
                feishu_elements.append({"tag": "hr"})

        html_content += "</body></html>"
        
        if has_any_content:
            st.download_button("📥 Download Final Dossier (HTML)", html_content, file_name=f"Automotive_Dossier_{d_ini_str.replace('/','')}.html", mime="text/html")
            
            payload = {
                "msg_type": "interactive",
                "card": {
                    "header": {"title": {"tag": "plain_text", "content": "🚗 Automotive Pulse Digest"}, "template": "blue"},
                    "elements": feishu_elements
                }
            }
            res = requests.post(WEBHOOK_URL, json=payload)
            if res.status_code == 200:
                st.success("Dossier finalized and links synced to Feishu!")
        else:
            st.warning("Nenhuma notícia foi selecionada. Marque os checkboxes das notícias que deseja incluir no dossiê.")
