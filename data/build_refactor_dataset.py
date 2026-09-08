"""
Construye un dataset de entrenamiento (CSV) a partir del log de refactorización:
por cada método del dataset ORIGINAL conserva su info y marca un único '1' en la
técnica que el bucle escogió (status=kept del log); el resto a 0. No usa el
proyecto refactorizado (out/), solo el dataset original y el log.

Uso:
    python data/build_refactor_dataset.py [--log data/refactor_log_lambda_05.jsonl]
                                          [--out data/refactor_training_dataset.csv]
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from refactor_loop import TARGETS  # reutiliza las columnas de técnicas

DATA_DIR = ROOT / "data"


def load_chosen_technique(log_path: Path) -> dict:
    """Técnica de refactorización elegida por el bucle por método.

    Devuelve {(project, file, start_line) -> technique} con la técnica del ÚLTIMO
    estado `kept` de cada método (el refactor que produce el estado final de la
    copia de trabajo). Usar el último estado a secas sería incorrecto: un método
    puede re-evaluarse después de ser refactorizado y quedar `keep_original` sin
    deshacer el refactor previo."""
    chosen = {}
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            if e.get("status") == "kept" and e.get("technique") in TARGETS \
                    and e.get("start_line") is not None:
                chosen[(e["project"], e["file"], int(e["start_line"]))] = e["technique"]
    return chosen


def build_project_csv(csv_path: Path, project: str, chosen: dict) -> pd.DataFrame:
    """Devuelve el CSV original del proyecto (sin filas duplicadas por clave) con
    las columnas de técnica marcadas a partir del log (un único '1' por método)."""
    df = pd.read_csv(csv_path)
    before = len(df)
    # Eliminar filas duplicadas por (file, method_start_line): el dataset original
    # contiene el mismo método repetido en algún caso (p. ej. analizado desde dos
    # raíces de módulo), lo que ensuciaría el dataset de entrenamiento.
    df = df.drop_duplicates(subset=["file", "method_start_line"], keep="first")
    for t in TARGETS:
        df[t] = 0
    for idx, row in df.iterrows():
        tech = chosen.get((project, str(row["file"]), int(row["method_start_line"])))
        if tech in TARGETS:
            df.at[idx, tech] = 1
    df.insert(0, "project", project)
    if before != len(df):
        print(f"    (se descartan {before - len(df)} filas duplicadas por clave)")
    return df


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dataset de entrenamiento a partir del log de refactorización")
    parser.add_argument("--log", default=str(DATA_DIR / "refactor_log_lambda_05.jsonl"),
                        help="Log del bucle de refactorización")
    parser.add_argument("--out", default=str(DATA_DIR / "refactor_training_dataset.csv"),
                        help="CSV de salida")
    args = parser.parse_args()

    status = load_chosen_technique(Path(args.log))
    print(f"Log: {args.log}")
    print(f"  {len(status)} métodos refactorizados (con técnica) en el log")

    frames = []
    for csv_path in sorted(DATA_DIR.glob("final_methods_dataset_*.csv")):
        project = csv_path.name.removeprefix("final_methods_dataset_").removesuffix(".csv")
        df = build_project_csv(csv_path, project, status)
        frames.append(df)
        print(f"  {project}: {len(df)} métodos")

    out = pd.concat(frames, ignore_index=True)
    out.to_csv(args.out, index=False)

    n_kept = int((out[TARGETS].sum(axis=1) > 0).sum())
    multi = int((out[TARGETS].sum(axis=1) > 1).sum())
    print(f"\nGuardado: {args.out}")
    print(f"  Filas: {len(out)} | con un '1' (refactorizado): {n_kept} | todo a 0: {len(out) - n_kept}")
    print(f"  Filas con >1 '1' (no debería haber): {multi}")
    print("  '1' por técnica:")
    for t in TARGETS:
        print(f"    {t.replace('refactor_', ''):<28} {int(out[t].sum())}")


if __name__ == "__main__":
    main()