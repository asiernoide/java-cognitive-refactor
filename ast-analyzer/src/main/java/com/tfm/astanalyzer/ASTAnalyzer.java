package com.tfm.astanalyzer;

import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.CallableDeclaration;
import com.github.javaparser.ast.body.ConstructorDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.TypeDeclaration;
import com.github.javaparser.ast.stmt.*;
import com.github.javaparser.ast.expr.BinaryExpr;
import com.github.javaparser.ast.expr.ConditionalExpr;
import com.github.javaparser.ast.expr.LambdaExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
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
import com.google.gson.JsonObject;

import java.io.FileInputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.stream.Stream;

/**
 * Analizador de AST para métodos Java.
 *
 * Uso:
 *   java -jar ast-analyzer.jar <archivo.java> <numero_linea>
 *   java -jar ast-analyzer.jar <archivo.java> <firma>
 *   java -jar ast-analyzer.jar scan <raiz_proyecto> [umbral]
 *
 * Donde:
 *   - <numero_linea> es un entero: se analiza el método que contiene esa línea.
 *   - <firma> es "nombreMetodo(tipo1,tipo2)" (único dentro de una clase), con
 *     prefijo de clase opcional "Clase.nombreMetodo(tipo1)". Sirve para localizar
 *     métodos tras un refactor, cuando las líneas ya no son válidas.
 *   - "scan <raiz_proyecto> [umbral]" recorre el proyecto (los archivos .java)
 *     y devuelve un JSON array con todos los métodos cuya cognitive_complexity
 *     supera el umbral (por defecto 15). Es la DETECCIÓN local de métodos
 *     complejos, sin necesidad de SonarQube.
 *
 * Salida (stdout):
 *   JSON con métricas estructurales del método encontrado (modos 1 y 2) o
 *   JSON array de métodos complejos (modo scan).
 */
public class ASTAnalyzer {

    public static void main(String[] args) {
        // Modo escaneo de proyecto: detección local de métodos complejos.
        if (args.length >= 2 && args[0].equals("scan")) {
            int threshold = 15;
            if (args.length >= 3) {
                try {
                    threshold = Integer.parseInt(args[2]);
                } catch (NumberFormatException e) {
                    System.err.println("Error: el umbral debe ser un número entero.");
                    System.exit(1);
                    return;
                }
            }
            scanProject(args[1], threshold);
            return;
        }

        if (args.length < 2) {
            System.err.println("Uso:");
            System.err.println("  java -jar ast-analyzer.jar <archivo.java> <linea|firma>");
            System.err.println("  java -jar ast-analyzer.jar scan <raiz_proyecto> [umbral]");
            System.exit(1);
        }

        String filePath = args[0];
        String anchor = args[1];

        try {
            CompilationUnit cu = StaticJavaParser.parse(new FileInputStream(filePath));

            Optional<CallableDeclaration<?>> method = isInteger(anchor)
                    ? findByLine(cu, Integer.parseInt(anchor))
                    : findBySignature(cu, anchor);

            if (method.isEmpty()) {
                System.err.println("No se encontró ningún método para: " + anchor);
                System.exit(2);
                return;
            }

            MethodMetrics metrics = analyzeMethod(method.get());

            Gson gson = new GsonBuilder().setPrettyPrinting().create();
            System.out.println(gson.toJson(metrics));

        } catch (Exception e) {
            System.err.println("Error al parsear el archivo: " + e.getMessage());
            System.exit(3);
        }
    }

    // -------------------------------------------------------------------------
    // ESCANEO DE PROYECTO (DETECCIÓN LOCAL DE MÉTODOS COMPLEJOS)
    // -------------------------------------------------------------------------

    /**
     * Recorre un proyecto, analiza todos sus métodos y devuelve (por stdout) un
     * JSON array con los que superan el umbral de complejidad cognitiva.
     * Reemplaza la detección que antes hacía SonarQube (regla S3776).
     *
     * Las raíces de fuentes se detectan automáticamente por módulo (ver
     * detectSourceRoots): cubre proyectos de un solo módulo, multi-módulo
     * (Maven/Gradle) y layouts Ant (src directa).
     */
    private static void scanProject(String projectRoot, int threshold) {
        Path root = Path.of(projectRoot);
        if (!Files.isDirectory(root)) {
            System.err.println("Error: no es un directorio: " + projectRoot);
            System.exit(1);
            return;
        }

        Gson gson = new Gson();
        List<JsonObject> results = new ArrayList<>();
        Set<Path> files = new LinkedHashSet<>();

        for (Path sourceRoot : detectSourceRoots(root)) {
            if (!Files.isDirectory(sourceRoot)) continue;
            try (Stream<Path> paths = Files.walk(sourceRoot)) {
                paths.filter(Files::isRegularFile)
                        .filter(p -> p.toString().endsWith(".java"))
                        .forEach(files::add);
            } catch (IOException e) {
                System.err.println("[WARN] Error al recorrer " + sourceRoot + ": " + e.getMessage());
            }
        }

        for (Path file : files) {
            scanFile(root, file, threshold, gson, results);
        }

        System.out.println(gson.toJson(results));
    }

    /**
     * Detecta las raíces de fuentes de un proyecto:
     *   - Cada directorio con descriptor de build (pom.xml, build.gradle,
     *     settings.gradle, build.xml) es un módulo.
     *   - Si el módulo tiene src/main/java → raíz de fuentes.
     *   - Si no, y src/ contiene código Java directamente (layout Ant, p.ej. moea) → src/.
     *   - Se excluyen los tests (src/test/java): el dataset original de SonarQube
     *     no incluía rutas de test, y el TFM se centra en código de producción.
     * Devuelve rutas absolutas normalizadas, sin duplicados.
     */
    private static List<Path> detectSourceRoots(Path projectRoot) {
        List<Path> moduleRoots = new ArrayList<>();
        moduleRoots.add(projectRoot);
        try (Stream<Path> paths = Files.walk(projectRoot)) {
            paths.filter(Files::isDirectory)
                    .filter(p -> !p.equals(projectRoot))
                    .filter(p -> !isBuildOrVcsPath(projectRoot, p))
                    .filter(ASTAnalyzer::hasBuildDescriptor)
                    .forEach(moduleRoots::add);
        } catch (IOException e) {
            System.err.println("[WARN] No se pudieron localizar los módulos: " + e.getMessage());
        }

        Set<Path> sourceRoots = new LinkedHashSet<>();
        for (Path m : moduleRoots) {
            Path mainJava = m.resolve("src/main/java");
            if (Files.isDirectory(mainJava)) {
                sourceRoots.add(mainJava.normalize());
            } else {
                Path src = m.resolve("src");
                if (Files.isDirectory(src) && containsJavaFile(src)) {
                    sourceRoots.add(src.normalize());
                }
            }
        }
        return new ArrayList<>(sourceRoots);
    }

    private static boolean hasBuildDescriptor(Path dir) {
        return Files.exists(dir.resolve("pom.xml"))
                || Files.exists(dir.resolve("build.gradle"))
                || Files.exists(dir.resolve("build.gradle.kts"))
                || Files.exists(dir.resolve("settings.gradle"))
                || Files.exists(dir.resolve("settings.gradle.kts"))
                || Files.exists(dir.resolve("build.xml"));
    }

    private static boolean containsJavaFile(Path dir) {
        try (Stream<Path> paths = Files.walk(dir)) {
            return paths.anyMatch(p -> Files.isRegularFile(p) && p.toString().endsWith(".java"));
        } catch (IOException e) {
            return false;
        }
    }

    private static void scanFile(Path root, Path file, int threshold, Gson gson, List<JsonObject> results) {
        try {
            CompilationUnit cu = StaticJavaParser.parse(file.toFile());
            for (CallableDeclaration<?> c : collectCallables(cu)) {
                if (getBody(c).isEmpty()) continue;
                MethodMetrics m = analyzeMethod(c);
                if (m.cognitive_complexity > threshold) {
                    JsonObject obj = new JsonObject();
                    obj.addProperty("file", root.relativize(file).toString().replace('\\', '/'));
                    JsonObject metricsObj = (JsonObject) gson.toJsonTree(m);
                    for (var entry : metricsObj.entrySet()) {
                        obj.add(entry.getKey(), entry.getValue());
                    }
                    results.add(obj);
                }
            }
        } catch (Exception e) {
            System.err.println("[WARN] No se pudo analizar " + file + ": " + e.getMessage());
        }
    }

    /** Excluye directorios de build y control de versiones (target, .git, ...). */
    private static boolean isBuildOrVcsPath(Path root, Path file) {
        Path rel = root.relativize(file);
        for (Path part : rel) {
            String name = part.toString();
            if (name.equals(".git") || name.equals(".idea") || name.equals("target")
                    || name.equals("build") || name.equals("bin") || name.equals("dist")
                    || name.equals("out") || name.equals("node_modules")) {
                return true;
            }
        }
        return false;
    }

    private static boolean isInteger(String s) {
        if (s == null || s.isEmpty()) return false;
        for (int i = 0; i < s.length(); i++) {
            if (!Character.isDigit(s.charAt(i))) return false;
        }
        return true;
    }

    // -------------------------------------------------------------------------
    // LOCALIZACIÓN DEL MÉTODO
    // -------------------------------------------------------------------------

    /** Reúne métodos y constructores en una lista común (interfaz CallableDeclaration). */
    private static List<CallableDeclaration<?>> collectCallables(CompilationUnit cu) {
        List<CallableDeclaration<?>> all = new ArrayList<>();
        all.addAll(cu.findAll(MethodDeclaration.class));
        all.addAll(cu.findAll(ConstructorDeclaration.class));
        return all;
    }

    /**
     * Localiza el método que contiene la línea indicada.
     * Fase 1: búsqueda exacta (si hay callables anidados, el más pequeño).
     * Fase 2: por proximidad — SonarQube a veces reporta la línea de la firma de un
     * callable multilínea que queda fuera del rango exacto.
     */
    private static Optional<CallableDeclaration<?>> findByLine(CompilationUnit cu, int targetLine) {
        List<CallableDeclaration<?>> withRange = collectCallables(cu).stream()
                .filter(m -> m.getRange().isPresent())
                .toList();

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
        return found;
    }

    /**
     * Localiza un método por signatura "nombre(tipos)" o "Clase.nombre(tipos)".
     * El nombre + los tipos de parámetros son únicos dentro de una clase (Java no
     * permite dos métodos con el mismo nombre y tipos de parámetros en la misma
     * clase), por lo que la firma es estable ante refactorizaciones que cambian
     * las líneas.
     */
    private static Optional<CallableDeclaration<?>> findBySignature(CompilationUnit cu, String signatureArg) {
        ParsedSignature parsed = parseSignature(signatureArg);
        List<CallableDeclaration<?>> matches = new ArrayList<>();

        for (CallableDeclaration<?> c : collectCallables(cu)) {
            if (!c.getNameAsString().equals(parsed.methodName)) continue;
            if (!paramsMatch(c, parsed)) continue;
            if (parsed.className != null && !enclosingClassPath(c).endsWith(parsed.className)) continue;
            if (getBody(c).isEmpty()) continue; // solo métodos con cuerpo
            matches.add(c);
        }

        if (matches.size() == 1) {
            return Optional.of(matches.get(0));
        }
        if (matches.size() > 1) {
            System.err.println("Ambiguo: la firma coincide con varios métodos:");
            for (CallableDeclaration<?> m : matches) {
                int line = m.getRange().map(r -> r.begin.line).orElse(-1);
                System.err.println("  " + enclosingClassPath(m) + "." + signature(m) + " @ línea " + line);
            }
            return Optional.empty();
        }
        return Optional.empty();
    }

    /** Signatura "nombre(tipo1,tipo2)" de un callable (sin espacios). */
    private static String signature(CallableDeclaration<?> m) {
        StringBuilder sb = new StringBuilder(m.getNameAsString());
        sb.append('(');
        boolean first = true;
        for (Parameter p : m.getParameters()) {
            if (!first) sb.append(',');
            sb.append(p.getType().asString());
            first = false;
        }
        sb.append(')');
        return sb.toString();
    }

    /** Ruta de la(s) clase(s) contenedora(s), p.ej. "Outer.Inner". */
    private static String enclosingClassPath(CallableDeclaration<?> m) {
        List<String> names = new ArrayList<>();
        Optional<Node> parent = m.getParentNode();
        while (parent.isPresent()) {
            Node p = parent.get();
            if (p instanceof TypeDeclaration<?> td && td.getNameAsString() != null) {
                names.add(td.getNameAsString());
            }
            parent = p.getParentNode();
        }
        java.util.Collections.reverse(names);
        return String.join(".", names);
    }

    private static boolean paramsMatch(CallableDeclaration<?> m, ParsedSignature sig) {
        List<Parameter> params = m.getParameters();
        if (params.size() != sig.paramTypes.size()) return false;
        for (int i = 0; i < params.size(); i++) {
            if (!normalize(params.get(i).getType().asString()).equals(normalize(sig.paramTypes.get(i)))) {
                return false;
            }
        }
        return true;
    }

    /** Elimina todo el espacio en blanco para comparar tipos de forma robusta. */
    private static String normalize(String s) {
        return s.replaceAll("\\s+", "");
    }

    /** Signatura parseada: prefijo de clase opcional + nombre + tipos de parámetros. */
    private record ParsedSignature(String className, String methodName, List<String> paramTypes) {}

    private static ParsedSignature parseSignature(String sig) {
        int open = sig.indexOf('(');
        if (open < 0 || !sig.endsWith(")")) {
            throw new IllegalArgumentException("Firma inválida: " + sig);
        }
        String head = sig.substring(0, open);
        String paramsStr = sig.substring(open + 1, sig.length() - 1);

        String methodName = head;
        String className = null;
        int lastDot = head.lastIndexOf('.');
        if (lastDot >= 0) {
            className = head.substring(0, lastDot);
            methodName = head.substring(lastDot + 1);
        }
        return new ParsedSignature(className, methodName, splitParams(paramsStr));
    }

    /**
     * Divide los tipos de parámetros por comas respetando la profundidad de los
     * genéricos (&lt; &gt;) y de los paréntesis (p.ej. Function&lt;String, Integer&gt;
     * o tipos funcionales), para no romper las comas internas.
     */
    private static List<String> splitParams(String paramsStr) {
        List<String> parts = new ArrayList<>();
        int angle = 0;
        int paren = 0;
        StringBuilder current = new StringBuilder();
        for (char c : paramsStr.toCharArray()) {
            if (c == '<') angle++;
            else if (c == '>') angle = Math.max(0, angle - 1);
            else if (c == '(') paren++;
            else if (c == ')') paren = Math.max(0, paren - 1);

            if (c == ',' && angle == 0 && paren == 0) {
                parts.add(current.toString().trim());
                current.setLength(0);
            } else {
                current.append(c);
            }
        }
        if (current.length() > 0) {
            parts.add(current.toString().trim());
        }
        return parts;
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
        m.method_signature  = signature(method);
        m.enclosing_class   = enclosingClassPath(method);

        // Complejidad cognitiva (replica local del algoritmo de SonarQube S3776)
        m.cognitive_complexity = CognitiveComplexityVisitor.computeComplexity(method);

        // Complejidad ciclomática (McCabe) y proxy de trabajo/allocaciones
        m.cyclomatic_complexity = cyclomaticComplexity(method);
        m.method_invocations = method.findAll(MethodCallExpr.class).size();

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
    // COMPLEJIDAD CICLOMÁTICA (McCABE)
    // V(G) = 1 + puntos de decisión. Puntos de decisión:
    //   if, while, do, for, foreach, case (sin default), catch, ternario (?:)
    //   y cada operador lógico && / || (cada operador cuenta 1, sin colapsar).
    // -------------------------------------------------------------------------

    private static int cyclomaticComplexity(CallableDeclaration<?> method) {
        int cyclo = 1;
        cyclo += method.findAll(IfStmt.class).size();
        cyclo += method.findAll(WhileStmt.class).size();
        cyclo += method.findAll(DoStmt.class).size();
        cyclo += method.findAll(ForStmt.class).size();
        cyclo += method.findAll(ForEachStmt.class).size();
        cyclo += countSwitchCases(method);
        cyclo += method.findAll(CatchClause.class).size();
        cyclo += method.findAll(ConditionalExpr.class).size();
        for (BinaryExpr b : method.findAll(BinaryExpr.class)) {
            if (b.getOperator() == BinaryExpr.Operator.AND
                    || b.getOperator() == BinaryExpr.Operator.OR) {
                cyclo++;
            }
        }
        return cyclo;
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
