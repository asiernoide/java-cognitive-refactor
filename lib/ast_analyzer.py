import json
import os
import subprocess
import sys
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

JAVA_ANALYZER_JAR = "ast-analyzer/ast-analyzer.jar"

# Directorio raíz del proyecto Java en disco.
#
# SonarQube devuelve rutas relativas a este directorio, que ya incluyen
# el nombre del submódulo como primer segmento cuando el proyecto es multi-módulo:
#
#   Módulo único:   src/main/java/com/example/UserService.java
#   Multi-módulo:   jmetal-core/src/main/java/org/uma/jmetal/...
#
# En ambos casos basta con definir JAVA_SRC_ROOT apuntando a la raíz del
# proyecto; el resto de la ruta lo proporciona SonarQube automáticamente.
JAVA_SRC_ROOT = os.getenv("JAVA_SRC_ROOT", ".")


def analyze_method(file_path: str, line: int) -> dict | None:
    """
    Ejecuta el analizador Java para un método concreto y devuelve
    sus métricas AST como diccionario, o None si falla.

    file_path debe ser la ruta absoluta al archivo Java.
    """
    if not os.path.isfile(file_path):
        print(f"  [WARN] Archivo no encontrado en disco: {file_path}", file=sys.stderr)
        return None

    try:
        result = subprocess.run(
            ["java", "-jar", JAVA_ANALYZER_JAR, file_path, str(line)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("ERROR: No se encontró 'java' en el PATH o el JAR no existe.", file=sys.stderr)
        return None

    if result.returncode != 0:
        print(f"  [WARN] Fallo al analizar {file_path}:{line}", file=sys.stderr)
        print(f"  {result.stderr.strip()}", file=sys.stderr)
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON inválido para {file_path}:{line} — {e}", file=sys.stderr)
        return None


def enrich_with_ast(df: pd.DataFrame, java_src_root: str | None = None) -> pd.DataFrame:
    """
    Recibe un DataFrame de métodos complejos (de sonarqube_api)
    y lo enriquece con métricas AST ejecutando el analizador Java
    para cada método. Devuelve un nuevo DataFrame con todas las columnas.
    Los métodos que no puedan analizarse se omiten.

    Si no se especifica java_src_root, se usa el directorio actual.
    """
    src_root = java_src_root or JAVA_SRC_ROOT
    if src_root == ".":
        print("[WARN] JAVA_SRC_ROOT no está definido — usando el directorio actual.")

    enriched_rows = []

    for _, row in df.iterrows():
        relative_path = row["file"]
        line = row["start_line"]

        # Construir ruta absoluta: JAVA_SRC_ROOT + ruta relativa de SonarQube.
        # Para módulo único:  <root>/src/main/java/...
        # Para multi-módulo:  <root>/jmetal-core/src/main/java/...
        full_path = os.path.join(src_root, relative_path)

        print(f"  Analizando {relative_path}:{line} ...", end=" ")

        analysis = analyze_method(full_path, line)

        if analysis is None:
            print("OMITIDO")
            continue

        enriched_rows.append({**row.to_dict(), **analysis})
        print("OK")

    result = pd.DataFrame(enriched_rows)

    # Eliminar columnas redundantes o de depuración antes de devolver el DataFrame:
    # - start_line: redundante con method_start_line (calculado por JavaParser sobre el AST)
    # - message:    mensaje de SonarQube útil para depuración pero sin valor en el dataset
    columns_to_drop = [c for c in ["start_line", "message"] if c in result.columns]
    return result.drop(columns=columns_to_drop)
