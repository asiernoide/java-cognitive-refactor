# Clasificador de refactorizaciones por reglas sobre métricas

`metrics_classifier.py` asigna a cada método Java hasta **dos de las cinco técnicas** de refactorización consideradas, aplicando reglas deterministas sobre las métricas estructurales del CSV (**sin leer el código fuente**). La clasificación es transparente y reproducible: cada técnica se activa con condiciones explícitas sobre una métrica (patrón directo) o con una heurística compuesta.

## Técnicas de refactorización y reglas de activación

### 1. Collapse nested ifs using `&&` (`refactor_collapse_ifs_with_and`)

Transforma `if` anidados directamente en una sola condición compuesta.

| Regla | Métrica utilizada |
|---|---|
| Se activa si hay **al menos 1** cadena de `if` anidados sin `else` | `nested_if_chains > 0` |

`nested_if_chains` cuenta cadenas del tipo `if(a) { if(b) { ... } }` donde el `if` interior no tiene `else`. Son directamente colapsables con `&&` sin alterar la semántica.

### 2. Lambda Map (`refactor_lambda_map`)

Sustituye un `foreach` con transformación elemento a elemento por `stream().map()`.

| Regla | Métrica utilizada |
|---|---|
| Se activa si hay **al menos 1** bucle candidato a map | `map_candidate_loops > 0` |

`map_candidate_loops` cuenta bucles `foreach` cuyo cuerpo es un único statement que no es un `if`: una transformación directa por elemento.

### 3. Lambda Filter + Map (`refactor_lambda_filter_map`)

Sustituye un `foreach` con filtrado y transformación por `stream().filter().map()`.

| Regla | Métrica utilizada |
|---|---|
| Se activa si hay **al menos 1** bucle candidato a filter+map | `filter_map_candidate_loops > 0` |

`filter_map_candidate_loops` cuenta bucles `foreach` con un único `if` sin `else` que contiene un statement simple.

### 4. Lambda Reduce (`refactor_lambda_reduce`)

Sustituye un `foreach` con acumulación por `stream().reduce()`.

| Regla | Métrica utilizada |
|---|---|
| Se activa si hay **al menos 1** bucle candidato a reduce | `reduce_candidate_loops > 0` |

`reduce_candidate_loops` cuenta bucles `foreach` que acumulan un resultado con a lo sumo un `if` de guarda, sin bucles anidados.

### 5. Extract Method (`refactor_extract_method`)

Extrae un bloque lógico del método a un nuevo método privado.

| Regla | Métrica utilizada |
|---|---|
| Método grande y denso | `loc >= 80` **y** `statement_count >= 40` |
| Muchas ramas de decisión | `branch_count >= 8` |
| Ifs anidados con else o anidamiento profundo | `nested_if_chains_with_else >= 2` **o** `max_if_nesting >= 3` |
| Múltiples bucles en método denso | `foreach_count >= 2` **y** `statement_count >= 20` |

**Se activa si se cumplen al menos 2 de las 4 condiciones anteriores.**

## Priorización

Cada técnica recibe una puntuación:

| Técnica | Puntuación | Tipo |
|---|---|---|
| Collapse ifs | 3 | Patrón directo |
| Lambda Map | 3 | Patrón directo |
| Lambda Filter+Map | 3 | Patrón directo |
| Lambda Reduce | 3 | Patrón directo |
| Extract Method | 2 | Heurística compuesta |

Se seleccionan las **2 técnicas con mayor puntuación**. Si hay empate, se prioriza la técnica con **mayor valor de su métrica de activación** (la métrica que la activa cuando es `> 0`; en `extract_method`, el número de condiciones cumplidas de su heurística). Las técnicas con puntuación inferior a 2 no se seleccionan.

Por ejemplo, un método con `nested_if_chains` y `map_candidate_loops` positivos recibe las etiquetas `collapse_ifs` y `lambda_map`; si además cumpliera la heurística de `extract_method`, esta se descarta por tener menor puntuación.

## Entrada y salida

| | Ruta |
|---|---|
| **Entrada** | `data/aggregated_method_data.csv` |
| **Salida** | `analysis/output/aggregated_method_data_metrics_labels.csv` |

El CSV de salida contiene todas las columnas originales más 5 columnas nuevas con el sufijo `_metrics` (ej: `refactor_extract_method_metrics`), con valor `1` o `0`.

Además, si el CSV de entrada ya contiene columnas de refactorización (etiquetas de referencia), el script imprime una comparativa: porcentaje de acierto y recall por label.

## Uso

```bash
python metrics_classifier.py
```

## Limitaciones

- **No inspecciona el código fuente.** Las reglas dependen completamente de la calidad y precisión de las métricas extraídas por `ast-analyzer.jar`. Si el analizador AST omite un patrón, el clasificador no puede detectarlo.
- **Las métricas de bucles (`map_candidate_loops`, etc.) solo cubren `foreach`.** Los bucles `for` clásicos y `while` no generan estas métricas, por lo que el clasificador no puede recomendar streams/lambdas para ellos.
- **El criterio de Extract Method es una heurística compuesta.** Los umbrales (`loc >= 80`, `branch_count >= 8`, etc.) se calibraron empíricamente y pueden no ser óptimos para todos los proyectos.
- **Etiquetas automáticas, no supervisadas.** No hay validación humana de las etiquetas generadas.
