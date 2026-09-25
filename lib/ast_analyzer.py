import json
import os
import subprocess
import sys

import pandas as pd

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


def _run_analyzer(args: list[str]) -> subprocess.CompletedProcess | None:
    """Ejecuta el JAR del analizador con los argumentos dados (None si no hay java)."""
    try:
        return subprocess.run(
            ["java", "-jar", JAVA_ANALYZER_JAR, *args],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("ERROR: No se encontró 'java' en el PATH o el JAR no existe.", file=sys.stderr)
        return None


def _analyze_file(file_path: str, anchor: str, context: str) -> dict | None:
    """Analiza un método por ancla (línea o signatura) y devuelve sus métricas.

    `context` es la descripción que aparece en los mensajes de warning, p. ej.
    "ruta.java:10" o "ruta.java por signatura m(int)".
    """
    if not os.path.isfile(file_path):
        print(f"  [WARN] Archivo no encontrado en disco: {file_path}", file=sys.stderr)
        return None

    result = _run_analyzer([file_path, anchor])
    if result is None:
        return None

    global last_error
    last_error = None
    if result.returncode != 0:
        last_error = _first_error_line(result.stderr)
        print(f"  [WARN] Fallo al analizar {context}", file=sys.stderr)
        print(f"  {last_error}", file=sys.stderr)
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON inválido para {context} — {e}", file=sys.stderr)
        return None


def analyze_method(file_path: str, line: int) -> dict | None:
    """Analiza el método en `line` y devuelve sus métricas AST, o None si falla."""
    return _analyze_file(file_path, str(line), f"{file_path}:{line}")


def analyze_method_by_signature(file_path: str, signature: str) -> dict | None:
    """Analiza el método por signatura (estable ante refactors, cuando las líneas cambian)."""
    return _analyze_file(file_path, signature, f"{file_path} por signatura {signature}")


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
