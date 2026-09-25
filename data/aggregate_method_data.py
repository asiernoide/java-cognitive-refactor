"""Agrega los CSV por proyecto (`final_methods_dataset_*.csv`) en un único CSV
con columna `project`.

Uso (desde cualquier directorio):
    python data/aggregate_method_data.py

Entrada:  data/final_methods_dataset_<proyecto>.csv
Salida:   data/aggregated_method_data.csv
"""

from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent


def main() -> None:
    csv_files = sorted(
        p for p in DATA_DIR.glob("final_methods_dataset_*.csv")
        if p.name != "aggregated_method_data.csv"
    )
    if not csv_files:
        print(f"No se encontraron archivos 'final_methods_dataset_*.csv' en {DATA_DIR}")
        return

    print(f"Archivos encontrados: {len(csv_files)}")
    for f in csv_files:
        print(f"  - {f.name}")

    dataframes = []
    for f in csv_files:
        df = pd.read_csv(f)
        project = f.name.removeprefix("final_methods_dataset_").removesuffix(".csv")
        df.insert(0, "project", project)
        dataframes.append(df)
    aggregated = pd.concat(dataframes, ignore_index=True)

    output_file = DATA_DIR / "aggregated_method_data.csv"
    aggregated.to_csv(output_file, index=False)

    print(f"\nArchivo generado: '{output_file}'")
    print(f"   Filas totales: {len(aggregated)}")
    print(f"   Columnas: {list(aggregated.columns)}")


if __name__ == "__main__":
    main()
