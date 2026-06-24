# TFM — Reducción automática de la complejidad cognitiva en código Java

Sistema de detección, clasificación y refactorización automática de métodos Java con complejidad cognitiva excesiva. Forma parte del Trabajo de Fin de Máster del Máster en Ingeniería del Software e Inteligencia Artificial (MISIA).

---

## Arquitectura del TFM

El proyecto se divide en dos fases:

1. **Detección y clasificación** *(implementada)*: SonarQube detecta métodos con `cognitive_complexity > 15` (regla `java:S3776`). Un analizador AST extrae 16 métricas estructurales de cada método. Un clasificador determinista (`metrics_classifier.py`) aplica reglas sobre esas métricas para determinar qué técnica de refactorización corresponde.
2. **Refactorización automática** *(pendiente)*: un LLM aplicará la técnica recomendada generando el código refactorizado.

```
SonarQube (Docker)          AST Analyzer (JavaParser)        metrics_classifier.py
     │                              │                              │
     ▼                              ▼                              ▼
 complex_methods.csv ──────► enriquecimiento AST ──────► etiquetas de refactorización
```

---

## Estructura del proyecto

```
Scripts/
├── main.py                       # Pipeline principal
├── metrics_classifier.py         # Clasificador basado en reglas sobre métricas
│
├── lib/
│   ├── sonarqube_api.py          # Cliente HTTP para SonarQube
│   └── ast_analyzer.py           # Wrapper del analizador AST (Java)
│
├── ast-analyzer/                 # Módulo Java (Maven + JavaParser)
│   ├── pom.xml
│   ├── src/main/java/com/tfm/astanalyzer/
│   │   ├── ASTAnalyzer.java      # Análisis del AST
│   │   └── MethodMetrics.java    # Contenedor de métricas
│   └── ast-analyzer.jar
│
├── data/
│   ├── aggregate_method_data.py  # Script de consolidación de CSVs
│   ├── aggregated_method_data.csv
│   └── final_methods_dataset_*.csv
│
├── docs/
│   ├── ast-metrics.md            # Documentación de métricas extraídas
│   └── labeler-script.md         # Documentación del script de etiquetado
│
├── projects/                     # Código fuente de proyectos analizados
├── requirements.txt
├── .env                          # Variables de entorno (no versionado)
├── .env.example                  # Plantilla de variables de entorno
├── .gitignore
└── README.md
```

---

## Requisitos

- **Python 3.10+** con las dependencias de `requirements.txt`:
  ```bash
  pip install -r requirements.txt
  ```
- **Java 17+** para ejecutar `ast-analyzer.jar`.
- **SonarQube** ejecutándose en Docker en `http://localhost:9000` con los proyectos a analizar ya indexados. Cada proyecto debe estar registrado en SonarQube con un **Project Key** que coincida exactamente (1:1) con el nombre de su carpeta dentro de `projects/`. Ejemplo: si la carpeta es `projects/mi-proyecto`, el Project Key en SonarQube debe ser `mi-proyecto`.
- **Maven** para reconstruir el analizador AST (solo si se modifica el código Java).

---

## Configuración

Copia `.env.example` a `.env` y rellena los valores:

```env
SONAR_URL=http://localhost:9000
SONAR_TOKEN=your_sonarqube_token_here
PROJECTS_DIR=projects
DATA_DIR=data
```

---

## Uso

### 1. Pipeline completo (SonarQube → AST → CSV)

```bash
cd Scripts/
python main.py
```

Recorre todos los proyectos en `projects/`, consulta SonarQube para cada uno, analiza el AST de cada método y genera los CSV individuales en `data/`.

### 2. Consolidar CSVs individuales en uno agregado

```bash
python data/aggregate_method_data.py
```

Genera `data/aggregated_method_data.csv` a partir de todos los `final_methods_dataset_*.csv`.

### 3. Clasificador por reglas (métricas del CSV)

```bash
python metrics_classifier.py
```

Aplica reglas deterministas sobre las métricas del CSV para predecir refactorizaciones. El CSV de salida se guarda en `analysis/output/`.

### 4. Reconstruir el analizador AST

```bash
cd ast-analyzer/
mvn package
copy target\ast-analyzer.jar ast-analyzer.jar
```

---

## Técnicas de refactorización evaluadas

El proyecto etiqueta cada método con hasta 2 de las siguientes 5 técnicas:

| Técnica | Columna en el CSV |
|---|---|
| Extract Method | `refactor_extract_method` |
| Collapse nested ifs with `&&` | `refactor_collapse_ifs_with_and` |
| Lambda Map (`stream().map()`) | `refactor_lambda_map` |
| Lambda Filter + Map (`stream().filter().map()`) | `refactor_lambda_filter_map` |
| Lambda Reduce (`stream().reduce()`) | `refactor_lambda_reduce` |

Cada columna contiene `1` si la técnica es aplicable o `0` si no lo es.

La documentación detallada de las reglas de clasificación y el funcionamiento de `metrics_classifier.py` está en [docs/labeler-script.md](docs/labeler-script.md).

---

## Métricas del dataset

El dataset agregado contiene 26 columnas: 5 de identificación (`file`, `method_name`, `method_start_line`, `method_end_line`, `cognitive_complexity`), 16 métricas estructurales extraídas del AST, y 5 columnas de refactorización.

La documentación detallada de cada métrica está en [docs/ast-metrics.md](docs/ast-metrics.md).
