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

# tabla -> (etiqueta de cada registro, {campo de salida: atributo XML})
ESQUEMA = {
    "alumnos": ("alumno", {"NIA": "NIA", "Curso": "curso", "Turno": "turno", "Grupo": "grupo"}),
    "cursos": ("curso", {"Codigo": "codigo", "Nombre_Cas": "nombre_cas",
                         "Nombre_Val": "nombre_val", "Padre": "padre"}),
    "calificaciones": ("calificacion", {"NIA": "alumno", "Curso": "curso", "Modulo": "contenido",
                                        "Nota": "nota_numerica", "Evaluacion": "evaluacion"}),
    "contenidos": ("contenido", {"Curso": "curso", "Modulo": "codigo", "Nombre_Modulo": "nombre_cas"}),
    "grupos": ("grupo", {"Codigo": "codigo", "Nombre": "nombre", "Turno": "turno"}),
}
CAMPOS_NIA = {"alumnos", "calificaciones"}


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
        fila = {"Anyo": anyo}
        for salida, atributo in campos.items():
            valor = nodo.get(atributo)
            valor = valor.strip() if valor is not None else None
            if salida == "NIA" and valor:
                valor = anonimizar(valor)
            fila[salida] = valor or None
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
    """Integridad referencial entre tablas (clave incluye Anyo y Curso)."""
    ok = True
    modulos = {(r["Anyo"], r["Curso"], r["Modulo"]) for r in datos["contenidos"]}
    grupos = {(r["Anyo"], r["Codigo"]) for r in datos["grupos"]}
    alumnos = {(r["Anyo"], r["NIA"]) for r in datos["alumnos"]}
    comprobaciones = [
        ("Calificaciones.Modulo -> Contenidos",
         [r for r in datos["calificaciones"] if (r["Anyo"], r["Curso"], r["Modulo"]) not in modulos]),
        ("Alumnos.Grupo -> Grupos",
         [r for r in datos["alumnos"] if r["Grupo"] and (r["Anyo"], r["Grupo"]) not in grupos]),
        ("Calificaciones.NIA -> Alumnos",
         [r for r in datos["calificaciones"] if (r["Anyo"], r["NIA"]) not in alumnos]),
    ]
    sin_grupo = sum(1 for r in datos["alumnos"] if not r["Grupo"])
    if sin_grupo:
        logger.info("Alumnos sin grupo asignado en el origen: %d (no se consideran huérfanos)", sin_grupo)
    for nombre, huerfanos in comprobaciones:
        if huerfanos:
            ok = False
            logger.warning("%s: %d huérfanos (ej. %s)", nombre, len(huerfanos), huerfanos[0])
            # se informa pero no se descartan: el hueco está en el origen
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
    validar(datos)
    return 0


if __name__ == "__main__":
    sys.exit(main())
