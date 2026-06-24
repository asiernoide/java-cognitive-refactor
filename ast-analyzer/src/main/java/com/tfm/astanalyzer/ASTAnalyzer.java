package com.tfm.astanalyzer;

import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.CallableDeclaration;
import com.github.javaparser.ast.body.ConstructorDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.stmt.*;
import com.github.javaparser.ast.expr.LambdaExpr;
import com.github.javaparser.ast.stmt.ReturnStmt;
import com.github.javaparser.ast.stmt.TryStmt;
import com.github.javaparser.ast.stmt.SwitchStmt;
import com.github.javaparser.ast.stmt.SwitchEntry;
import com.github.javaparser.ast.stmt.ForStmt;
import com.github.javaparser.ast.stmt.WhileStmt;
import com.github.javaparser.ast.stmt.DoStmt;
import com.github.javaparser.ast.body.Parameter;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;

import java.io.FileInputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/**
 * Analizador de AST para métodos Java.
 *
 * Uso:
 *   java -jar ast-analyzer.jar <ruta_archivo.java> <numero_linea>
 *
 * Salida (stdout):
 *   JSON con métricas estructurales del método que contiene la línea indicada.
 */
public class ASTAnalyzer {

    public static void main(String[] args) {
        if (args.length < 2) {
            System.err.println("Uso: java -jar ast-analyzer.jar <archivo.java> <linea>");
            System.exit(1);
        }

        String filePath = args[0];
        int targetLine;

        try {
            targetLine = Integer.parseInt(args[1]);
        } catch (NumberFormatException e) {
            System.err.println("Error: la línea debe ser un número entero.");
            System.exit(1);
            return;
        }

        try {
            CompilationUnit cu = StaticJavaParser.parse(new FileInputStream(filePath));

            // Reunir métodos y constructores en una lista común.
            // JavaParser los representa con nodos distintos (MethodDeclaration y
            // ConstructorDeclaration), pero ambos implementan CallableDeclaration,
            // que proporciona nombre, cuerpo y rango de forma uniforme.
            List<CallableDeclaration<?>> allCallables = new ArrayList<>();
            allCallables.addAll(cu.findAll(MethodDeclaration.class));
            allCallables.addAll(cu.findAll(ConstructorDeclaration.class));

            List<CallableDeclaration<?>> withRange = allCallables.stream()
                    .filter(m -> m.getRange().isPresent())
                    .toList();

            // Fase 1: búsqueda exacta — la línea cae dentro del rango del callable.
            // Si hay callables anidados (clases internas), quedarse con el más pequeño.
            Optional<CallableDeclaration<?>> found = withRange.stream()
                    .filter(m -> {
                        int begin = m.getRange().get().begin.line;
                        int end   = m.getRange().get().end.line;
                        return targetLine >= begin && targetLine <= end;
                    })
                    .min((a, b) -> {
                        int sizeA = a.getRange().get().end.line - a.getRange().get().begin.line;
                        int sizeB = b.getRange().get().end.line - b.getRange().get().begin.line;
                        return Integer.compare(sizeA, sizeB);
                    });

            // Fase 2: búsqueda por proximidad — SonarQube a veces reporta la línea
            // de la firma de un callable multilínea, que puede quedar fuera del rango
            // exacto. Se busca el callable cuyo inicio sea el más cercano dentro de
            // un margen de 5 líneas.
            if (found.isEmpty()) {
                found = withRange.stream()
                        .filter(m -> {
                            int begin = m.getRange().get().begin.line;
                            return begin > targetLine - 5 && begin <= targetLine + 1;
                        })
                        .min((a, b) -> {
                            int distA = Math.abs(a.getRange().get().begin.line - targetLine);
                            int distB = Math.abs(b.getRange().get().begin.line - targetLine);
                            return Integer.compare(distA, distB);
                        });
            }

            if (found.isEmpty()) {
                System.err.println("No se encontró ningún método en la línea " + targetLine);
                System.exit(2);
                return;
            }

            CallableDeclaration<?> method = found.get();
            MethodMetrics metrics = analyzeMethod(method);

            Gson gson = new GsonBuilder().setPrettyPrinting().create();
            System.out.println(gson.toJson(metrics));

        } catch (Exception e) {
            System.err.println("Error al parsear el archivo: " + e.getMessage());
            System.exit(3);
        }
    }

    /**
     * Extrae el cuerpo de un método o constructor.
     * CallableDeclaration no expone getBody() directamente en la API de JavaParser;
     * cada subclase lo declara por separado con tipo de retorno distinto.
     */
    private static Optional<BlockStmt> getBody(CallableDeclaration<?> callable) {
        if (callable instanceof MethodDeclaration m)      return m.getBody();
        if (callable instanceof ConstructorDeclaration c) return Optional.of(c.getBody());
        return Optional.empty();
    }

        // -------------------------------------------------------------------------
    // ANÁLISIS DEL MÉTODO
    // -------------------------------------------------------------------------

    static MethodMetrics analyzeMethod(CallableDeclaration<?> method) {
        MethodMetrics m = new MethodMetrics();

        int startLine = method.getRange().get().begin.line;
        int endLine   = method.getRange().get().end.line;

        m.method_name       = method.getNameAsString();
        m.method_start_line = startLine;
        m.method_end_line   = endLine;
        m.loc               = endLine - startLine + 1;

        // Recoger todos los statements del cuerpo del método
        List<Statement> allStatements = method.findAll(Statement.class);

        m.statement_count = countStatements(allStatements);
        m.branch_count    = countBranches(method);

        // Métricas de ifs anidados
        m.max_if_nesting              = computeMaxIfNesting(method);
        m.nested_if_chains            = countNestedIfChains(method, false);
        m.nested_if_chains_with_else  = countNestedIfChains(method, true);

        // Métricas de bucles foreach
        List<ForEachStmt> foreachLoops = method.findAll(ForEachStmt.class);
        m.foreach_count              = foreachLoops.size();
        m.simple_foreach_count       = countSimpleForeachLoops(foreachLoops);
        m.map_candidate_loops        = countMapCandidateLoops(foreachLoops);
        m.filter_map_candidate_loops = countFilterMapCandidateLoops(foreachLoops);
        m.reduce_candidate_loops     = countReduceCandidateLoops(foreachLoops);

        // Métricas adicionales
        m.return_count              = method.findAll(ReturnStmt.class).size();
        m.try_catch_count           = method.findAll(TryStmt.class).size();
        m.switch_case_count         = countSwitchCases(method);
        m.loop_body_max_statements  = computeLoopBodyMaxStatements(method);
        m.parameter_count           = method.getParameters().size();

        return m;
    }

    // -------------------------------------------------------------------------
    // CONTEO DE STATEMENTS
    // Cuenta statements "relevantes": no cuenta bloques vacíos ni el propio
    // cuerpo del método, solo los statements con contenido semántico real.
    // -------------------------------------------------------------------------

    private static int countStatements(List<Statement> statements) {
        int count = 0;
        for (Statement s : statements) {
            if (s instanceof BlockStmt)      continue; // solo el bloque contenedor
            if (s instanceof EmptyStmt)      continue; // punto y coma suelto
            count++;
        }
        return count;
    }

    // -------------------------------------------------------------------------
    // CONTEO DE RAMAS
    // Ramas = if + else + case (switch) + catch
    // -------------------------------------------------------------------------

    private static int countBranches(CallableDeclaration<?> method) {
        int count = 0;

        // if / else
        for (IfStmt ifStmt : method.findAll(IfStmt.class)) {
            count++; // rama if
            if (ifStmt.getElseStmt().isPresent()) count++; // rama else
        }

        // switch: cada case con etiqueta cuenta como una rama
        for (SwitchEntry entry : method.findAll(SwitchEntry.class)) {
            if (!entry.getLabels().isEmpty()) count++;
        }

        // catch
        count += method.findAll(CatchClause.class).size();

        return count;
    }

    // -------------------------------------------------------------------------
    // PROFUNDIDAD MÁXIMA DE IFS ANIDADOS
    // Recorre recursivamente el AST midiendo la profundidad de anidamiento
    // de IfStmt dentro de otros IfStmt (ignorando el else-if plano).
    // -------------------------------------------------------------------------

    private static int computeMaxIfNesting(CallableDeclaration<?> method) {
        if (getBody(method).isEmpty()) return 0;
        return maxIfDepth(getBody(method).get(), 0);
    }

    private static int maxIfDepth(Statement stmt, int currentDepth) {
        if (stmt instanceof IfStmt ifStmt) {
            int depth = currentDepth + 1;
            int maxInThen = maxIfDepth(ifStmt.getThenStmt(), depth);

            // El else-if directo NO suma profundidad (es un bloque plano)
            int maxInElse = 0;
            if (ifStmt.getElseStmt().isPresent()) {
                Statement elseStmt = ifStmt.getElseStmt().get();
                if (elseStmt instanceof IfStmt) {
                    // else-if: continúa en el mismo nivel
                    maxInElse = maxIfDepth(elseStmt, currentDepth);
                } else {
                    maxInElse = maxIfDepth(elseStmt, depth);
                }
            }

            return Math.max(depth, Math.max(maxInThen, maxInElse));
        }

        if (stmt instanceof BlockStmt block) {
            int max = currentDepth;
            for (Statement child : block.getStatements()) {
                max = Math.max(max, maxIfDepth(child, currentDepth));
            }
            return max;
        }

        // Para otros statements con cuerpo (for, while, try...) seguimos bajando
        int max = currentDepth;
        for (Statement child : stmt.findAll(Statement.class, s -> s != stmt)) {
            // Solo bajar un nivel directo para no sobrecontar
            if (stmt.getChildNodes().contains(child)) {
                max = Math.max(max, maxIfDepth(child, currentDepth));
            }
        }
        return max;
    }

    // -------------------------------------------------------------------------
    // CADENAS DE IFS ANIDADOS SIN STATEMENTS INTERMEDIOS
    //
    // Hay dos variantes según si el if interior tiene else o no:
    //
    // Sin else → colapsable con &&:
    //   if (a) {
    //       if (b) { st1; }   <-- equivale exactamente a if(a && b)
    //   }
    //
    // Con else → NO colapsable con && (el else pertenece al if interior
    // y cambiaría la semántica). Candidato a Extract Method:
    //   if (a) {
    //       if (b) { st1; }
    //       else   { st2; }   <-- st2 se ejecuta cuando a=true y b=false;
    //   }                         colapsar con && eliminaría ese caso
    //
    // En ambos casos NO se cuenta si hay statements entre el if exterior
    // y el if interior:
    //   if (a) {
    //       x++;              <-- código entre medias: no es cadena directa
    //       if (b) { ... }
    //   }
    //
    // @param countWithElse  true  → cuenta cadenas cuyo if interior tiene else
    //                       false → cuenta cadenas cuyo if interior NO tiene else
    // -------------------------------------------------------------------------

    private static int countNestedIfChains(CallableDeclaration<?> method, boolean countWithElse) {
        if (getBody(method).isEmpty()) return 0;
        int[] count = {0};
        findNestedIfChains(getBody(method).get(), count, countWithElse);
        return count[0];
    }

    private static void findNestedIfChains(Statement stmt, int[] count, boolean countWithElse) {
        if (stmt instanceof IfStmt ifStmt) {
            Statement thenStmt = ifStmt.getThenStmt();
            Statement innerStmt = unwrapBlock(thenStmt);

            if (innerStmt instanceof IfStmt innerIf) {
                // El then contiene SOLO otro if: comprobar si tiene else
                boolean innerHasElse = innerIf.getElseStmt().isPresent();

                if (innerHasElse == countWithElse) {
                    count[0]++;
                }
                // Continuar buscando cadenas dentro del if interior
                findNestedIfChains(innerStmt, count, countWithElse);
            } else {
                // El then no es un if directo: seguir buscando dentro
                findNestedIfChains(thenStmt, count, countWithElse);
            }

            // Buscar también dentro del else
            ifStmt.getElseStmt().ifPresent(e -> findNestedIfChains(e, count, countWithElse));
            return;
        }

        if (stmt instanceof BlockStmt block) {
            for (Statement child : block.getStatements()) {
                findNestedIfChains(child, count, countWithElse);
            }
            return;
        }

        // Otros statements: buscar dentro de sus hijos directos
        for (var child : stmt.getChildNodes()) {
            if (child instanceof Statement s) {
                findNestedIfChains(s, count, countWithElse);
            }
        }
    }

    /**
     * Si el bloque contiene un único statement (ignorando bloques vacíos),
     * devuelve ese statement. Si no, devuelve el propio stmt.
     * Esto permite tratar "if(a) if(b)..." y "if(a) { if(b)... }" igual.
     */
    private static Statement unwrapBlock(Statement stmt) {
        if (stmt instanceof BlockStmt block) {
            List<Statement> nonEmpty = block.getStatements().stream()
                    .filter(s -> !(s instanceof EmptyStmt))
                    .toList();
            if (nonEmpty.size() == 1) {
                return nonEmpty.get(0);
            }
        }
        return stmt;
    }

    // -------------------------------------------------------------------------
    // FOREACH SIMPLES
    // Un foreach es "simple" si su cuerpo solo contiene statements básicos
    // (asignaciones, llamadas a métodos, returns) sin estructuras de control
    // anidadas (ifs, loops, try/catch). Estos son los más fácilmente
    // convertibles a stream/lambda.
    // -------------------------------------------------------------------------

    private static int countSimpleForeachLoops(List<ForEachStmt> loops) {
        int count = 0;
        for (ForEachStmt loop : loops) {
            if (isForeachSimple(loop)) count++;
        }
        return count;
    }

    private static boolean isForeachSimple(ForEachStmt loop) {
        // El cuerpo no debe contener estructuras de control anidadas
        boolean hasNestedControl =
                !loop.findAll(IfStmt.class).isEmpty()        ||
                !loop.findAll(ForStmt.class).isEmpty()       ||
                !loop.findAll(ForEachStmt.class).isEmpty()   ||
                !loop.findAll(WhileStmt.class).isEmpty()     ||
                !loop.findAll(DoStmt.class).isEmpty()        ||
                !loop.findAll(TryStmt.class).isEmpty()       ||
                !loop.findAll(SwitchStmt.class).isEmpty();

        return !hasNestedControl;
    }

    // -------------------------------------------------------------------------
    // FOREACH CANDIDATOS A STREAM/LAMBDA — PATRÓN MAP
    //
    // Un foreach es candidato a map() cuando su cuerpo contiene un único
    // statement: una transformación directa de cada elemento.
    //
    // Ejemplo:
    //   for (User u : users) { result.add(u.getName()); }
    //   → users.stream().map(User::getName).collect(toList())
    // -------------------------------------------------------------------------

    private static int countMapCandidateLoops(List<ForEachStmt> loops) {
        int count = 0;
        for (ForEachStmt loop : loops) {
            if (isMapCandidate(loop)) count++;
        }
        return count;
    }

    private static boolean isMapCandidate(ForEachStmt loop) {
        List<Statement> stmts = getBodyStatements(loop);
        // Cuerpo de exactamente un statement y sin if (eso sería filter+map)
        return stmts.size() == 1 && !(stmts.get(0) instanceof IfStmt);
    }

    // -------------------------------------------------------------------------
    // FOREACH CANDIDATOS A STREAM/LAMBDA — PATRÓN FILTER+MAP
    //
    // Un foreach es candidato a filter().map() cuando su cuerpo contiene
    // un único if simple (sin anidamiento) con una operación dentro.
    //
    // Ejemplo:
    //   for (User u : users) { if (u.isActive()) result.add(u); }
    //   → users.stream().filter(User::isActive).collect(toList())
    // -------------------------------------------------------------------------

    private static int countFilterMapCandidateLoops(List<ForEachStmt> loops) {
        int count = 0;
        for (ForEachStmt loop : loops) {
            if (isFilterMapCandidate(loop)) count++;
        }
        return count;
    }

    private static boolean isFilterMapCandidate(ForEachStmt loop) {
        List<Statement> stmts = getBodyStatements(loop);
        if (stmts.size() != 1) return false;
        if (!(stmts.get(0) instanceof IfStmt ifStmt)) return false;

        // El if no debe tener else (eso complicaría la transformación)
        if (ifStmt.getElseStmt().isPresent()) return false;

        // El cuerpo del if debe ser un statement simple, sin bucles ni ifs anidados
        Statement inner = unwrapBlock(ifStmt.getThenStmt());
        return !(inner instanceof IfStmt)
            && !(inner instanceof ForEachStmt)
            && !(inner instanceof ForStmt)
            && !(inner instanceof WhileStmt);
    }

    // -------------------------------------------------------------------------
    // FOREACH CANDIDATOS A STREAM/LAMBDA — PATRÓN REDUCE
    //
    // Un foreach es candidato a reduce()/collect() cuando acumula un resultado
    // (suma, conteo, concatenación) con a lo sumo un if de guarda, sin bucles
    // anidados que compliquen la transformación.
    //
    // Ejemplo:
    //   for (int n : numbers) { if (n > 0) total += n; }
    //   → numbers.stream().filter(n -> n > 0).reduce(0, Integer::sum)
    // -------------------------------------------------------------------------

    private static int countReduceCandidateLoops(List<ForEachStmt> loops) {
        int count = 0;
        for (ForEachStmt loop : loops) {
            if (isReduceCandidate(loop)) count++;
        }
        return count;
    }

    private static boolean isReduceCandidate(ForEachStmt loop) {
        List<Statement> stmts = getBodyStatements(loop);

        // Ya cubierto por map o filter+map
        if (stmts.size() == 1) return false;

        // Máximo dos statements: un if de guarda y una acumulación
        if (stmts.size() > 2) return false;

        boolean hasIf = stmts.stream().anyMatch(s -> s instanceof IfStmt);
        boolean noNestedLoops =
                loop.findAll(ForEachStmt.class).size() <= 1 &&
                loop.findAll(ForStmt.class).isEmpty()       &&
                loop.findAll(WhileStmt.class).isEmpty();

        return hasIf && noNestedLoops;
    }

    // -------------------------------------------------------------------------
    // HELPER: obtener los statements del cuerpo de un foreach
    // -------------------------------------------------------------------------

    private static List<Statement> getBodyStatements(ForEachStmt loop) {
        Statement body = loop.getBody();
        return body instanceof BlockStmt block
                ? block.getStatements()
                : List.of(body);
    }

    // -------------------------------------------------------------------------
    // CONTEO DE CASE EN SWITCH
    // Cuenta todos los case (con etiqueta) en todos los switch del método.
    // -------------------------------------------------------------------------

    private static int countSwitchCases(CallableDeclaration<?> method) {
        int count = 0;
        for (SwitchStmt sw : method.findAll(SwitchStmt.class)) {
            for (SwitchEntry entry : sw.getEntries()) {
                if (!entry.getLabels().isEmpty()) {
                    count++;
                }
            }
        }
        return count;
    }

    // -------------------------------------------------------------------------
    // MÁXIMO DE STATEMENTS EN CUERPO DE BUCLE
    // Recorre todos los bucles (for, foreach, while, do-while) del método
    // y devuelve el número máximo de statements dentro de un mismo cuerpo.
    // Útil para discriminar bucles trivialmente convertibles a stream (1-2
    // statements) de los que requerirían una reescritura mayor.
    // -------------------------------------------------------------------------

    private static int computeLoopBodyMaxStatements(CallableDeclaration<?> method) {
        int maxStmts = 0;

        List<Statement> loopBodies = new ArrayList<>();

        for (ForEachStmt loop : method.findAll(ForEachStmt.class)) {
            loopBodies.add(loop.getBody());
        }
        for (ForStmt loop : method.findAll(ForStmt.class)) {
            loopBodies.add(loop.getBody());
        }
        for (WhileStmt loop : method.findAll(WhileStmt.class)) {
            loopBodies.add(loop.getBody());
        }
        for (DoStmt loop : method.findAll(DoStmt.class)) {
            loopBodies.add(loop.getBody());
        }

        for (Statement body : loopBodies) {
            List<Statement> stmts = body instanceof BlockStmt block
                    ? block.getStatements()
                    : List.of(body);
            int count = 0;
            for (Statement s : stmts) {
                if (!(s instanceof EmptyStmt)) {
                    count++;
                }
            }
            if (count > maxStmts) {
                maxStmts = count;
            }
        }

        return maxStmts;
    }
}
