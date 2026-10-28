# Proyecto 1 · Analítica académica ITACA (origen XML)

> Proyecto del módulo **Big Data aplicado** (IABD). Dashboard de análisis académico en Power BI a partir de datos de ITACA exportados como XML. Arquitectura batch.

## Descripción y objetivo

Ingestar las exportaciones XML de ITACA, almacenarlas y transformarlas en un modelo de datos que alimente un dashboard académico.

## Arquitectura

```mermaid
graph LR
    A[Exportación XML ITACA] --> B[Ingesta]
    B --> C[MongoDB]
    C --> D[ETL]
    D --> E[Dashboard Power BI]
```

*Diagrama provisional (Bloque 0). Se actualizará al terminar cada bloque.*

## Fuentes de datos

| Fuente | URL / origen | Formato | Frecuencia de actualización | Licencia | Última ingesta |
|---|---|---|---|---|---|
| ITACA (exportación) | `data/exportaciones/<curso>/<tabla>.xml` (cursos 2021-22 a 2025-26) | XML | Una exportación por curso académico; se renueva al cerrar cada curso (la de 2025-26 se refresca en cada evaluación) | Uso docente (datos de ejemplo anonimizados por el docente) | 2026-10-28 |

### Tablas de origen

Acceso: exportación XML de ITACA, un fichero por tabla y curso; cada registro es un elemento con los datos como atributos.

| Tabla | Elemento XML | Campos disponibles que se ingestan | Campos descartados |
|---|---|---|---|
| Alumnos | `alumno` | NIA, curso, turno, grupo | Nombre, apellidos, fecha de nacimiento, documento, contacto, dirección, SIP, expediente… (datos personales) |
| Cursos | `curso` | código, nombre_cas, nombre_val, padre | abreviatura, enseñanza |
| Calificaciones | `calificacion` | alumno (NIA), curso, contenido, nota_numerica, evaluacion | tipo_nota, bloque_contenido, enseñanza |
| Contenidos (módulos) | `contenido` | código, nombre_cas, curso | nombre_val, enseñanza |
| Grupos | `grupo` | código, nombre, turno | línea, modalidad, aula, capacidad |

A todas se les añade `Anyo` (atributo `curso` del elemento raíz).

## Estructura del repositorio

| Carpeta | Contenido |
|---|---|
| `ingesta/` | Scripts de lectura de los XML |
| `etl/` | Transformaciones y modelo de datos |
| `dashboard/` | Informe de Power BI |
| `docs/` | Capturas y diagramas |
| `docker-compose.yml` | Servicio MongoDB local para desarrollo |

## Cómo desplegar el entorno

```bash
docker compose up -d
docker compose ps
```

MongoDB queda expuesto en `localhost:27017`.

## Bitácora de prácticas

### Bloque 0 · Entorno

- Objetivo: dejar preparado el repositorio y un MongoDB local con Docker.
- Pasos realizados: creación del repo con la estructura de la práctica, `.gitignore` del módulo, `docker-compose.yml` con MongoDB 7, primer commit y publicación en GitHub.
- Problemas encontrados y solución: —
- Resultado: `docker compose ps` con el contenedor `bigdata-mongo` en ejecución.

  ![docker compose ps con bigdata-mongo en ejecución](docs/captura-docker-compose-ps.png)

### Bloque 1 · Ingesta batch

- Objetivo: primera ingesta de los XML de ITACA (cinco cursos académicos) a la zona `raw` como JSON, con el NIA anonimizado.
- Pasos realizados:
  1. Análisis de la fuente (sección «Fuentes de datos»).
  2. Entorno: Python con sólo biblioteca estándar (`xml.etree`, `hashlib`, `json`); los XML se colocan en `data/exportaciones/<curso>/`.
  3. `ingesta/ingesta_itaca_xml.py`: recorre cada curso y tabla, extrae sólo los campos elegidos, anonimiza y guarda `data/raw/<tabla>_<fecha>.json`. Usa `logging` y errores controlados (fichero que falta o XML mal formado → mensaje y código de salida 1).
  4. Anonimización del NIA: hash **HMAC-SHA256** con una sal secreta guardada en `data/.sal_nia` (fuera de Git), truncado a 10 hex. Se calcula una sola vez por NIA (caché) y se reutiliza en Alumnos y Calificaciones.
  5. Validación de integridad al final de la ejecución.
  6. Comprobado que en `data/raw/` no hay nombre, apellidos, documento ni email, y que el NIA original no aparece.
- Problemas encontrados y solución:
  - El enunciado habla de cuatro y de cinco tablas (y menciona `horas`, que no existe en la exportación). Se ingestan las cinco que hay: `alumnos`, `cursos`, `calificaciones`, `contenidos` y `grupos`.
  - El esqueleto del enunciado lee elementos hijos (`findtext`), pero el XML real usa atributos: se leen con `nodo.get()`. Los campos de salida siguen los nombres del enunciado.
  - El esqueleto usa campos distintos para `cursos` y `contenidos`; se usan los de la descripción (`Codigo`, `Nombre_Cas`, `Nombre_Val`, `Padre`).
  - Un hash simple de un NIA de 8 cifras se deshace probando todos los valores; por eso se añade la sal.
  - 54 alumnos no tienen grupo en el origen: se guardan con `Grupo = null` y no cuentan como huérfanos.
  - 188 calificaciones de 2023 (9 NIA) no tienen su alumno en `alumnos.xml` de ese curso: es un hueco del origen. No se descartan; queda anotado para la limpieza del Bloque 2.
- Resultado: 5 tablas en `data/raw/` (7 193 alumnos, 413 cursos, 106 814 calificaciones, 1 688 contenidos, 257 grupos). Todo `Modulo` de Calificaciones existe en Contenidos y todo `Grupo` de Alumnos existe en Grupos. Salida de la ejecución en [`docs/salida-ingesta.txt`](docs/salida-ingesta.txt):

```text
INFO Guardados 7193 registros en data/raw/alumnos_2026-10-28.json
INFO Guardados 413 registros en data/raw/cursos_2026-10-28.json
INFO Guardados 106814 registros en data/raw/calificaciones_2026-10-28.json
INFO Guardados 1688 registros en data/raw/contenidos_2026-10-28.json
INFO Guardados 257 registros en data/raw/grupos_2026-10-28.json
INFO Alumnos sin grupo asignado en el origen: 54 (no se consideran huérfanos)
INFO Calificaciones.Modulo -> Contenidos: correcto
INFO Alumnos.Grupo -> Grupos: correcto
WARNING Calificaciones.NIA -> Alumnos: 188 huérfanos (ej. {'Anyo': '2023', 'NIA': '5e7d55e135', 'Curso': '3003826541', 'Modulo': '0173', 'Nota': '0', 'Evaluacion': '01'})
```

## Decisiones técnicas

| Decisión | Alternativa descartada | Motivo |
|---|---|---|
| MongoDB 7 en Docker para el origen XML | Cargar los XML en PostgreSQL | Los documentos XML se almacenan de forma natural como documentos BSON |

## Incidencias resueltas

*Ninguna por ahora.*
