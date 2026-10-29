"""Ingesta de las exportaciones XML de ITACA a la zona raw (JSON).

Lee data/exportaciones/<curso>/<tabla>.xml, anonimiza el NIA y guarda
data/raw/<tabla>_<fecha>.json. No se vuelca ningún dato personal salvo el
NIA anonimizado: los campos de cada tabla se eligen explícitamente.
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import xml.etree.ElementTree as ET
from datetime import date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ingesta_itaca_xml")

DIR_EXPORT = "data/exportaciones"
DIR_RAW = "data/raw"
FICHERO_SAL = "data/.sal_nia"

TABLAS = ["alumnos", "cursos", "calificaciones", "contenidos", "grupos"]

# tabla -> (etiqueta de cada registro, campos de salida). Los campos y sus nombres son los del
# enunciado (P1): coinciden con los atributos del XML. Nombre, apellidos, documento, contacto,
# SIP y fechas de nacimiento no se leen nunca.
ESQUEMA = {
    "alumnos": ("alumno", ["NIA", "sexo", "nacionalidad", "provincia", "municipio", "ensenanza", "curso",
                           "grupo", "turno", "linea", "modalidad", "repite", "estado_matricula",
                           "tipo_matricula", "fecha_matricula", "fecha_ingreso_centro"]),
    "cursos": ("curso", ["codigo", "nombre_cas", "nombre_val", "abreviatura", "ensenanza", "padre"]),
    "calificaciones": ("calificacion", ["evaluacion", "alumno", "ensenanza", "curso", "contenido",
                                        "bloque_contenido", "nota_numerica", "tipo_nota"]),
    "contenidos": ("contenido", ["codigo", "nombre_cas", "nombre_val", "ensenanza", "curso"]),
    "grupos": ("grupo", ["codigo", "nombre", "turno", "linea", "modalidad"]),
}
CAMPOS_NIA = {"NIA", "alumno"}  # campos que contienen el NIA y se anonimizan


def cargar_sal() -> bytes:
    """Sal secreta y estable: evita deshacer el hash probando todos los NIA."""
    if os.path.exists(FICHERO_SAL):
        with open(FICHERO_SAL, "rb") as f:
            return f.read()
    sal = secrets.token_hex(16).encode()
    os.makedirs(os.path.dirname(FICHERO_SAL), exist_ok=True)
    with open(FICHERO_SAL, "wb") as f:
        f.write(sal)
    logger.info("Sal nueva creada en %s (está fuera de Git)", FICHERO_SAL)
    return sal


class Anonimizador:
    """Un único hash por NIA original, reutilizado en todas las tablas."""

    def __init__(self, sal: bytes):
        self.sal = sal
        self.cache: dict[str, str] = {}

    def __call__(self, nia: str) -> str:
        if nia not in self.cache:
            self.cache[nia] = hmac.new(self.sal, nia.encode(), hashlib.sha256).hexdigest()[:10]
        return self.cache[nia]


def cursos_exportados() -> list[str]:
    if not os.path.isdir(DIR_EXPORT):
        raise FileNotFoundError(f"No existe {DIR_EXPORT}: coloca ahí los XML de origen")
    cursos = sorted(d for d in os.listdir(DIR_EXPORT) if os.path.isdir(os.path.join(DIR_EXPORT, d)))
    if not cursos:
        raise FileNotFoundError(f"{DIR_EXPORT} no contiene carpetas de curso")
    return cursos


def parsear_xml(tabla: str, curso: str, anonimizar) -> list[dict]:
    ruta = os.path.join(DIR_EXPORT, curso, f"{tabla}.xml")
    try:
        raiz = ET.parse(ruta).getroot()
    except (FileNotFoundError, ET.ParseError) as e:
        logger.error("No se puede leer %s: %s", ruta, e)
        raise
    etiqueta, campos = ESQUEMA[tabla]
    anyo = raiz.get("curso")
    registros = []
    for nodo in raiz.iter(etiqueta):
        fila = {"anyo": anyo}
        for campo in campos:
            valor = nodo.get(campo)
            valor = valor.strip() if valor is not None else None
            if campo in CAMPOS_NIA and valor:
                valor = anonimizar(valor)
            fila[campo] = valor or None
        registros.append(fila)
    logger.info("%s %s: %d registros", curso, tabla, len(registros))
    return registros


def guardar(datos: list[dict], nombre: str) -> None:
    ruta = os.path.join(DIR_RAW, f"{nombre}_{date.today().isoformat()}.json")
    os.makedirs(DIR_RAW, exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    logger.info("Guardados %d registros en %s", len(datos), ruta)


def validar(datos: dict[str, list[dict]]) -> bool:
    """Integridad referencial entre tablas (la clave incluye anyo y curso)."""
    modulos = {(r["anyo"], r["curso"], r["codigo"]) for r in datos["contenidos"]}
    grupos = {(r["anyo"], r["codigo"]) for r in datos["grupos"]}
    alumnos = {(r["anyo"], r["NIA"]) for r in datos["alumnos"]}
    comprobaciones = [
        ("calificaciones.contenido -> contenidos",
         [r for r in datos["calificaciones"] if (r["anyo"], r["curso"], r["contenido"]) not in modulos]),
        ("alumnos.grupo -> grupos",
         [r for r in datos["alumnos"] if r["grupo"] and (r["anyo"], r["grupo"]) not in grupos]),
        ("calificaciones.alumno -> alumnos",
         [r for r in datos["calificaciones"] if (r["anyo"], r["alumno"]) not in alumnos]),
    ]
    sin_grupo = sum(1 for r in datos["alumnos"] if not r["grupo"])
    if sin_grupo:
        logger.info("Alumnos sin grupo asignado en el origen: %d (no se consideran huérfanos)", sin_grupo)
    ok = True
    for nombre, huerfanos in comprobaciones:
        if huerfanos:
            ok = False
            # se informa pero no se descartan: el hueco está en el origen
            logger.warning("%s: %d huérfanos", nombre, len(huerfanos))
        else:
            logger.info("%s: correcto", nombre)
    return ok


def main() -> int:
    anonimizar = Anonimizador(cargar_sal())
    datos: dict[str, list[dict]] = {t: [] for t in TABLAS}
    try:
        for curso in cursos_exportados():
            for tabla in TABLAS:
                datos[tabla] += parsear_xml(tabla, curso, anonimizar)
    except (FileNotFoundError, ET.ParseError):
        return 1
    for tabla, filas in datos.items():
        guardar(filas, tabla)
    if not validar(datos):
        logger.warning("Ingesta completada; los huérfanos vienen del origen y se limpian en el Bloque 2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
