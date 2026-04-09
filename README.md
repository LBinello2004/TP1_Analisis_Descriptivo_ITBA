# Proyecto: Análisis de Departamentos en Venta en CABA (Argenprop)

Este repositorio contiene el pipeline completo para extraer, geolocalizar y enriquecer datos de departamentos en venta en la Ciudad Autónoma de Buenos Aires publicados en Argenprop, junto con un informe descriptivo de los resultados.

---

## Flujo del pipeline

```
scraper.py  →  argenprop_1775261213.tsv
                       ↓
          mapeo_latitud_longitud.ipynb  →  argenprop_con_lat_lon.tsv
                                                      ↓
                                              enrichment.ipynb  →  argenprop_enriched.tsv
                                                                             ↓
                                                                         Informe (PDF)
```

---

## Archivos

### `1. scraper.py`
Script principal de scraping. Usa **Playwright** para navegar Argenprop con un browser real (visible) y **aiohttp** para descargar las páginas de detalle en paralelo.

Características clave:
- Scraper las páginas de listado de departamentos en venta en CABA (`argenprop.com/departamentos/venta/capital-federal`)
- Manejo automático de **CAPTCHA**: cuando detecta uno, pausa y espera que el usuario lo resuelva manualmente en el browser
- **Checkpoint automático**: si se interrumpe, retoma desde la última página guardada
- Guarda incrementalmente cada 50 propiedades en la carpeta `output/`
- Extrae: precio, expensas, dirección (calle, altura, piso), descripción, características del departamento (ambientes, dormitorios, baños, estado, antigüedad, amenities, etc.)

**Salida:** `output/argenprop_1775261213.tsv`

---

### `2. mapeo_latitud_longitud.ipynb` (Si bien no es parte de este entregable, queriamos probar si era factible mapear la latitud y longitud a partir de la calle de la propiedad)
Notebook que **geocodifica** las direcciones del dataset crudo, convirtiendo la calle y altura de cada propiedad en coordenadas geográficas (latitud y longitud).

- Usa la API gratuita de **Nominatim (OpenStreetMap)**
- Valida que las coordenadas obtenidas caigan dentro de los límites geográficos de CABA
- Procesa las propiedades de forma asíncrona con un delay para respetar los límites de la API
- Lee `argenprop_1775261213.tsv` y genera `argenprop_con_lat_lon.tsv`

**Salida:** `argenprop_con_lat_lon.tsv`

---

### `3. enrichment.ipynb` (Con el mapeo de la latitud y longitud no se logran visualizar los datos, por ello decidimos hacer un testeo con distintas fuentes para la planificación de fuentes externas para entregas futuras)

Notebook que **enriquece geoespacialmente** el dataset geocodificado, cruzando cada propiedad con datos públicos de la ciudad.

Agrega las siguientes columnas por propiedad:
- `Barrio` y `Comuna`: mediante un join espacial con el polígono de barrios de CABA
- `Dist_Subte_m` y `Subte_cercano` y `Linea_subte`: distancia en metros a la boca de subte más cercana
- `Dist_Hospital_m` y `Hospital_cercano`: distancia al hospital público más cercano
- `Paradas_colectivo_300m`: cantidad de paradas de colectivo en un radio de 300m

Usa **GeoPandas** y proyección EPSG:22185 para calcular distancias con precisión métrica.

**Salida:** `argenprop_enriched.tsv`

---

### `argenprop_1775261213.tsv`
Dataset **crudo** generado por el scraper (~6.500 propiedades). Contiene todos los campos extraídos de Argenprop: precio, expensas, dirección, descripción, características del departamento y amenities. No tiene información geográfica.

---

### `argenprop_con_lat_lon.tsv`
Dataset intermedio con las **coordenadas geográficas** añadidas (~5.900 propiedades). Es igual al anterior pero incluye las columnas `Latitud`, `Longitud` y `Procesada`. Las filas sin coordenadas válidas (direcciones no geocodificables o fuera de CABA) fueron descartadas.

---

### `argenprop_enriched.tsv`
Dataset **final y completo** (~4.200 propiedades), listo para análisis. Combina toda la información del scraping con el enriquecimiento geoespacial: barrio, comuna, proximidad a transporte público y servicios de salud.

---

### `Informe.pdf` 
Documento final que resume el desarrollo del proyecto desde el enfoque de negocio hasta el análisis de los datos obtenidos. Incluye la definición del problema orientado a un inversor inmobiliario tipo flipper, cuyo objetivo es maximizar el retorno de inversión (ROI) mediante la identificación de propiedades subvaluadas en la Ciudad Autónoma de Buenos Aires.

El informe presenta las preguntas clave del análisis, estructuradas en niveles descriptivo, diagnóstico, predictivo y prescriptivo, junto con los KPIs asociados para evaluar el mercado inmobiliario. A su vez, detalla las hipótesis planteadas, como el impacto de la ubicación, el estado de la propiedad y la presencia de amenities en el precio y potencial de valorización.

También se describe el proceso completo de construcción del dataset, incluyendo la extracción de datos mediante scraping, la limpieza y estructuración de los DataFrames en Pandas y el enriquecimiento con fuentes externas geoespaciales.

---

## Dependencias

```
pip install playwright aiohttp beautifulsoup4 pandas geopandas shapely requests
playwright install chromium
```
