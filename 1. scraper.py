"""
Argenprop Scraper - Playwright (browser visible)
─────────────────────────────────────────────────
- Abre un Chrome real que podés ver en pantalla
- Si aparece un captcha lo resolvés ahí mismo, el scraper espera y sigue
- Checkpoint automático: si se interrumpe, retoma desde donde quedó
- Las páginas de detalle se scrapeán en paralelo con aiohttp (más rápido)
- Guardado incremental cada SAVE_EVERY propiedades

Instalación:
    pip install playwright aiohttp beautifulsoup4 pandas
    playwright install chromium
"""

import asyncio
import aiohttp
import json
import os
import re
import time
import logging
import pandas as pd
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page, BrowserContext

# ─── CONFIGURACIÓN ────────────────────────────────────────────────────────────

SAVE_EVERY              = 50
MAX_CONCURRENT_DETAILS  = 20
DETAIL_DELAY            = 0.5
PAGE_DELAY              = 1.0
TIMEOUT_MS              = 20000
OUTPUT_DIR              = "output"
CHECKPOINT_FILE         = os.path.join(OUTPUT_DIR, "checkpoint.json")

BASE_URL   = "https://www.argenprop.com"
SEARCH_URL = f"{BASE_URL}/departamentos/venta/capital-federal"

# ─── LOGGING ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("argenprop")

# ─── CHECKPOINT ───────────────────────────────────────────────────────────────

def save_checkpoint(page: int, output_file: str, seen_links: set):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump({"page": page, "output_file": output_file, "seen_links": list(seen_links)}, f)
    log.info(f"📌 Checkpoint guardado: página {page}")


def load_checkpoint() -> dict | None:
    if not os.path.exists(CHECKPOINT_FILE):
        return None
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        log.info(f"📌 Retomando desde página {data['page']} "
                 f"({len(data['seen_links'])} propiedades ya guardadas)")
        return data
    except Exception:
        return None


def clear_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    if not text:
        return "N/A"
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_address(address_raw: str):
    calle = altura = piso = "N/A"
    try:
        # Normalizar "al NNNN" → "NNNN"
        address_raw = re.sub(r"\bal\s+(\d)", r"\1", address_raw, flags=re.IGNORECASE)

        # Separar piso si viene después de coma: "Calle 1234, 5" o "Calle 1234, PB"
        piso_match = re.search(r",\s*(.+)$", address_raw)
        if piso_match:
            piso = piso_match.group(1).strip()
            address_raw = address_raw[:piso_match.start()].strip()

        # Extraer altura: último número al final de la dirección
        altura_match = re.search(r"^(.*?)\s+(\d+)\s*$", address_raw)
        if altura_match:
            calle  = altura_match.group(1).strip()
            altura = altura_match.group(2).strip()
        else:
            calle = address_raw

        # Normalizar piso
        if piso != "N/A":
            piso = re.sub(r"[°º]", "", piso).strip()
            if piso.upper() in ("PB", "pb"):
                piso = "PB"

    except Exception:
        pass
    return calle, altura, piso

# ─── PARSEO ───────────────────────────────────────────────────────────────────

KV_FIELDS = {
    "cant. ambientes":      "Ambientes",
    "cant. dormitorios":    "Dormitorios",
    "cant. baños":          "Baños",
    "cant. toilettes":      "Toilettes",
    "estado":               "Estado",
    "antiguedad":           "Antiguedad",
    "disposición":          "Disposicion",
    "tipo de balcón":       "Tipo_Balcon",
    "tipo de unidad":       "Tipo_Unidad",
    "sup. cubierta":        "Sup_Cubierta_m2",
    "sup. total":           "Sup_Total_m2",
    "precio":               "Precio_Ficha",
    "expensas":             "Expensas_Ficha",
    "cant. pisos":          "Cant_Pisos_Edificio",
    "deptos. por piso":     "Deptos_Por_Piso",
    "estado edificio":      "Estado_Edificio",
}

BINARY_FEATURES = [
    "Aire acondicionado individual", "Losa radiante",
    "Gas natural", "Agua corriente", "Balcón", "Terraza", "Jardín", "Patio", "Baulera", "Cochera",
    "Muebles de cocina", "Lavarropas", "Lavavajillas", "Permite Mascotas",
    "Ascensor", "Pileta", "Piscina", "Parrilla", "SUM", "Gimnasio",
    "Sauna", "Laundry", "Seguridad 24hs", "Vigilancia"
]
BINARY_KEYS = {f.lower(): f.replace(" ", "_") for f in BINARY_FEATURES}


def parse_listing_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for item in soup.find_all("div", class_="listing__item"):
        try:
            link_tag = item.find("a", class_="card")
            if not link_tag:
                continue
            link = BASE_URL + link_tag["href"]

            price_block = item.find("p", class_="card__price")
            price_text  = clean_text(price_block.text) if price_block else ""
            p_match     = re.search(r"(USD|S)\s?([\d.]+)", price_text)
            precio      = p_match.group(0) if p_match else "Consultar"
            e_match     = re.search(r"\+\s?\$?\s?([\d.]+)", price_text)
            expensas    = e_match.group(0) if e_match else "N/A"

            addr_tag    = item.find("p", class_="card__address")
            raw_address = clean_text(addr_tag.text) if addr_tag else ""
            calle, altura, piso = parse_address(raw_address)

            feat_tag = item.find("ul", class_="card__main-features")
            features = clean_text(feat_tag.text) if feat_tag else "N/A"

            items.append({
                "Precio": precio, "Expensas": expensas,
                "Calle": calle, "Altura": altura, "Piso": piso,
                "Detalles": features, "Descripción": "", "Link": link,
            })
        except Exception:
            continue
    return items


def parse_detail_html(html: str) -> dict:
    result: dict = {"Descripción": "Sin descripción"}
    for col in KV_FIELDS.values():
        result[col] = "N/A"
    for col in BINARY_KEYS.values():
        result[col] = 0

    soup = BeautifulSoup(html, "html.parser")

    desc_sec = soup.find("section", class_="section-description")
    if desc_sec:
        content = desc_sec.find(class_="section-description--content")
        raw = content.get_text(" ") if content else desc_sec.get_text(" ")
        result["Descripción"] = clean_text(raw).replace("Leer más Leer menos", "").strip()

    for ul in soup.find_all("ul", class_="property-features"):
        for li in ul.find_all("li"):
            li_class = li.get("class", [])
            if "property-features-item" in li_class:
                amenity = clean_text(li.get_text()).lower()
                col = BINARY_KEYS.get(amenity)
                if col:
                    result[col] = 1
                continue
            p = li.find("p")
            if not p:
                continue
            strong = p.find("strong")
            if strong:
                strong_text = clean_text(strong.get_text())
                label_text  = clean_text(p.get_text().replace(strong.get_text(), "")).rstrip(":").strip()
                if label_text:
                    key   = label_text.lower()
                    value = re.sub(r"\s*m2\s*", "", strong_text).strip()
                    col   = KV_FIELDS.get(key)
                    if col:
                        result[col] = value
                else:
                    key = strong_text.lower()
                    col = KV_FIELDS.get(key)
                    if col:
                        result[col] = "Sí"
                    else:
                        bin_col = BINARY_KEYS.get(key)
                        if bin_col:
                            result[bin_col] = 1
    return result

# ─── GUARDADO ─────────────────────────────────────────────────────────────────

def save_incremental(data: list[dict], filepath: str):
    df = pd.DataFrame(data)
    df.to_csv(filepath, sep="\t", index=False, encoding="utf-8-sig")
    log.info(f"💾 {len(df)} propiedades guardadas → {filepath}")

# ─── DETALLE VIA AIOHTTP (paralelo) ───────────────────────────────────────────

AIOHTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "es-AR,es;q=0.9",
}

async def fetch_detail(session: aiohttp.ClientSession, url: str, sem: asyncio.Semaphore) -> str | None:
    async with sem:
        await asyncio.sleep(DETAIL_DELAY)
        for attempt in range(1, 4):
            try:
                async with session.get(url, headers=AIOHTTP_HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        return await resp.text()
                    await asyncio.sleep(2 ** attempt)
            except Exception:
                await asyncio.sleep(2 ** attempt)
        return None


async def enrich_items(items: list[dict]) -> list[dict]:
    sem = asyncio.Semaphore(MAX_CONCURRENT_DETAILS)
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        async def enrich(item: dict) -> dict:
            html = await fetch_detail(session, item["Link"], sem)
            if html:
                item.update(parse_detail_html(html))
            return item
        return list(await asyncio.gather(*[enrich(i) for i in items]))

# ─── SCRAPER PRINCIPAL ────────────────────────────────────────────────────────

async def scrape(max_pages: int | None = None):
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    checkpoint  = load_checkpoint()
    start_page  = checkpoint["page"] if checkpoint else 1
    output_file = checkpoint["output_file"] if checkpoint else os.path.join(OUTPUT_DIR, f"argenprop_{int(time.time())}.tsv")
    seen_links: set[str] = set(checkpoint["seen_links"]) if checkpoint else set()

    all_data: list[dict] = []
    if checkpoint and os.path.exists(output_file):
        try:
            df_prev  = pd.read_csv(output_file, sep="\t", encoding="utf-8-sig")
            all_data = df_prev.to_dict("records")
            log.info(f"📂 {len(all_data)} propiedades previas cargadas")
        except Exception:
            all_data = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, slow_mo=50)
        context: BrowserContext = await browser.new_context(
            locale="es-AR",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        pw_page: Page = await context.new_page()

        page = start_page
        while True:
            if max_pages and page > max_pages:
                log.info(f"Límite de páginas alcanzado ({max_pages}).")
                break

            url = SEARCH_URL if page == 1 else f"{SEARCH_URL}?pagina-{page}"
            log.info(f"📄 Página {page} → {url}")

            try:
                await pw_page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
            except Exception as e:
                log.warning(f"Error navegando a página {page}: {e}. Reintentando...")
                await asyncio.sleep(5)
                continue

            try:
                await pw_page.wait_for_selector(
                    "div.listing__item, iframe[src*='recaptcha'], div[class*='captcha']",
                    timeout=TIMEOUT_MS,
                )
            except Exception:
                log.warning(f"Timeout en página {page}, reintentando...")
                await asyncio.sleep(3)
                continue

            has_results = await pw_page.query_selector("div.listing__item")
            has_captcha = await pw_page.query_selector(
                "iframe[src*='recaptcha'], div[class*='captcha'], form[action*='captcha']"
            )

            if not has_results and has_captcha:
                log.warning(f"🚨 CAPTCHA detectado en página {page} — resolvelo en el browser, el scraper espera...")
                try:
                    await pw_page.wait_for_selector("div.listing__item", timeout=300_000)
                    log.info("✅ Captcha resuelto, continuando...")
                except Exception:
                    log.error("⏰ Timeout esperando resolución del captcha. Guardando y saliendo.")
                    break
                has_results = await pw_page.query_selector("div.listing__item")

            if not has_results:
                log.info(f"Sin resultados en página {page}. Fin del scraping.")
                break

            html   = await pw_page.content()
            items  = parse_listing_html(html)

            if not items:
                log.info(f"Sin propiedades en página {page}. Fin.")
                break

            new_items = [i for i in items if i["Link"] not in seen_links]
            for i in new_items:
                seen_links.add(i["Link"])

            log.info(f"   → {len(new_items)} propiedades nuevas. Descargando detalles...")

            enriched = await enrich_items(new_items)
            all_data.extend(enriched)

            log.info(f"   ✅ Total acumulado: {len(all_data)} propiedades.")

            save_incremental(all_data, output_file)
            save_checkpoint(page + 1, output_file, seen_links)

            page += 1
            await asyncio.sleep(PAGE_DELAY)

        await browser.close()

    if all_data:
        save_incremental(all_data, output_file)
        clear_checkpoint()
        log.info(f"\n🎉 Scraping completo. {len(all_data)} propiedades en: {output_file}")
    else:
        log.warning("No se obtuvieron datos.")

# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Argenprop Scraper con Playwright")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="Límite de páginas (omitir para scrapear todo)")
    args = parser.parse_args()
    asyncio.run(scrape(max_pages=args.max_pages))