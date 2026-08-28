import os

from dotenv import load_dotenv

import lib.ast_analyzer as ast

load_dotenv()

PROJECTS_DIR = os.getenv("PROJECTS_DIR", "projects")
DATA_DIR = os.getenv("DATA_DIR", "data")
CC_THRESHOLD = int(os.getenv("CC_THRESHOLD", "15"))


def main():
    # Descubrir proyectos en el directorio projects/
    if not os.path.isdir(PROJECTS_DIR):
        print(f"ERROR: No se encontró el directorio '{PROJECTS_DIR}'")
        return

    project_dirs = sorted(
        d for d in os.listdir(PROJECTS_DIR)
        if os.path.isdir(os.path.join(PROJECTS_DIR, d))
    )
    print(f"Proyectos encontrados: {len(project_dirs)}")
    for pd_name in project_dirs:
        print(f"  - {pd_name}")

    os.makedirs(DATA_DIR, exist_ok=True)

    for project_key in project_dirs:
        project_path = os.path.abspath(os.path.join(PROJECTS_DIR, project_key))
        print(f"\n{'='*60}")
        print(f"  PROCESANDO: {project_key}")
        print(f"  Ruta: {project_path}")
        print(f"{'='*60}")

        # Detección local de métodos complejos (CC > umbral) sin SonarQube
        print(f"  Escaneando métodos con cognitive_complexity > {CC_THRESHOLD} ...")
        final_df = ast.scan_project_complex_methods(
            project_path, threshold=CC_THRESHOLD
        )

        if final_df.empty:
            print(f"  Sin métodos complejos para {project_key}. Saltando.")
            continue

        print(f"  Métodos complejos encontrados: {len(final_df)}")

        # Guardar CSV individual
        csv_name = f"final_methods_dataset_{project_key}.csv"
        csv_path = os.path.join(DATA_DIR, csv_name)
        final_df.to_csv(csv_path, index=False)
        print(f"  Dataset guardado: {csv_path} ({len(final_df)} filas)")

    print(f"\n--- Pipeline completado ---")


if __name__ == "__main__":
    main()