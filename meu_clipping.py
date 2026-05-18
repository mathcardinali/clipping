import streamlit as st
import feedparser
import requests
import urllib.parse
import re 
import json
import html
import time
import os
import hashlib
import csv
from datetime import datetime, timedelta, time as dt_time
from time import mktime
from concurrent.futures import ThreadPoolExecutor, as_completed
from deep_translator import GoogleTranslator
import trafilatura 
import google.generativeai as genai
from googlenewsdecoder import gnewsdecoder
from fpdf import FPDF

# --- 1. CORE CONFIGURATION ---
st.set_page_config(page_title="🚗 Automotive Pulse Digest", layout="wide")
st.title("🚗 Automotive Pulse Digest")

WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/8f561d21-2a4c-4726-bff3-c0bf5d9c35a5"
SENT_HISTORY_FILE = "sent_history.json"
SNAPSHOT_FILE = "clipping_snapshots.csv"

# Tunable concurrency
MAX_WORKERS_FETCH = 8       # parallel RSS feeds
MAX_WORKERS_TRANSLATE = 6   # parallel translations
MAX_WORKERS_EXTRACT = 4     # parallel article extractions (gentler — these hit real sites)

# Global HTTP session (reused connections)
HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
})

# --- BRANDS & RESEARCH TOPICS ---
brands_by_origin = {
    "China": ["OMODA&JAECOO", "Lepas", "BYD", "GWM", "Zeekr", "GAC", "Geely", "Leapmotor", "Chery", "Dongfeng", "Jetour"],
    "Germany": ["Volkswagen", "BMW", "Mercedes-Benz", "Audi", "Porsche"],
    "USA": ["Chevrolet", "Ford", "Tesla", "Ram", "Jeep"],
    "Japan": ["Toyota", "Honda", "Nissan", "Mitsubishi", "Subaru"]
}

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
    },
    "Competitive Response": {
        "emoji": "⚔️",
        "query": '("guerra de preços" OR "redução de preço" OR "campanha de financiamento" OR "garantia estendida" OR "taxa zero" OR desconto OR promoção) (Fiat OR Volkswagen OR Hyundai OR Renault OR Chevrolet OR Toyota OR Stellantis OR Honda OR Nissan)'
    },
    "Dealer Network & After-Sales": {
        "emoji": "🛠️",
        "query": '(concessionária OR "rede de concessionárias" OR "pós-venda" OR recall OR "peças de reposição" OR "Reclame Aqui" OR "atendimento ao cliente" OR "satisfação do cliente" OR "assistência técnica") (automotiva OR montadora OR veículo OR carro)'
    },
    "Sales & Registrations": {
        "emoji": "📈",
        "query": '(Fenabrave OR Anfavea OR emplacamentos OR "vendas de veículos" OR "market share" OR "ranking de vendas" OR "balanço mensal" OR "mais vendidos")'
    },
    "Geopolitics & China-Brazil Tariffs": {
        "emoji": "🌐",
        "query": '("imposto de importação" OR tarifa OR "tarifa de importação" OR Mercosul OR BNDES OR "política industrial" OR "Rota 2030" OR "Mover") (China OR chinesa OR montadora) Brasil'
    },
    "Segment Launches": {
        "emoji": "🚙",
        "query": '(lançamento OR "novo modelo" OR estreia OR "chega ao Brasil" OR "pré-venda") ("SUV compacto" OR "SUV médio" OR "SUV premium" OR "sedan médio" OR hatch OR "picape média" OR "picape compacta")'
    },
    "Media Sentiment & Reviews": {
        "emoji": "⭐",
        "query": '(teste OR "test drive" OR review OR avaliação OR "comparativo" OR "Carro do Ano" OR prêmio OR "primeiras impressões" OR "primeiro contato") (automotivo OR carro OR veículo OR SUV OR sedan)'
    }
}

PRIORITY_BRANDS = ["OMODA&JAECOO", "Omoda", "Jaecoo"]

HIGH_VALUE_KEYWORDS = [
    "lançamento", "lança", "estreia", "chega ao brasil", "pré-venda",
    "recall", "crise", "processo",
    "fábrica", "investimento", "investe",
    "preço", "redução", "desconto", "promoção",
    "vendas", "emplacamentos", "market share", "ranking",
    "ICMS", "imposto", "tarifa", "tributação"
]

SOURCE_TIER = {
    "g1.globo.com": 5, "estadao.com.br": 5, "folha.uol.com.br": 5,
    "uol.com.br": 4, "autoesporte.globo.com": 4, "quatrorodas.abril.com.br": 4,
    "motor1.uol.com.br": 3
}

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

# --- DEDUP & PERSISTENCE HELPERS ---

def normalize_url(url):
    try:
        parsed = urllib.parse.urlparse(url)
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return clean.rstrip('/').lower()
    except:
        return url.lower()

def article_fingerprint(title, url):
    base = (normalize_url(url) + "|" + (title or "")[:60].lower()).encode("utf-8")
    return hashlib.md5(base).hexdigest()

def load_sent_history():
    try:
        if os.path.exists(SENT_HISTORY_FILE):
            with open(SENT_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return {}

def save_sent_history(history):
    try:
        cutoff = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        pruned = {fp: d for fp, d in history.items() if d >= cutoff}
        with open(SENT_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(pruned, f)
        return True
    except:
        return False

def append_snapshot(brand_counts, topic_counts):
    try:
        is_new = not os.path.exists(SNAPSHOT_FILE)
        with open(SNAPSHOT_FILE, "a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            if is_new:
                writer.writerow(["timestamp", "type", "name", "count"])
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            for name, count in brand_counts.items():
                writer.writerow([ts, "brand", name, count])
            for name, count in topic_counts.items():
                writer.writerow([ts, "topic", name, count])
        return True
    except:
        return False

def compute_relevance_score(item):
    score = 0
    title_lower = item.get("title", "").lower()
    for kw in HIGH_VALUE_KEYWORDS:
        if kw in title_lower:
            score += 2
    try:
        domain = urllib.parse.urlparse(item.get("link", "")).netloc.replace("www.", "")
        score += SOURCE_TIER.get(domain, 1)
    except:
        score += 1
    return score

# --- TRANSLATION (cached + lazy) ---

@st.cache_data(show_spinner=False, ttl=86400)  # 24h cache
def cached_translate(text, target_lang):
    """Single translation, cached. Streamlit hashes (text, target_lang) automatically."""
    if not text or len(text.strip()) == 0:
        return text
    try:
        urls = re.findall(r'(https?://[^\s]+)', text)
        temp_text = text
        for i, url in enumerate(urls):
            temp_text = temp_text.replace(url, f"[[URL_{i}]]")
        translated = GoogleTranslator(source='auto', target=target_lang).translate(temp_text)
        for i, url in enumerate(urls):
            translated = translated.replace(f"[[URL_{i}]]", url)
        return translated
    except:
        return text

def translate_pair_parallel(text):
    """Translates a single text to (en, zh) in parallel. Returns 'en / zh'."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_en = pool.submit(cached_translate, text, 'en')
        f_zh = pool.submit(cached_translate, text, 'zh-CN')
        return f"{f_en.result()} / {f_zh.result()}"

def translate_titles_batch(items, progress_callback=None):
    """Translates a list of items' titles in parallel. Mutates 'title' in place."""
    def _translate_one(item):
        item['title_translated'] = translate_pair_parallel(item['title_pt'])
        return item
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_TRANSLATE) as pool:
        futures = {pool.submit(_translate_one, it): it for it in items}
        done = 0
        for future in as_completed(futures):
            done += 1
            if progress_callback:
                progress_callback(done, len(items))
    return items

# --- ARTICLE EXTRACTION (reuses global session) ---

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

        resposta = HTTP_SESSION.get(url, timeout=15, allow_redirects=True)
        dominio_real = urllib.parse.urlparse(url).netloc.replace("www.", "")
        
        if resposta.status_code == 200:
            texto = trafilatura.extract(resposta.text)
            return texto if texto and len(texto) > 150 else f"- Insufficient text extracted from {dominio_real} -"
        return f"- Site blocked by security wall {dominio_real} (Error {resposta.status_code}) -"
    except Exception as e:
        return f"- Connection error: {e} -"

# --- GEMINI (model name cached once per session) ---

def _get_gemini_model_name():
    """Cached in session_state so list_models() is called once per browser session."""
    if 'gemini_model_name' in st.session_state:
        return st.session_state.gemini_model_name
    model_name = 'models/gemini-1.5-flash'
    try:
        available = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        for m in available:
            if 'flash' in m.lower():
                model_name = m
                break
        if not model_name and available:
            model_name = available[0]
    except:
        pass
    st.session_state.gemini_model_name = model_name
    return model_name

def resumir_noticia_com_gemini(texto, api_key):
    if not api_key: return "- Error: Missing API Key -"
    if "- " in texto[:2]: return f"- Extraction Failure: {texto} -"
    
    try:
        genai.configure(api_key=api_key)
        model_name = _get_gemini_model_name()

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

def gerar_executive_summary(dossier_data, session_state, api_key):
    if not api_key:
        return "- Executive summary unavailable (missing API key) -"
    
    bundle = []
    for section, items in dossier_data.items():
        for idx, it in enumerate(items):
            if session_state.get(f"keep_{section}_{idx}"):
                summary_snippet = (it.get("summary") or "")[:300]
                bundle.append(f"[{section}] {it['title']}\n{summary_snippet}")
    
    if not bundle:
        return "- No selected articles to summarize -"
    
    aggregated = "\n\n".join(bundle)[:8000]
    
    try:
        genai.configure(api_key=api_key)
        model_name = _get_gemini_model_name()
        
        instruction = """
        You are a senior automotive market analyst. Read the bundle of news headlines and short summaries below and write an EXECUTIVE SUMMARY for a Chinese executive audience.
        
        Rules:
        - 5 to 7 lines, in BOTH English and Chinese (English first, then Chinese, no mixing within sentences).
        - Highlight the MOST strategically important moves: launches, factory news, regulatory shifts, competitor reactions, sales numbers.
        - Plain text only. No bold, no asterisks, no markdown.
        - Lead with the single most important headline of the week.
        - Be concrete (cite brand names and numbers when available).
        """
        
        model = genai.GenerativeModel(model_name=model_name, system_instruction=instruction)
        response = model.generate_content(aggregated)
        return response.text.strip()
    except Exception as e:
        return f"- Could not generate executive summary: {e} -"

# --- PARALLEL FEED FETCH ---

def fetch_single_feed(section_key, query_str, d_ini, d_end, title_match_fn, seen_fingerprints_lock, seen_fingerprints, sent_history, hide_already_sent):
    """Fetches and parses one RSS feed. Returns list of dicts (untranslated)."""
    full_q = f"{query_str} after:{d_ini.strftime('%Y-%m-%d')} before:{d_end.strftime('%Y-%m-%d')}"
    feed_url = f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(full_q)}&hl=pt-BR&gl=BR"
    feed = feedparser.parse(feed_url)
    
    collected = []
    for entry in feed.entries:
        if not title_match_fn(entry.title):
            continue
        if not hasattr(entry, 'published_parsed'):
            continue
        pub_date = datetime.fromtimestamp(mktime(entry.published_parsed)).date()
        if not (d_ini <= pub_date <= d_end):
            continue
        
        fp = article_fingerprint(entry.title, entry.link)
        
        # Thread-safe check against global dedup set
        with seen_fingerprints_lock:
            if fp in seen_fingerprints:
                continue
            seen_fingerprints.add(fp)
        
        already_sent = fp in sent_history
        if hide_already_sent and already_sent:
            continue
        
        item = {
            "title_pt": entry.title,         # raw Portuguese (used in Tela 2)
            "title_translated": "",          # filled on-demand
            "link": entry.link,
            "fingerprint": fp,
            "already_sent": already_sent
        }
        # Score uses the PT title since keywords are PT
        item["score"] = compute_relevance_score({"title": entry.title, "link": entry.link})
        collected.append(item)
    
    return section_key, collected

# --- PDF GENERATION ---
def gerar_pdf_bytes(dossier_data, session_state, executive_summary=None):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    font_path = "fireflysung.ttf"
    if os.path.exists(font_path):
        pdf.add_font('Firefly', '', font_path)
        font_main = 'Firefly'
    else:
        font_main = 'Helvetica'

    largura_util = pdf.epw 

    pdf.set_font(font_main, 'B' if font_main == 'Helvetica' else '', 20)
    pdf.set_text_color(26, 35, 126)
    pdf.cell(largura_util, 15, "Automotive Pulse Digest", ln=True, align="C")
    
    pdf.set_font(font_main, '', 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(largura_util, 6, f"Researcher: Matheus Cardinali | Date: {datetime.now().strftime('%m/%d/%Y')}", ln=True, align="C")
    pdf.ln(8)

    if executive_summary and not executive_summary.startswith("- "):
        pdf.set_font(font_main, 'B' if font_main == 'Helvetica' else '', 13)
        pdf.set_fill_color(26, 35, 126)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(largura_util, 9, "  EXECUTIVE SUMMARY / 执行摘要", ln=True, fill=True)
        pdf.ln(3)
        pdf.set_font(font_main, '', 10)
        pdf.set_text_color(33, 33, 33)
        clean_exec = executive_summary.encode('latin-1', 'replace').decode('latin-1') if font_main == 'Helvetica' else executive_summary
        pdf.multi_cell(largura_util, 6, txt=clean_exec, align='L')
        pdf.ln(4)
        pdf.set_draw_color(180, 180, 180)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + largura_util, pdf.get_y())
        pdf.ln(6)

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
                pdf.set_font(font_main, 'B' if font_main == 'Helvetica' else '', 11)
                pdf.set_text_color(0, 86, 179)
                clean_title = it['title'].encode('latin-1', 'replace').decode('latin-1') if font_main == 'Helvetica' else it['title']
                pdf.write(7, txt=clean_title, link=it['link'])
                pdf.ln(8)
                pdf.set_x(pdf.l_margin)

                pdf.set_font(font_main, '', 10)
                pdf.set_text_color(33, 33, 33)
                clean_summary = it['summary'].encode('latin-1', 'replace').decode('latin-1') if font_main == 'Helvetica' else it['summary']
                pdf.multi_cell(largura_util, 6, txt=clean_summary, align='L')
                
                pdf.ln(4)
                pdf.set_draw_color(220, 220, 220)
                pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + largura_util, pdf.get_y())
                pdf.ln(4)
    
    return pdf.output()

# --- 2. SESSION STATE ---
if 'raw_fetched_news' not in st.session_state: st.session_state.raw_fetched_news = {}
if 'dossier_data' not in st.session_state: st.session_state.dossier_data = {}
if 'executive_summary' not in st.session_state: st.session_state.executive_summary = ""
if 'd_ini_str' not in st.session_state: st.session_state.d_ini_str = ""
if 'd_end_str' not in st.session_state: st.session_state.d_end_str = ""
if 'sent_history' not in st.session_state: st.session_state.sent_history = load_sent_history()

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
    yesterday = datetime.now() - timedelta(days=1)
    date_range = st.date_input("Period:", value=(yesterday, yesterday))
    
    st.divider()
    with st.expander("🧰 Advanced Options"):
        sort_by_score = st.checkbox("Sort by relevance score", value=True, help="High-value keywords + source authority move articles to the top")
        hide_already_sent = st.checkbox("Hide articles already sent", value=False, help="Filter out URLs you've already pushed to PDF/Lark")
        translate_in_fetch = st.checkbox("Translate ALL titles in fetch (slow)", value=False, help="Default = OFF. Titles are translated only when you select them for AI processing.")
        st.caption(f"📂 Memory: {len(st.session_state.sent_history)} articles tracked")

    if st.button("🚀 1. Fetch News Links"):
        if gemini_api_key and len(date_range) == 2:
            st.session_state.dossier_data = {}
            st.session_state.executive_summary = ""
            
            d_ini, d_end = date_range
            st.session_state.d_ini_str = d_ini.strftime('%m/%d')
            st.session_state.d_end_str = d_end.strftime('%m/%d')

            launch_keywords = " (lançamento OR segredo OR flagra OR novidade OR \"modelo 2027\" OR \"modelo 2026\")"
            media_filter = " (site:g1.globo.com OR site:uol.com.br OR site:estadao.com.br OR site:folha.uol.com.br OR site:quatrorodas.abril.com.br OR site:autoesporte.globo.com OR site:motor1.uol.com.br)"
            
            # Build the list of fetch tasks BEFORE submitting to executor
            fetch_tasks = []
            sorted_brands = sort_priority_brands(brand_selection)
            
            for brand in sorted_brands:
                if brand == "OMODA&JAECOO":
                    q_base = '("Omoda" OR "Jaecoo") Brasil'
                    title_match = (lambda t: ("omoda" in t.lower()) or ("jaecoo" in t.lower()))
                else:
                    q_base = f"\"{brand}\" Brasil"
                    title_match = (lambda t, b=brand: b.lower() in t.lower())
                
                q = q_base + (launch_keywords if target_launch else "") + media_filter
                fetch_tasks.append((brand, q, title_match, False))  # False = is_topic
            
            for topic in topic_selection:
                topic_q = research_topics[topic]["query"] + media_filter
                fetch_tasks.append((topic, topic_q, lambda t: True, True))
            
            # Run all RSS fetches in parallel
            import threading
            seen_fingerprints = set()
            seen_lock = threading.Lock()
            results_raw = {}
            
            progress = st.progress(0, text=f"Fetching {len(fetch_tasks)} feeds in parallel...")
            
            # Brands must be processed BEFORE topics for dedup priority — so we run
            # brand fetches first as a batch, then topic fetches as a batch.
            brand_tasks = [t for t in fetch_tasks if not t[3]]
            topic_tasks = [t for t in fetch_tasks if t[3]]
            
            completed = 0
            total = len(fetch_tasks)
            
            for batch in (brand_tasks, topic_tasks):
                if not batch:
                    continue
                with ThreadPoolExecutor(max_workers=MAX_WORKERS_FETCH) as pool:
                    futures = [
                        pool.submit(
                            fetch_single_feed,
                            section_key, query_str, d_ini, d_end, title_match_fn,
                            seen_lock, seen_fingerprints,
                            st.session_state.sent_history, hide_already_sent
                        )
                        for section_key, query_str, title_match_fn, _ in batch
                    ]
                    for future in as_completed(futures):
                        section_key, collected = future.result()
                        if sort_by_score:
                            collected.sort(key=lambda x: x["score"], reverse=True)
                        if collected:
                            results_raw[section_key] = collected[:10]
                        completed += 1
                        progress.progress(completed / total, text=f"Fetched {completed}/{total} feeds")
            
            # Optional: pre-translate titles right now if user opted in.
            # Default behavior is to defer translation until AI summarization step.
            if translate_in_fetch:
                progress.progress(1.0, text="Translating titles...")
                all_items = [it for items in results_raw.values() for it in items]
                if all_items:
                    bar = st.progress(0, text="Translating titles in parallel...")
                    def _cb(done, total):
                        bar.progress(done / total, text=f"Translated {done}/{total} titles")
                    translate_titles_batch(all_items, progress_callback=_cb)
                    bar.empty()
            
            progress.empty()
            st.session_state.raw_fetched_news = results_raw

# --- 4. SELECTION & AI PROCESSING ---
if st.session_state.raw_fetched_news and not st.session_state.dossier_data:
    st.header("📝 2. Select News for AI Processing")
    st.info("Check the news you want to analyze. Titles in PT are translated only when selected. ✓ = already sent · ⭐ = high score.")
    
    selected_to_process = {}
    
    for brand, items in st.session_state.raw_fetched_news.items():
        emoji = get_section_emoji(brand)
        st.subheader(f"{emoji} {brand.upper()}")
        selected_to_process[brand] = []
        for idx, item in enumerate(items):
            col_cb, col_text = st.columns([0.05, 0.95])
            with col_cb:
                if st.checkbox("", value=False, key=f"raw_select_{brand}_{idx}"):
                    selected_to_process[brand].append(item)
            with col_text:
                badges = []
                if item.get("already_sent"):
                    badges.append("✓ sent")
                if item.get("score", 0) >= 8:
                    badges.append(f"⭐ score {item['score']}")
                elif item.get("score", 0) >= 5:
                    badges.append(f"score {item['score']}")
                badge_str = f" `[{' · '.join(badges)}]`" if badges else ""
                # Show translated title if already cached, otherwise PT
                display_title = item.get("title_translated") or item.get("title_pt") or ""
                st.markdown(f"**[{display_title}]({item['link']})**{badge_str}")

    st.divider()
    if st.button("🧠 3. Summarize Selected with AI"):
        has_selections = sum(len(news) for news in selected_to_process.values())
        if has_selections == 0:
            st.warning("Please select at least one article to process.")
        else:
            # STEP A: Translate titles of selected items in parallel (only those missing translation)
            to_translate = []
            for items in selected_to_process.values():
                for it in items:
                    if not it.get("title_translated"):
                        to_translate.append(it)
            
            if to_translate:
                tbar = st.progress(0, text=f"Translating {len(to_translate)} selected titles...")
                def _cb(done, total):
                    tbar.progress(done / total, text=f"Translated {done}/{total}")
                translate_titles_batch(to_translate, progress_callback=_cb)
                tbar.empty()
            
            # STEP B: Extract + summarize sequentially (Gemini rate limits + politeness to sites)
            final_dossier = {}
            total_items = sum(len(v) for v in selected_to_process.values())
            sbar = st.progress(0, text="Extracting & summarizing...")
            done = 0
            
            # Extract texts in parallel first (I/O bound)
            extract_queue = []
            for brand, items in selected_to_process.items():
                if items:
                    final_dossier[brand] = []
                    for it in items:
                        extract_queue.append((brand, it))
            
            extracted_texts = {}  # id(item) -> raw text
            with ThreadPoolExecutor(max_workers=MAX_WORKERS_EXTRACT) as pool:
                futures = {pool.submit(extrair_texto_da_noticia, it['link']): (brand, it) for brand, it in extract_queue}
                for future in as_completed(futures):
                    brand, it = futures[future]
                    extracted_texts[id(it)] = future.result()
                    done += 1
                    sbar.progress(done / (total_items * 2), text=f"Extracted {done}/{total_items} articles")
            
            # Now summarize sequentially (Gemini free tier has tight rate limits)
            done = 0
            for brand, it in extract_queue:
                texto_raw = extracted_texts.get(id(it), "- Extraction failed -")
                resumo = resumir_noticia_com_gemini(texto_raw, gemini_api_key)
                time.sleep(1)  # reduced from 2s — extraction is no longer happening here
                final_dossier[brand].append({
                    "title": it.get('title_translated') or it.get('title_pt'),
                    "link": it['link'],
                    "fingerprint": it.get('fingerprint', ''),
                    "summary": resumo
                })
                done += 1
                sbar.progress(0.5 + done / (total_items * 2), text=f"Summarized {done}/{total_items}")
            
            sbar.empty()
            st.session_state.dossier_data = final_dossier
            st.rerun()

# --- 5. EDITING & PDF EXPORT ---
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
    
    st.markdown("### 🧭 Executive Summary")
    col_es1, col_es2 = st.columns([0.3, 0.7])
    with col_es1:
        if st.button("✨ Generate / Regenerate"):
            with st.spinner("Building executive overview..."):
                st.session_state.executive_summary = gerar_executive_summary(
                    st.session_state.dossier_data,
                    st.session_state,
                    gemini_api_key
                )
    with col_es2:
        st.caption("Optional: a 5-7 line bilingual briefing placed at the top of the PDF.")
    
    if st.session_state.executive_summary:
        st.session_state.executive_summary = st.text_area(
            "Executive Summary (editable)",
            value=st.session_state.executive_summary,
            height=180,
            key="exec_summary_edit"
        )
    
    st.divider()
    
    count = sum(1 for brand, items in st.session_state.dossier_data.items() for idx in range(len(items)) if st.session_state.get(f"keep_{brand}_{idx}"))
    
    if count == 0:
        st.warning("Please check at least one article to include in the PDF.")
    else:
        try:
            pdf_bytes = gerar_pdf_bytes(
                st.session_state.dossier_data,
                st.session_state,
                executive_summary=st.session_state.executive_summary
            )
            nome_arquivo = f"Automotive_Pulse_{datetime.now().strftime('%d%m%y')}.pdf"
            
            if st.download_button(
                label="📥 5. Download Final PDF Dossier", 
                data=bytes(pdf_bytes), 
                file_name=nome_arquivo, 
                mime="application/pdf"
            ):
                today_str = datetime.now().strftime("%Y-%m-%d")
                brand_counts = {}
                topic_counts = {}
                for section, items in st.session_state.dossier_data.items():
                    kept = [it for idx, it in enumerate(items) if st.session_state.get(f"keep_{section}_{idx}")]
                    target = topic_counts if is_topic(section) else brand_counts
                    target[section] = len(kept)
                    for it in kept:
                        if it.get('fingerprint'):
                            st.session_state.sent_history[it['fingerprint']] = today_str
                save_sent_history(st.session_state.sent_history)
                append_snapshot(brand_counts, topic_counts)
        except Exception as e:
            st.error(f"Failed to generate PDF: {e}")

    # --- LARK SYNC ---
    st.markdown("---")
    st.markdown("### 📤 6. Send to Lark / Feishu")
    link_nuvem = st.text_input("🔗 Paste the public cloud link here:")
    
    if st.button("🚀 Push to Lark"):
        if link_nuvem:
            lark_elements = [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"📍 **Focus Country / 重点国家:** Brazil / 巴西\n📅 **Period / 期间:** {st.session_state.d_ini_str} to {st.session_state.d_end_str}"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**⭐✨ [CLICK TO ACCESS FULL DOSSIER / 点击获取完整档案]({link_nuvem}) ✨⭐**"}},
                {"tag": "hr"}
            ]
            
            if st.session_state.executive_summary and not st.session_state.executive_summary.startswith("- "):
                exec_short = st.session_state.executive_summary[:900]
                lark_elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**🧭 EXECUTIVE SUMMARY / 执行摘要**\n{exec_short}"}})
                lark_elements.append({"tag": "hr"})
            
            for brand, items in st.session_state.dossier_data.items():
                kept = [it for idx, it in enumerate(items) if st.session_state.get(f"keep_{brand}_{idx}")]
                if kept:
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
            
            today_str = datetime.now().strftime("%Y-%m-%d")
            for section, items in st.session_state.dossier_data.items():
                for idx, it in enumerate(items):
                    if st.session_state.get(f"keep_{section}_{idx}") and it.get('fingerprint'):
                        st.session_state.sent_history[it['fingerprint']] = today_str
            save_sent_history(st.session_state.sent_history)
            
            st.success("Successfully sent to Lark!")
            st.balloons()
