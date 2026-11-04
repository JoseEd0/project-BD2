# Archivo de valores de longitud variable

## Qué es

Un archivo que guarda **valores de bytes de cualquier longitud** —de cero bytes a varios
megabytes— y devuelve por cada uno una dirección, un `RecordId(página, ranura)`. Es la base
para sacar de la fila los valores largos, como el texto de un documento: la fila guarda la
dirección (6 bytes) y el valor vive aquí. Es lo que hacen las columnas `TEXT`: cada tabla que
tiene alguna guarda sus textos en `<tabla>.text` (ver
[Columnas TEXT fuera de la fila](../README.md#7-columnas-text-fuera-de-la-fila-out_of_rowpy)).

```python
from config import EngineConfig
from storage.varlen import VariableLengthFile

config = EngineConfig()
with VariableLengthFile(config.data_directory / "documentos.var", config) as values:
    address = values.put("un documento de varios KB…".encode())
    texto = values.get(address).decode()
    address = values.replace(address, b"otro texto")  # puede cambiar de dirección
    values.delete(address)
```

| Método | Qué hace |
|---|---|
| `put(bytes) -> RecordId` | guarda el valor y devuelve la dirección de su primer fragmento |
| `get(RecordId) -> bytes` | el valor completo |
| `replace(RecordId, bytes) -> RecordId` | cambia el valor; la dirección se conserva si el primer fragmento nuevo cabe en su página |
| `delete(RecordId)` | libera todos sus fragmentos |
| `scan()` | `(dirección, valor)` de cada valor vivo, en orden físico |
| `describe()` | páginas de datos y de mapa, número de valores y bytes que suman |
| `flush()`, `close()` | vuelcan la cabecera y las páginas; también es gestor de contexto |

Una dirección que no es la de un valor vivo —la cabecera, una página de mapa, una página o
ranura que no existe, un valor borrado o el fragmento intermedio de otro valor— da
`ValueNotFoundError`. Abrir un archivo de otro tipo, de otra versión del formato o con otro
tamaño de página da `VariableLengthFormatError`.

## Formato del archivo

```
┌──────────┬────────┬────────┬─────┬────────┬────────┬────────┬─────┐
│ cabecera │ mapa 0 │ datos  │ ... │ datos  │ mapa 1 │ datos  │ ... │
└──────────┴────────┴────────┴─────┴────────┴────────┴────────┴─────┘
  página 0   página 1  ← page_size páginas →  página 2 + page_size
```

- **Página 0**: la `FileHeader` común (firma `VLEN`, versión) con el tamaño de página con que
  se creó, el número de valores y los bytes que suman. Como en el heap, se escribe al volcar
  o cerrar, no en cada operación.
- **Grupos de mapa y datos**: una página del mapa de espacio libre seguida de las `page_size`
  páginas de datos que describe, un byte por página. Las posiciones son fijas, así que de un
  número de página se sabe sin leer nada si es de mapa o de datos y qué byte la describe.
  Por eso el tamaño de página va en la cabecera: con otro, las páginas de mapa se leerían
  como de datos.

## Formato de una página de datos

```
┌──────────┬──────────┬──────────┬─────┬─────────────┬─────────────┬─────────────┐
│ cabecera │ ranura 0 │ ranura 1 │ ... │    libre    │ fragmento 1 │ fragmento 0 │
└──────────┴──────────┴──────────┴─────┴─────────────┴─────────────┴─────────────┘
  6 bytes    5 bytes por ranura →            ← los datos crecen desde el final
```

| Campo | Bytes | Significado |
|---|---|---|
| cabecera: ranuras | 2 | tamaño del directorio |
| cabecera: inicio de datos | 2 | desplazamiento del fragmento más bajo |
| cabecera: huecos | 2 | bytes de la zona de datos que no son de ningún fragmento vivo |
| ranura: desplazamiento | 2 | dónde empieza el fragmento |
| ranura: longitud | 2 | cuánto mide |
| ranura: tipo | 1 | `FREE`, `WHOLE`, `HEAD` o `PART` |

Los fragmentos `HEAD` y `PART` empiezan con el enlace al siguiente: un `RecordId` de 6 bytes.
El último `PART` de una cadena enlaza a `(0:0)`, la cabecera, que nunca guarda fragmentos.

**Invariantes de la página:**

1. El directorio termina antes de que empiece la zona de datos.
2. Ningún par de fragmentos vivos se solapa.
3. `inicio de datos + huecos + Σ longitudes vivas = page_size`: los huecos están contados
   exactamente.
4. La última ranura del directorio nunca está libre: al liberar, las ranuras libres del final
   se devuelven al espacio libre.
5. Un fragmento vivo no cambia de ranura nunca, aunque cambie de sitio dentro de la página:
   la dirección de un valor sigue valiendo mientras viva.

**Compactación.** Borrar o encoger un fragmento deja un hueco que solo se cuenta. Cuando una
inserción o un `replace` necesitan más espacio contiguo del que hay, pero el total libre
alcanza, la página junta sus fragmentos vivos contra el final, del más alto al más bajo
(cada uno se mueve hacia arriba, sobre bytes que ya no son de nadie). Solo cambian los
desplazamientos de las ranuras. Se compacta cuando hace falta y no en cada borrado: un
borrado sin inserción posterior no mueve bytes.

## Valores que no caben en una página

Con `M = page_size − 6 − 5` bytes por fragmento:

- un valor de hasta `M` bytes es un solo fragmento `WHOLE`, sin enlace;
- uno mayor se parte en fragmentos de `C = M − 6` bytes de valor, que llenan cada uno una
  página entera (`PART`), y un primer fragmento (`HEAD`) con lo que sobra, `L mod C`.

```
 HEAD (resto, en una página compartida)   PART (página llena)   PART (página llena)
 ┌──────┬──────────┐                      ┌──────┬──────────┐   ┌──────┬──────────┐
 │ enl. │ L mod C  │ ───────────────────► │ enl. │    C     │──►│ (0:0)│    C     │
 └──────┴──────────┘                      └──────┴──────────┘   └──────┴──────────┘
   la dirección del valor
```

El resto va delante y no detrás porque el primer fragmento es el único que comparte página
con otros valores: siendo el más pequeño, es el que más fácil cabe en la página de antes en
un `replace`, y entonces la dirección no cambia. Los fragmentos se escriben del último al
segundo para que cada uno conozca la dirección del siguiente, y el primero al final.

Cada fragmento de una cadena está en una página distinta (los `PART` llenan la suya), así que
una cadena no puede tener más eslabones que páginas el archivo: si los tiene, un enlace
corrupto apunta hacia atrás. El recorrido lo hace `chained_links` de `layout.py`, el mismo
que usan las cadenas de páginas del resto del motor, y lanza `CorruptFileError` en vez de
girar para siempre.

## Mapa de espacio libre

Cada byte del mapa es la **categoría** de una página de datos: qué fracción de lo que cabe
en una página vacía (`M`) cabe todavía en ella, en 255 escalones. La categoría de la página
se redondea hacia abajo y la que pide un fragmento hacia arriba, así que una página que la
alcanza tiene sitio seguro, contando con compactarla. La escala es `M` y no `page_size` para
que una página vacía, de categoría 255, admita un fragmento de página completa con cualquier
tamaño de página. En memoria solo vive la categoría máxima de cada página de mapa, una por
cada `page_size` páginas de datos (16 MiB con páginas de 4 KiB).

`put` elige la **primera** página con sitio: salta los grupos cuya categoría máxima no
alcanza, lee una sola página de mapa y busca en ella con `bytes.translate` + `find`, que
corren en C. Si ninguna tiene sitio, añade una página al final. Cada escritura de una página
de datos anota su nueva categoría en el mapa.

**Por qué así y no de otra forma:**

- **No la `FreePageList` de `layout.py`.** Esa lista guarda páginas *enteramente* libres, y
  aquí casi toda página tiene algo de sitio y algo ocupado. Una lista por tamaño de hueco
  exigiría enlaces dobles, y mover una página de lista escribiría también sus vecinas.
- **No un mapa construido en memoria al abrir.** Habría que leer todas las páginas de datos
  al abrir, justo lo que no se puede hacer.
- **Páginas de mapa en posiciones fijas** (como el *free space map* de PostgreSQL, que lo
  guarda en un archivo aparte). Dentro del mismo archivo, un número de página basta para
  saber dónde está su byte: no hay punteros en disco que puedan romperse ni un segundo
  archivo que el catálogo tenga que borrar.

Las páginas que se vacían no se devuelven a ninguna lista: quedan con categoría 255 y el
mapa las entrega antes de hacer crecer el archivo.

## Complejidad

Con F los fragmentos del valor (1 si cabe en una página):

| Operación | Páginas de datos | Páginas de mapa |
|---|---|---|
| `get` | lee F | ninguna |
| `put` | escribe F | lee como mucho 1 por fragmento; anota cada página que cambia de categoría |
| `delete` | lee y escribe F | anota cada página que cambia de categoría |
| `replace` | lee y escribe las F del valor viejo y las del nuevo | igual que `put` |
| `scan` | todas, una vez, más los `PART` de cada cadena | ninguna |
| abrir | ninguna | lee todas (1 por cada `page_size` de datos) |

Ninguna operación salvo `scan` recorre el archivo, y ninguna lo carga en memoria: en memoria
hay a la vez una página y el valor que se entrega.

## Decisiones y límites

- **Las direcciones se reutilizan.** Como en el heap, la ranura de un valor borrado puede
  recibir otro valor; quien guarda una dirección la deja de usar al borrarla.
- **`replace` puede cambiar la dirección** cuando el primer fragmento nuevo no cabe en la
  página del anterior. Devuelve la que vale.
- **Orden de las páginas de una cadena.** Como se escribe del último fragmento al segundo,
  en un archivo que crece las páginas de una cadena quedan en orden decreciente. Leerla
  sigue costando exactamente F páginas.
- **Sin tope de tamaño.** El valor entero pasa por memoria al guardarlo y al leerlo, porque
  la API recibe y devuelve `bytes`; ningún otro límite lo acota.

## Tests

```bash
.venv/bin/python -m pytest tests/storage/varlen -q
```

- **Página** (`test_slotted_page.py`): página vacía, un fragmento, fragmento de 0 bytes,
  fragmento que llena justo la página, desbordamiento, reutilización de ranuras, directorio
  que se encoge, compactación de huecos, `rewrite` que encoge y que crece, y errores.
- **Mapa** (`test_free_space_map.py`): mapa vacío, primera página con sitio, posiciones de las
  páginas de mapa al cruzar un grupo, reapertura, y que una categoría nunca promete más sitio
  del que hay ni le niega a una página vacía un fragmento de página completa, con varios
  tamaños de página.
- **Archivo** (`test_varlen_file.py`): archivo vacío, un valor, valor de 0 bytes, valor que
  llena justo una página, valor de 10 páginas, borrado y reutilización del hueco,
  compactación, `replace` que crece y encoge (de un fragmento a una cadena y de vuelta),
  reapertura, más de un grupo de mapa, direcciones inválidas, cadena corrupta, formato
  ajeno; que `get` lee una página por fragmento y `put` no recorre el archivo; y una prueba
  contra un `dict` con operaciones al azar, semillas fijas, tres tamaños de página y valores
  en los bordes de página.
