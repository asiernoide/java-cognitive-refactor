import json
import os
import subprocess
import sys
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

JAVA_ANALYZER_JAR = "ast-analyzer/ast-analyzer.jar"

# Último error de parseo (para feedback dirigido en el reintento de lotes).
last_error: str | None = None


def _first_error_line(stderr: str, max_len: int = 400) -> str:
    """Extrae la primera línea significativa de error (Parse/Lexical error) del
    stderr de JavaParser para usarla como feedback conciso en los reintentos."""
    for line in stderr.splitlines():
        s = line.strip()
        if "Parse error" in s or "Lexical error" in s or s.startswith("Error al parsear"):
            return s[:max_len]
    return stderr.strip()[:max_len]

# Raíz del proyecto Java (rutas relativas: módulo único o multi-módulo con el
# submódulo como primer segmento).
JAVA_SRC_ROOT = os.getenv("JAVA_SRC_ROOT", ".")


def analyze_method(file_path: str, line: int) -> dict | None:
    """Analiza el método en `line` y devuelve sus métricas AST, o None si falla."""
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

    global last_error
    last_error = None
    if result.returncode != 0:
        last_error = _first_error_line(result.stderr)
        print(f"  [WARN] Fallo al analizar {file_path}:{line}", file=sys.stderr)
        print(f"  {last_error}", file=sys.stderr)
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON inválido para {file_path}:{line} — {e}", file=sys.stderr)
        return None


def analyze_method_by_signature(file_path: str, signature: str) -> dict | None:
    """Analiza el método por signatura (estable ante refactors, cuando las líneas cambian)."""
    if not os.path.isfile(file_path):
        print(f"  [WARN] Archivo no encontrado en disco: {file_path}", file=sys.stderr)
        return None

    try:
        result = subprocess.run(
            ["java", "-jar", JAVA_ANALYZER_JAR, file_path, signature],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("ERROR: No se encontró 'java' en el PATH o el JAR no existe.", file=sys.stderr)
        return None

    global last_error
    last_error = None
    if result.returncode != 0:
        last_error = _first_error_line(result.stderr)
        print(f"  [WARN] Fallo al analizar {file_path} por signatura {signature}", file=sys.stderr)
        print(f"  {last_error}", file=sys.stderr)
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON inválido para {file_path} — {e}", file=sys.stderr)
        return None


def scan_project_complex_methods(project_root: str, threshold: int = 15) -> pd.DataFrame:
    """Detección LOCAL de métodos complejos (scan JavaParser, CC local, CC > umbral)."""
    result = subprocess.run(
        ["java", "-jar", JAVA_ANALYZER_JAR, "scan", project_root, str(threshold)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[ERROR] Fallo al escanear el proyecto {project_root}", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        return pd.DataFrame()

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON inválido en el escaneo de {project_root}: {e}", file=sys.stderr)
        return pd.DataFrame()

    return pd.DataFrame(data)


def enrich_with_ast(df: pd.DataFrame, java_src_root: str | None = None) -> pd.DataFrame:
    """Enriquece un DataFrame de métodos con métricas AST (por línea); helper,
    el pipeline principal usa `scan_project_complex_methods`."""
    src_root = java_src_root or JAVA_SRC_ROOT
    if src_root == ".":
        print("[WARN] JAVA_SRC_ROOT no está definido — usando el directorio actual.")

    enriched_rows = []

    for _, row in df.iterrows():
        relative_path = row["file"]
        line = row["start_line"]

        # Construir ruta absoluta: JAVA_SRC_ROOT + ruta relativa del archivo.
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
    # - message:    mensaje del issue de detección, útil para depuración pero sin valor en el dataset
    columns_to_drop = [c for c in ["start_line", "message"] if c in result.columns]
    return result.drop(columns=columns_to_drop)
