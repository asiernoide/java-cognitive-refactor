package com.tfm.astanalyzer;

/**
 * Contenedor de métricas estructurales de un método Java.
 * Gson serializa directamente los campos públicos a JSON.
 */
public class MethodMetrics {

    // --- Identificación ---
    public String method_name;
    public int method_start_line;
    public int method_end_line;
    public String method_signature;   // nombre(tipos de parámetros) — único dentro de una clase
    public String enclosing_class;    // clase(s) contenedora(s) separadas por '.' (p.ej. "Outer.Inner")

    // --- Complejidad cognitiva (SonarSource / S3776) ---
    // Replica local del algoritmo de Cognitive Complexity de SonarQube,
    // calculada sobre el AST con JavaParser (sin necesidad de SonarQube).
    public int cognitive_complexity;

    // --- Complejidad ciclomática (McCabe) y proxy de rendimiento ---
    // 1 + puntos de decisión (if, bucles, case, catch, ternario, &&/||).
    public int cyclomatic_complexity;
    // Nº de llamadas a métodos dentro del método (proxy de trabajo/allocaciones).
    public int method_invocations;

    // --- Tamaño ---
    public int loc;               // Líneas de código (end - start + 1)
    public int statement_count;   // Número de statements con valor semántico

    // --- Complejidad estructural ---
    public int branch_count;      // Ramas: if, else, case, catch
    public int max_if_nesting;    // Máxima profundidad de ifs anidados

    // --- Patrón colapsar ifs con && ---
    public int nested_if_chains;            // Ifs anidados directos sin else ni statements entre medias (colapsables con &&)
    public int nested_if_chains_with_else;  // Ifs anidados directos con else en el if interior (candidatos a Extract Method)

    // --- Patrones foreach / lambda ---
    public int foreach_count;          // Total de bucles foreach
    public int simple_foreach_count;   // Foreach sin estructuras de control anidadas
    public int map_candidate_loops;         // Foreach con cuerpo de un único statement (patrón map)
    public int filter_map_candidate_loops;  // Foreach con un if simple seguido de una operación (patrón filter+map)
    public int reduce_candidate_loops;      // Foreach que acumula un resultado sin bucles anidados (patrón reduce)

    // --- Métricas adicionales ---
    public int return_count;                // Número de sentencias return en el método
    public int try_catch_count;             // Número de bloques try/catch en el método
    public int switch_case_count;           // Número total de case en todos los switch del método
    public int loop_body_max_statements;    // Máximo de statements dentro de un mismo cuerpo de bucle (for/foreach/while)
    public int parameter_count;             // Número de parámetros del método
}
