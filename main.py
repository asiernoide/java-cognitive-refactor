import os

from dotenv import load_dotenv

import lib.sonarqube_api as sq
import lib.ast_analyzer as ast

load_dotenv()

PROJECTS_DIR = os.getenv("PROJECTS_DIR", "projects")
DATA_DIR = os.getenv("DATA_DIR", "data")
SONAR_URL = os.getenv("SONAR_URL", "http://localhost:9000")
SONAR_TOKEN = os.getenv("SONAR_TOKEN", "")


def main():
    if not SONAR_TOKEN:
        print("ERROR: SONAR_TOKEN no esta definido en .env")
        return

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

        # 1. Obtener métodos complejos desde SonarQube
        try:
            sonar_df = sq.fetch_complex_methods(
                sonar_url=SONAR_URL,
                project_key=project_key,
                token=SONAR_TOKEN,
            )
        except Exception as e:
            print(f"  [ERROR] SonarQube falló para {project_key}: {e}")
            continue

        if sonar_df.empty:
            print(f"  Sin métodos complejos para {project_key}. Saltando.")
            continue

        print(f"  Métodos complejos encontrados: {len(sonar_df)}")

        # 2. Enriquecer con métricas AST
        final_df = ast.enrich_with_ast(sonar_df, java_src_root=project_path)

        if final_df.empty:
            print(f"  [WARN] No se generaron filas para {project_key}.")
            continue

        # 3. Guardar CSV individual
        csv_name = f"final_methods_dataset_{project_key}.csv"
        csv_path = os.path.join(DATA_DIR, csv_name)
        final_df.to_csv(csv_path, index=False)
        print(f"  Dataset guardado: {csv_path} ({len(final_df)} filas)")

    print(f"\n--- Pipeline completado ---")


if __name__ == "__main__":
    main()