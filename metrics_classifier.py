"""
Clasificador de oportunidades de refactorizacion basado UNICAMENTE en las
metricas estructurales del CSV (sin leer codigo fuente).

Uso:
    python metrics_classifier.py

Entrada:  aggregated_method_data.csv
Salida:   aggregated_method_data_metrics_labels.csv (con 5 columnas nuevas *_metrics)
          metrics_vs_script_comparison.png
"""

import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "analysis" / "output"
TARGETS = [
    "refactor_extract_method",
    "refactor_collapse_ifs_with_and",
    "refactor_lambda_map",
    "refactor_lambda_filter_map",
    "refactor_lambda_reduce",
]
OUTPUT_COLS = [f"{t}_metrics" for t in TARGETS]


def classify_from_metrics(row: pd.Series) -> dict[str, int]:
    """
    Reglas deterministicas basadas en las metricas del CSV.

    Cada tecnica recibe una puntuacion:
      - 3 puntos: patrones directos (collapse, map, filter_map, reduce)
      - 2 puntos: heuristica compuesta (extract_method)

    Se seleccionan las 2 tecnicas con mayor puntuacion (maximo 2 labels = 1).
    """
    scores: dict[str, int] = {}

    # ── Collapse ifs (&&) ──
    # nested_if_chains > 0 indica cadenas de if anidados sin else colapsables
    if row["nested_if_chains"] > 0:
        scores["refactor_collapse_ifs_with_and"] = 3

    # ── Lambda Map ──
    # map_candidate_loops > 0: foreach con un unico statement simple
    if row["map_candidate_loops"] > 0:
        scores["refactor_lambda_map"] = 3

    # ── Lambda Filter + Map ──
    if row["filter_map_candidate_loops"] > 0:
        scores["refactor_lambda_filter_map"] = 3

    # ── Lambda Reduce ──
    # reduce_candidate_loops > 0: foreach con acumulacion y a lo sumo un if de guarda
    if row["reduce_candidate_loops"] > 0:
        scores["refactor_lambda_reduce"] = 3

    # ── Extract Method ──
    # Heuristica compuesta: metodo grande + mucha estructura interna
    loc = row["loc"]
    stmt = row["statement_count"]
    branches = row["branch_count"]
    nesting = row["max_if_nesting"]
    chains_with_else = row["nested_if_chains_with_else"]
    foreach = row["foreach_count"]

    extract_score = 0
    if loc >= 80 and stmt >= 40:
        extract_score += 1
    if branches >= 8:
        extract_score += 1
    if chains_with_else >= 2 or nesting >= 3:
        extract_score += 1
    if foreach >= 2 and stmt >= 20:
        extract_score += 1

    if extract_score >= 2:
        scores["refactor_extract_method"] = 2

    # ── Seleccionar top 2 ──
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    chosen = {name for name, score in ordered[:2] if score >= 2}

    return {t: 1 if t in chosen else 0 for t in TARGETS}


def main() -> None:
    df = pd.read_csv(DATA_DIR / "aggregated_method_data.csv")

    # Aplicar clasificador a cada fila
    preds = df.apply(classify_from_metrics, axis=1, result_type="expand")
    for src, dst in zip(TARGETS, OUTPUT_COLS):
        df[dst] = preds[src]

    # Guardar CSV con etiquetas
    df.to_csv(OUTPUT_DIR / "aggregated_method_data_metrics_labels.csv", index=False)
    print(f"CSV guardado: analysis/output/aggregated_method_data_metrics_labels.csv")

    # ── Comparar con etiquetas del script original ──
    if all(t in df.columns for t in TARGETS):
        gt = df[TARGETS].fillna(0).astype(int)
        print(f"\n{'Label':<36} {'% acierto':>9} {'Script=1':>9} {'Metricas=1':>10} {'Recall':>7}")
        print("-" * 75)
        for t in TARGETS:
            script_pos = int(gt[t].sum())
            metric_pos = int(preds[t].sum())
            tp = int(((gt[t] == 1) & (preds[t] == 1)).sum())
            correct = tp + int(((gt[t] == 0) & (preds[t] == 0)).sum())
            pct = correct / len(df) * 100
            recall = tp / script_pos * 100 if script_pos > 0 else 0
            print(f"{t.replace('refactor_',''):<36} {pct:>8.1f}% {script_pos:>9} {metric_pos:>10} {recall:>6.1f}%")

        exact = int(((gt.values == preds.values).all(axis=1)).sum())
        print(f"\nExact match (5/5): {exact}/{len(df)} = {exact/len(df)*100:.1f}%")

        # Verificar maximo 2 labels
        over2 = int((preds.sum(axis=1) > 2).sum())
        print(f"Filas con >2 labels activos: {over2}")


if __name__ == "__main__":
    main()
