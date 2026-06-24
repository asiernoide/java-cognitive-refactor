import pandas as pd
import glob
import os

# Encuentra todos los archivos .csv de entrada en la carpeta raíz
csv_files = [f for f in glob.glob("final_methods_dataset_*.csv") if os.path.basename(f) != "aggregated_method_data.csv"]

if not csv_files:
    print("No se encontraron archivos .csv en la carpeta actual.")
    exit()

print(f"Archivos encontrados: {len(csv_files)}")
for f in csv_files:
    print(f"  - {f}")

# Lee y concatena todos los archivos
dataframes = [pd.read_csv(f) for f in csv_files]
aggregated = pd.concat(dataframes, ignore_index=True)

# Guarda el resultado
output_file = "aggregated_method_data.csv"
aggregated.to_csv(output_file, index=False)

print(f"\nArchivo generado: '{output_file}'")
print(f"   Filas totales: {len(aggregated)}")
print(f"   Columnas: {list(aggregated.columns)}")
