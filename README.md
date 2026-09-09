# java-cognitive-refactor

Sistema que detecta métodos Java con **complejidad cognitiva (CC)** excesiva y aplica sobre ellos técnicas de refactorización de forma automática para reducirla. El flujo completo funciona en dos fases:

1. **Detección y clasificación.** Un analizador AST local (JavaParser) escanea los proyectos de `projects/`, detecta los métodos con `cognitive_complexity > CC_THRESHOLD` y extrae sus métricas estructurales. Sobre esas métricas, un clasificador determinista (`metrics_classifier.py`) asigna a cada método hasta **dos técnicas de refactorización candidatas** (sin leer el código fuente).
2. **Refactorización con LLM.** Para cada método con técnicas candidatas, un LLM genera una versión refactorizada por técnica. Cada candidato se aplica sobre una **copia de trabajo** (`out/`, los proyectos originales quedan intactos), se miden las métricas resultantes y el cambio se deshace con `git`; solo se conserva el candidato que mejora el método original. Cada intento queda registrado en `data/refactor_log.jsonl`, que permite reanudar ejecuciones y generar un **dataset de entrenamiento** para modelos ML que predigan la técnica adecuada.

![Diagrama de flujo del sistema: análisis y detección de métodos complejos, clasificación por heurístico de las técnicas candidatas y bucle de refactorización con LLM por método del dataset; el registro de refactorizaciones alimenta el dataset de entrenamiento.](docs/images/pipeline.png)

## Complejidad cognitiva local

La métrica `cognitive_complexity` se calcula **100 % local** (sin SonarQube), replicando la implementación de referencia de SonarSource —`org.sonar.java.ast.visitors.CognitiveComplexityVisitor` de [sonar-java](https://github.com/SonarSource/sonar-java)— y no solo la especificación, para que los valores coincidan con los que reporta la regla `java:S3776` de SonarQube.

- **Implementación:** `ast-analyzer/src/main/java/com/tfm/astanalyzer/CognitiveComplexityVisitor.java` (visitante del AST con entry point `computeComplexity`).
- **Integración:** la usa `ASTAnalyzer.analyzeMethod`; aparece como campo `cognitive_complexity` en los modos por línea/signatura y en `scan`.
- **Uso del analizador:** lo invoca `lib/ast_analyzer.py` (`scan_project_complex_methods`) desde `main.py` y `refactor_loop.py`.

## Requisitos

- **Python 3.10+** con las dependencias de `requirements.txt`:
  ```bash
  pip install -r requirements.txt
  ```
- **Java 17+** para ejecutar `ast-analyzer/ast-analyzer.jar`.
- **git** (copias de trabajo y trazabilidad de cambios).
- Un **LLM** accesible mediante API compatible con OpenAI (`/chat/completions`): OpenAI, OpenRouter, Ollama, etc.

## Compilación del analizador

El JAR `ast-analyzer/ast-analyzer.jar` no se versiona; se genera con Maven:

```bash
cd ast-analyzer/
mvn package
copy target\ast-analyzer.jar ast-analyzer.jar
cd ..
```

## Configuración

Copia `.env.example` a `.env` y rellena, al menos, las variables `LLM_*`. Los parámetros principales son:

| Grupo | Variables |
|---|---|
| Directorios y umbral | `PROJECTS_DIR` (proyectos a analizar), `DATA_DIR` (datasets y logs), `WORK_DIR` (copias de trabajo, por defecto `out/`), `CC_THRESHOLD` (15) |
| Refactorización | `REFACTOR_MODE` (`stream`/`batch`), `REFACTOR_CYCLO_PENALTY` (λ), `REFACTOR_MAX_CYCLO_DELTA_PCT`, `REFACTOR_SEED`, `REFACTOR_MAX_TOKENS`, `REFACTOR_BATCH_MAX_TOKENS`, `REFACTOR_BATCH_TIMEOUT`, `REFACTOR_BATCH_RETRIES`, `REFACTOR_RUN_TESTS` |
| LLM | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`, `LLM_TIMEOUT`, `LLM_REASONING_EFFORT` |

### Criterio de aceptación de candidatos

De las versiones generadas para un método se elige la de mayor score, con penalización del aumento de complejidad ciclomática:

```
S = ΔCC − λ·max(0, ΔCyclo)
```

con `ΔCC = CC_base − CC_candidato` y `ΔCyclo = Cyclo_candidato − Cyclo_base`. Solo se aplica (y se commitea en la copia de trabajo) el candidato con `S > 0`; en caso contrario se mantiene el método original. Empates → menor ciclomática; después → semilla `REFACTOR_SEED`. En **Extract Method** la CC/ciclomática del candidato se mide como **total = método principal + métodos extraídos**, para que extraer lógica a sub-métodos no reduzca la CC artificialmente.

### Validación de candidatos

Un candidato es válido si el archivo sigue siendo parseable y el método se localiza por signatura con la firma original; las salidas no parseables, vacías, con *fences* de Markdown o que cambian la firma se descartan. `REFACTOR_RUN_TESTS` (`never`/`auto`/`always`, **experimental**) ejecuta y cronometra la suite de pruebas del proyecto antes/después del pase cuando hay build tool y tests; el resto del pipeline solo depende del parseo.

## Uso

### 1. Detección local → CSV por proyecto

```bash
python main.py
```

Escanea cada proyecto de `projects/` (una subcarpeta por proyecto) y genera `data/final_methods_dataset_<proyecto>.csv` con los métodos de `cognitive_complexity > CC_THRESHOLD` y sus métricas.

### 2. Consolidar los CSV en un dataset agregado

```bash
python data/aggregate_method_data.py
```

Genera `data/aggregated_method_data.csv` a partir de todos los `final_methods_dataset_*.csv`.

### 3. Clasificador determinista (técnicas candidatas)

```bash
python metrics_classifier.py
```

Aplica las reglas sobre las métricas del CSV agregado y guarda el resultado (columnas `*_metrics`, valor `1`/`0`) en `analysis/output/aggregated_method_data_metrics_labels.csv`. Si el CSV de entrada ya trae etiquetas de referencia, imprime además una comparativa de acierto/recall.

### 4. Bucle de refactorización con LLM

```bash
python refactor_loop.py [--project <proyecto>] [--limit N] [--dry-run]
```

Crea (o reutiliza) las copias de trabajo en `WORK_DIR` y procesa cada método de los CSV por proyecto con técnicas marcadas (`refactor_*` = 1). Los métodos se localizan por **signatura** (`nombre + tipos de parámetros`, única dentro de una clase) porque tras editar un archivo las líneas cambian. Cada intento (válido o no) se registra en `data/refactor_log.jsonl`. `--dry-run` no deja cambios ni commits.

- **Modo batch:** `REFACTOR_MODE=batch` agrupa varios métodos en una sola llamada LLM (JSON); los que fallan (no parsean, JSON inválido, ausentes) se reintentan en rondas dirigidas (`REFACTOR_BATCH_RETRIES`) reenviando el error exacto al modelo.
- **Ejecución paralela:** `run_parallel.py` reparte los proyectos entre N workers (un proyecto lo procesa un solo worker, cada uno con su workspace y su log) y fusiona después los logs parciales:

```bash
python run_parallel.py -n 4 [--fresh] [--dry-run --limit N] [--resume F]
```

### 5. Dataset de entrenamiento desde el log

```bash
python data/build_refactor_dataset.py --log data/refactor_log_lambda_05.jsonl \
    --out data/refactor_training_dataset.csv
```

Combina el dataset original de métodos (con sus métricas) y el log del bucle: cada método conserva sus métricas y marca con **un único `1`** la técnica que el bucle aplicó de verdad (refactor con estado `kept` del log); el resto a 0 y los no refactorizados quedan con todo a 0.

### 6. Uso directo del analizador AST

```bash
# Escaneo de un proyecto: métodos con cognitive_complexity > umbral (15 por defecto)
java -jar ast-analyzer/ast-analyzer.jar scan <raiz_proyecto> [umbral]

# Por línea (legado)
java -jar ast-analyzer/ast-analyzer.jar <archivo.java> <linea>

# Por signatura: nombre + tipos de parámetros
java -jar ast-analyzer/ast-analyzer.jar <archivo.java> "nombreMetodo(int, java.lang.String)"

# Por signatura con clase contenedora (varias clases en el archivo)
java -jar ast-analyzer/ast-analyzer.jar <archivo.java> "MiClase.nombreMetodo(int)"
```

Las raíces de fuentes se autodetectan por módulo: cada directorio con `pom.xml`, `build.gradle` o `build.xml` aporta su `src/main/java` (o `src` en layouts Ant); se excluyen tests y directorios de build.

## Técnicas de refactorización

Cada método se etiqueta con hasta 2 de las 5 técnicas siguientes (columnas `refactor_*` en los CSV):

| Técnica | Columna en el CSV |
|---|---|
| Extract Method | `refactor_extract_method` |
| Colapsar `if` anidados con `&&` | `refactor_collapse_ifs_with_and` |
| Lambda Map (`stream().map()`) | `refactor_lambda_map` |
| Lambda Filter + Map (`stream().filter().map()`) | `refactor_lambda_filter_map` |
| Lambda Reduce (`stream().reduce()`) | `refactor_lambda_reduce` |

Las reglas exactas del clasificador se documentan en [docs/labeler-script.md](docs/labeler-script.md).

## Estructura del repositorio

```
java-cognitive-refactor/
├── main.py                  # Detección local → CSV por proyecto
├── metrics_classifier.py    # Clasificador determinista (reglas sobre métricas)
├── refactor_loop.py         # Bucle de refactorización con LLM
├── run_parallel.py          # Lanzador paralelo del bucle (N workers)
├── lib/                     # ast_analyzer.py · llm_client.py · workspace.py
├── ast-analyzer/            # Analizador Java (Maven + JavaParser) → .jar
├── data/                    # Scripts de agregación/dataset y salidas (CSV, logs)
├── docs/                    # ast-metrics.md · labeler-script.md · images/
├── projects/                # (no versionado) proyectos Java a analizar
├── out/                     # (no versionado) copias de trabajo del refactor
├── requirements.txt
├── .env.example             # Plantilla de configuración
└── README.md
```

## Documentación

- [docs/ast-metrics.md](docs/ast-metrics.md) — métricas estructurales que extrae el analizador por método.
- [docs/labeler-script.md](docs/labeler-script.md) — reglas de activación y priorización del clasificador.
