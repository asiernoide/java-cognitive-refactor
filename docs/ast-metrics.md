# AST Analyzer — Métricas extraídas

Métricas estructurales calculadas por `ast-analyzer.jar` para cada método Java analizado. El analizador localiza el método en el AST —por línea, por signatura o mediante el modo `scan` de un proyecto— y devuelve el siguiente conjunto de métricas en formato JSON.

Además, calcula la `cognitive_complexity` replicando el algoritmo de Cognitive Complexity de SonarSource (ver [README — Complejidad cognitiva local](../README.md#complejidad-cognitiva-local)).

---

## Identificación del método

| Métrica | Tipo | Descripción |
|---|---|---|
| `method_name` | string | Nombre del método analizado. |
| `method_start_line` | int | Línea de inicio del método en el archivo fuente. |
| `method_end_line` | int | Línea de fin del método en el archivo fuente. |
| `method_signature` | string | Signatura `nombre(tipo1,tipo2)`, única dentro de una clase. Sirve para localizar el método por signatura (`java -jar ast-analyzer.jar <archivo> "nombre(tipos)"`), necesaria tras un refactor porque las líneas cambian. |
| `enclosing_class` | string | Clase(s) contenedora(s) separadas por `.` (p.ej. `Outer.Inner`). Permite desambiguar firmas repetidas en distintas clases del mismo archivo (`java -jar ast-analyzer.jar <archivo> "Clase.nombre(tipos)"`). |

---

## Tamaño

| Métrica | Tipo | Descripción |
|---|---|---|
| `loc` | int | Líneas de código del método (`end_line - start_line + 1`), calculadas sobre el rango exacto del método según el AST. |
| `statement_count` | int | Número de statements con valor semántico real (asignaciones, llamadas, returns, bucles, condicionales). Se excluyen bloques vacíos y puntos y coma sueltos. Señal principal para detectar candidatos a **Extract Method**. |
| `parameter_count` | int | Número de parámetros del método. Métodos con 5+ parámetros suelen indicar sobrecarga de responsabilidades y son candidatos a **Extract Method** o a encapsular parámetros en un objeto. |
| `return_count` | int | Número de sentencias `return` en el método. Múltiples puntos de salida temprana indican validaciones o ramas independientes extraíbles a métodos separados (**Extract Method**). |

---

## Complejidad estructural

| Métrica | Tipo | Descripción |
|---|---|---|
| `branch_count` | int | Número total de ramas en el método: cada `if`, cada `else`, cada `case` de un switch y cada `catch`. Indica la complejidad de flujo independientemente de su profundidad. |
| `max_if_nesting` | int | Máxima profundidad alcanzada por `if` anidados dentro de otros `if`. Un `else-if` en cadena plana no incrementa la profundidad. Valor 3 o superior suele indicar código difícil de seguir. |
| `try_catch_count` | int | Número de bloques `try/catch` en el método. Cada bloque es una sección autocontenida candidata natural a **Extract Method**. Dos o más bloques `try/catch` son un indicador muy fuerte. |
| `switch_case_count` | int | Número total de etiquetas `case` en todos los bloques `switch` del método. Un `switch` con 5+ casos sugiere **Extract Method** (cada caso a su propio método) o **Replace Conditional with Polymorphism**. |

---

## Patrón: colapsar ifs con `&&`

| Métrica | Tipo | Descripción |
|---|---|---|
| `nested_if_chains` | int | Número de cadenas de `if` anidados **directamente**, sin statements entre medias y **sin `else`** en el `if` interior. Detecta el patrón `if(a){ if(b){...` que puede simplificarse en `if(a && b){...` sin alterar la semántica. No cuenta casos con `else` ni casos con código entre los `if`. |
| `nested_if_chains_with_else` | int | Número de cadenas de `if` anidados directamente donde el `if` interior **sí tiene `else`**. Estos no son colapsables con `&&` porque el `else` pertenece al `if` interior y cambiaría el comportamiento. Son candidatos a **Extract Method** en su lugar. |

---

## Patrones: uso de streams y lambdas

| Métrica | Tipo | Descripción |
|---|---|---|
| `foreach_count` | int | Número total de bucles `for-each` en el método. Un número elevado sugiere que el método podría beneficiarse de la API de streams de Java. |
| `simple_foreach_count` | int | Número de bucles `for-each` cuyo cuerpo no contiene ninguna estructura de control anidada (`if`, bucles, `try/catch`, `switch`). Estos son los más directamente convertibles a una operación de stream (`map`, `forEach`, `collect`). |
| `map_candidate_loops` | int | Foreach cuyo cuerpo contiene **un único statement que no es un `if`**. Transformación directa de cada elemento, candidato a `stream().map().collect()`. Ejemplo: `for (User u : users) { result.add(u.getName()); }` |
| `filter_map_candidate_loops` | int | Foreach cuyo cuerpo contiene **un único `if` sin `else`** con un statement simple dentro. Combina filtrado y transformación, candidato a `stream().filter().collect()`. Ejemplo: `for (User u : users) { if (u.isActive()) result.add(u); }` |
| `reduce_candidate_loops` | int | Foreach que **acumula un resultado** con a lo sumo un `if` de guarda y sin bucles anidados. Candidato a `stream().filter().reduce()` o `stream().collect()`. Ejemplo: `for (int n : numbers) { if (n > 0) total += n; }` |
| `loop_body_max_statements` | int | Máximo número de statements dentro del cuerpo de un mismo bucle (`for`, `foreach`, `while`, `do-while`). Valores bajos (1-2) indican bucles trivialmente convertibles a stream; valores altos (5+) indican bucles que requerirían una reescritura mayor y que probablemente deban extraerse a un método propio. |

---

## Relación con las categorías de refactorización

Las métricas están diseñadas para alimentar las siguientes heurísticas de clasificación:

```
Extract Method       →  loc, statement_count, parameter_count, return_count,
                        try_catch_count, switch_case_count,
                        nested_if_chains_with_else

Colapsar ifs (&&)    →  max_if_nesting, nested_if_chains

Usar streams/lambdas →  foreach_count, simple_foreach_count,
                        map_candidate_loops, filter_map_candidate_loops,
                        reduce_candidate_loops, loop_body_max_statements
```
