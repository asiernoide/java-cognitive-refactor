"""
Clasificador de oportunidades de refactorizacion basado UNICAMENTE en las
metricas estructurales del CSV (sin leer codigo fuente).

Uso:
    python metrics_classifier.py

Entrada:  data/aggregated_method_data.csv (con columna `project`)
Salida:   analysis/output/aggregated_method_data_metrics_labels.csv, con las
          recomendaciones en las 5 columnas refactor_* (1/0), que es lo que
          consume refactor_loop.py
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


def extract_method_score(row: pd.Series) -> int:
    """Heuristica compuesta de Extract Method: nº de condiciones cumplidas (0-4)."""
    score = 0
    if row["loc"] >= 80 and row["statement_count"] >= 40:
        score += 1
    if row["branch_count"] >= 8:
        score += 1
    if row["nested_if_chains_with_else"] >= 2 or row["max_if_nesting"] >= 3:
        score += 1
    if row["foreach_count"] >= 2 and row["statement_count"] >= 20:
        score += 1
    return score


def classify_from_metrics(row: pd.Series) -> dict[str, int]:
    """
    Reglas deterministicas basadas en las metricas del CSV.

    Cada tecnica recibe una puntuacion:
      - 3 puntos: patrones directos (collapse, map, filter_map, reduce)
      - 2 puntos: heuristica compuesta (extract_method)

    Se seleccionan las 2 tecnicas con mayor puntuacion (maximo 2 labels = 1);
    en caso de empate se prioriza la de mayor valor de su metrica de activacion.
    """
    scores: dict[str, int] = {}
    track: dict[str, int] = {}

    # ── Collapse ifs (&&) ──
    # nested_if_chains > 0 indica cadenas de if anidados sin else colapsables
    if row["nested_if_chains"] > 0:
        scores["refactor_collapse_ifs_with_and"] = 3
        track["refactor_collapse_ifs_with_and"] = int(row["nested_if_chains"])

    # ── Lambda Map ──
    # map_candidate_loops > 0: foreach con un unico statement simple
    if row["map_candidate_loops"] > 0:
        scores["refactor_lambda_map"] = 3
        track["refactor_lambda_map"] = int(row["map_candidate_loops"])

    # ── Lambda Filter + Map ──
    if row["filter_map_candidate_loops"] > 0:
        scores["refactor_lambda_filter_map"] = 3
        track["refactor_lambda_filter_map"] = int(row["filter_map_candidate_loops"])

    # ── Lambda Reduce ──
    # reduce_candidate_loops > 0: foreach con acumulacion y a lo sumo un if de guarda
    if row["reduce_candidate_loops"] > 0:
        scores["refactor_lambda_reduce"] = 3
        track["refactor_lambda_reduce"] = int(row["reduce_candidate_loops"])

    # ── Extract Method ──
    score = extract_method_score(row)
    if score >= 2:
        scores["refactor_extract_method"] = 2
        track["refactor_extract_method"] = score

    # ── Seleccionar top 2: puntuacion desc; empate -> mayor metrica de activacion ──
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], -track[kv[0]]))
    chosen = {name for name, score in ordered[:2] if score >= 2}

    return {t: 1 if t in chosen else 0 for t in TARGETS}


def main() -> None:
    df = pd.read_csv(DATA_DIR / "aggregated_method_data.csv")

    # Etiquetas de referencia (si el agregado ya las trae) para la comparativa.
    reference = df[TARGETS].copy() if all(t in df.columns for t in TARGETS) else None

    # Aplicar el clasificador a cada fila y escribir las recomendaciones como refactor_*
    preds = df.apply(classify_from_metrics, axis=1, result_type="expand")
    for t in TARGETS:
        df[t] = preds[t]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_DIR / "aggregated_method_data_metrics_labels.csv", index=False)
    print("CSV guardado: analysis/output/aggregated_method_data_metrics_labels.csv")

    # ── Comparar con etiquetas de referencia, si el CSV de entrada las traía ──
    if reference is not None:
        gt = reference.fillna(0).astype(int)
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
