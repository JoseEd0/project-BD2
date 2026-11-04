# Analizador de texto

El primer paso de la Parte 3: convertir un texto en la lista de **términos** que guarda el
índice invertido y que busca una consulta. Hay **un solo analizador para documentos y
consultas**: si el índice cortara «Teléfonos» en `telefon` y la consulta en `teléfonos`, la
consulta no encontraría nada. Por eso todo pasa por el mismo método, `Analyzer.terms`.

No depende de otro paquete del motor salvo `config` (longitudes de token) y `errors`.

## Uso

```python
from config import EngineConfig
from text import Analyzer

analyzer = Analyzer(EngineConfig())          # español por omisión
analyzer.terms("Cable USB-C de 3,5 mm para iPhone12")
# ['cabl', 'usb', 'c', '3.5', 'mm', 'iphon', '12']

analyzed = analyzer.analyze("La casa, las casas y la casita")
analyzed.frequencies   # {'cas': 2, 'casit': 1}
analyzed.length        # 3

Analyzer(EngineConfig(), "en").terms("The databases are running")   # ['databas', 'run']
```

| API | Qué da |
|---|---|
| `Analyzer(config, language="es")` | el analizador de un idioma; `UnknownLanguageError` si no existe |
| `terms(text) -> list[str]` | términos en orden de aparición, con repeticiones |
| `analyze(text) -> AnalyzedText` | `frequencies` (término → veces) y `length` (términos del texto) |
| `fold(text) -> str` | el plegado del paso 1, que también usan las listas de stopwords |
| `language_named(code) -> Language` | el registro de idiomas: código, stemmer y stopwords |

`analyze` es lo que necesita SPIMI por documento: la frecuencia de cada término para su
posting y la longitud del documento para la normalización del coseno y para BM25, todo en
una pasada. Una consulta usa el mismo `analyze` o `terms`.

## Las cinco etapas

```
texto ──► plegado ──► tokens ──► longitud ──► stopwords ──► raíz (Snowball) ──► términos
```

### 1. Plegado (`normalization.py`)

1. **NFKC**: la misma letra escrita de dos formas pasa a ser una sola. Une la `n` con la
   tilde combinante en `ñ`, convierte las letras y cifras de ancho completo (`ＡＢＣ１２`), las
   ligaduras (`ﬁ`) y los superíndices (`m²` → `m2`).
2. **`casefold()`**, que es más agresivo que `lower()`: `Straße` → `strasse`.
3. **Sin tildes ni diéresis, salvo la ñ**: `canción` → `cancion`, `pingüino` →
   `pinguino`, pero `año` sigue siendo `año`, distinto de `ano`.
4. **Sin apóstrofos**: `L'Oréal` → `loreal`, `Kellogg’s` → `kelloggs`. Se borran en vez de
   separar porque así se buscan las marcas.

Los pasos 3 y 4 son un único `str.translate` con una tabla que se construye una vez al
importar el módulo, recorriendo el plano básico de Unicode: cada letra con diacrítico va a
su letra base y cada marca diacrítica suelta se borra. Ningún texto paga una llamada a
`unicodedata` por carácter.

Quitar las tildes hace que la búsqueda no dependa de que el usuario las escriba, que en las
fichas de producto y en las consultas faltan a menudo. Tiene un coste conocido: las
palabras que solo se distinguen por la tilde se juntan, y si una de las dos es stopword se
pierden las dos. `sería` y `te` están en la lista, así que `seria` (de «serio») y `té`
tampoco se indexan.

### 2. Tokens

Un token es una **palabra** (letras seguidas) o una **cifra** (dígitos seguidos, con
separadores decimales o de miles entre dígitos). Todo lo demás separa: espacios,
puntuación, guiones, barras, el guion bajo.

| Texto | Términos | Regla |
|---|---|---|
| `3,5 mm`, `3.5mm` | `3.5`, `mm` | la coma decimal se escribe como punto |
| `1.000,50` y `1,000.50` | `1.000.50` | los dos formatos dan el mismo término |
| `128GB`, `128 GB` | `128`, `gb` | entre letra y cifra siempre hay corte |
| `iPhone12`, `iPhone 12` | `iphon`, `12` | ídem |
| `4K` | `4`, `k` | ídem |
| `USB-C` | `usb`, `c` | el guion separa |
| `Wi-Fi` | `wi`, `fi` | ídem: `wifi` sin guion es otro término |
| `modelo 2020.` | `model`, `2020` | el punto final no es decimal |

**Por qué se corta entre letras y cifras.** En las fichas de producto la misma cosa aparece
con y sin espacio: `128GB` y `128 GB`, `3,5mm` y `3.5 mm`, `iPhone12` e `iPhone 12`.
Cortando siempre, las dos formas dan los mismos términos y una consulta encuentra las dos.
El precio es que un código como `4K` o `PS5` se indexa como dos términos (`4` y `k`), y la
consulta `4K` busca los dos: la coincidencia sigue siendo exacta, pero cada mitad por
separado es menos específica.

**Por qué el guion separa.** `USB-C`, `Full-HD` y `anti-arañazos` se escriben también con
espacio. La forma unida (`wifi`) no se genera además de las partes, porque contaría dos
veces las mismas letras en la longitud del documento que usa BM25.

**`1.000` no es `1000`.** El punto puede ser separador de miles (español) o decimal
(inglés), y el analizador no adivina cuál: se conserva tal cual.

### 3. Longitud

Se descartan los tokens de menos de `text_min_token_length` caracteres o de más de
`text_max_token_length` (`EngineConfig`). Por omisión la mínima es **1**: las tallas
(`talla M`) y variantes de una letra (`USB-C`, `vitamina C`) importan en un catálogo, y los
artículos de una letra (`a`, `y`, `o`) ya los quitan las stopwords. La máxima es **40**:
ninguna palabra de diccionario llega, y lo que pasa de ahí son cadenas pegadas.

### 4. Stopwords (`stopwords/es.txt`, `stopwords/en.txt`)

Son las listas de **Snowball** (Martin Porter, licencia BSD) en su formato original: la
palabra al inicio de la línea y lo que sigue a `|` es comentario. Al cargarse, cada entrada
pasa por el **mismo plegado** que los tokens, así que `más`, `MÁS` y `mas` se quitan por
igual. Se quitan **antes** de reducir a la raíz: `nosotros` está en la lista, pero su raíz
`nosotr` no.

Un único cambio respecto al original: en la lista inglesa están desactivadas las
contracciones (`she'll`, `we'll`, `don't`…). Al borrar el apóstrofo se convertirían en
palabras de contenido (`shell`, `well`) y esas palabras desaparecerían del índice.

### 5. Raíz (Snowball)

Cada palabra se reduce con el stemmer Snowball del paquete
[`snowballstemmer`](https://pypi.org/project/snowballstemmer/) (Python puro). Es
preprocesamiento, que el enunciado no evalúa, así que entra por la regla de librerías del
proyecto. Recibe la palabra **ya sin tildes**, y por eso `baterías`, `baterias`, `batería`
y `bateria` dan todas `bateri`; con la tilde puesta el stemmer las reparte entre `bat` y
`bateri`. Las cifras no pasan por el stemmer ni por las stopwords.

Las raíces se **memoizan** en una caché LRU por analizador de `STEM_CACHE_ENTRIES` = 65 536
palabras. El vocabulario sigue la ley de Zipf: unas decenas de miles de palabras cubren casi
todas las apariciones, mientras que la cola de modelos, marcas y erratas crece con el
corpus. Acotada, la caché se queda con las frecuentes y la memoria no crece con la cola.

El stemmer de Snowball guarda estado mientras procesa una palabra, así que dos consultas
concurrentes sobre el mismo índice lo romperían. Se protege con un candado que solo se toma
cuando la palabra **no** está en la caché; un test con ocho hilos lo comprueba, y sin el
candado falla.

## Idiomas (`languages.py`)

El idioma se elige por índice (`WITH (idioma='es')`, español por omisión). `languages.py`
es el único registro: cada idioma es su código, su lista de stopwords y el nombre de su
algoritmo Snowball. Un código desconocido lanza `UnknownLanguageError`, un `DomainError`
que el API muestra como error del usuario y que lista los idiomas disponibles.

| Código | Stopwords | Stemmer |
|---|---|---|
| `es` | 301 (Snowball, plegadas) | `spanish` |
| `en` | 124 (Snowball, sin contracciones) | `english` |

## Velocidad medida

```bash
.venv/bin/python -m benchmarks.text_analyzer_benchmark --repetitions 5 \
    --input Proyecto_Integrador_BD2.md docs/latex/informe/*.tex
```

Texto real en español: el enunciado y las fuentes del informe, 871 documentos (una línea
no vacía cada uno) con 8 779 tokens. Se mide `analyze`, lo mismo que hará SPIMI. Resultado
crudo en [`benchmarks/results/analizador_texto.json`](../../benchmarks/results/analizador_texto.json);
Apple M4 Pro, Python 3.12.

| Pasada | Tiempo | Tokens por segundo |
|---|---:|---:|
| fría (caché de raíces vacía) | 36,1 ms | 243 227 |
| caliente (mediana de 5) | 7,9 ms | **1 116 951** |

La pasada caliente es la que importa al indexar un corpus grande, donde casi todas las
palabras ya se vieron: el stemmer solo trabaja la primera vez que aparece cada una.

## Tests

```bash
.venv/bin/python -m pytest tests/text -q
```

Cubren tildes, ñ, mayúsculas, puntuación, cifras y decimales, guiones, códigos de modelo,
stopwords con y sin tilde, raíces en español e inglés, texto vacío, texto de solo
stopwords, longitudes configurables, idioma desconocido, determinismo, que documento y
consulta se analizan igual, que las listas versionadas están plegadas y son un token por
entrada, y el acceso concurrente.
