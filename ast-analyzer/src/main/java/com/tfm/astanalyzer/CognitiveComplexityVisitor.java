package com.tfm.astanalyzer;

import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.AnnotationDeclaration;
import com.github.javaparser.ast.body.CallableDeclaration;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.ConstructorDeclaration;
import com.github.javaparser.ast.body.EnumDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.RecordDeclaration;
import com.github.javaparser.ast.expr.BinaryExpr;
import com.github.javaparser.ast.expr.ConditionalExpr;
import com.github.javaparser.ast.expr.EnclosedExpr;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.LambdaExpr;
import com.github.javaparser.ast.expr.ObjectCreationExpr;
import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.stmt.BreakStmt;
import com.github.javaparser.ast.stmt.ContinueStmt;
import com.github.javaparser.ast.stmt.DoStmt;
import com.github.javaparser.ast.stmt.ForEachStmt;
import com.github.javaparser.ast.stmt.ForStmt;
import com.github.javaparser.ast.stmt.IfStmt;
import com.github.javaparser.ast.stmt.SwitchStmt;
import com.github.javaparser.ast.stmt.TryStmt;
import com.github.javaparser.ast.stmt.WhileStmt;
import com.github.javaparser.ast.visitor.VoidVisitorAdapter;

import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Optional;
import java.util.Set;

/**
 * Cálculo local de la Cognitive Complexity de un método Java replicando 1:1
 * la implementación de SonarSource (sonar-java):
 *
 *   org.sonar.java.ast.visitors.CognitiveComplexityVisitor
 *
 * La referencia del algoritmo es el white paper de G. Ann Campbell:
 *   https://www.sonarsource.com/docs/CognitiveComplexity.pdf
 *
 * El objetivo es que los valores calculados localmente coincidan con los que
 * reporta SonarQube (regla java:S3776), para poder iterar el refactor sin
 * tener que re-escanear en SonarQube en cada iteración.
 *
 * NOTA IMPORTANTE sobre el set `ignored`: JavaParser sobreescribe Node.equals()
 * y Node.hashCode() con comparación ESTRUCTURAL, de modo que dos subtrees
 * sintácticamente idénticos (p.ej. dos condiciones `x == null || x.isEmpty()`
 * en ifs distintos) se consideran iguales. Por eso `ignored` usa SEMÁNTICA DE
 * IDENTIDAD (IdentityHashMap), no estructural: solo queremos marcar los nodos
 * concretos ya aplanados de una cadena lógica.
 *
 * Comportamiento replicado (detalle, ver nota Semana 8):
 *   - if / else if / else   → if y else-if: +nesting; else plano: +1 y anida su cuerpo
 *   - ternario              → +nesting, anida las 3 ramas
 *   - for / foreach / while / do → +nesting, anida el cuerpo
 *   - switch                → +nesting, anida las entries (los case NO suman)
 *   - catch                 → +nesting cada catch, anida sus bloques; el try no suma
 *   - && / ||               → +1, colapsando operadores consecutivos del mismo tipo
 *   - break/continue con etiqueta → +1 (un break normal NO suma)
 *   - lambda                → solo anida (+1 nesting)
 *   - clase interna/anónima/enum/record → solo anida (+1 nesting)
 */
public class CognitiveComplexityVisitor extends VoidVisitorAdapter<Void> {

    private int complexity;
    private int nesting;
    private boolean ignoreNesting;
    private final Set<Node> ignored;

    private CognitiveComplexityVisitor() {
        this.complexity = 0;
        this.nesting = 1; // el cuerpo del método está al nivel de anidamiento 1
        this.ignoreNesting = false;
        this.ignored = Collections.newSetFromMap(new IdentityHashMap<>());
    }

    /**
     * Complejidad cognitiva de un método o constructor.
     * Equivalente a methodComplexity() de sonar-java.
     */
    public static int computeComplexity(CallableDeclaration<?> callable) {
        Optional<BlockStmt> body = getBody(callable);
        if (body.isEmpty()) {
            return 0;
        }
        CognitiveComplexityVisitor visitor = new CognitiveComplexityVisitor();
        body.get().accept(visitor, null);
        return visitor.complexity;
    }

    /**
     * Extrae el cuerpo del método/constructor (CallableDeclaration no expone
     * getBody() de forma uniforme en la API de JavaParser).
     */
    private static Optional<BlockStmt> getBody(CallableDeclaration<?> callable) {
        if (callable instanceof MethodDeclaration m)      return m.getBody();
        if (callable instanceof ConstructorDeclaration c) return Optional.of(c.getBody());
        return Optional.empty();
    }

    // ---------------------------------------------------------------------
    // INCREMENTOS
    // ---------------------------------------------------------------------

    /** +nesting (incremento estructural + 1 por cada nivel de anidamiento). */
    private void increaseComplexityByNesting() {
        complexity += nesting;
        ignoreNesting = false;
    }

    /** +1 fijo (independiente del anidamiento). */
    private void increaseComplexityByOne() {
        complexity += 1;
        ignoreNesting = false;
    }

    // ---------------------------------------------------------------------
    // IF / ELSE IF / ELSE
    // ---------------------------------------------------------------------

    @Override
    public void visit(IfStmt n, Void arg) {
        increaseComplexityByNesting();
        n.getCondition().accept(this, arg);
        nesting++;
        n.getThenStmt().accept(this, arg);
        nesting--;
        boolean elseStatementNotIF = n.getElseStmt().isPresent()
                && !(n.getElseStmt().get() instanceof IfStmt);
        if (elseStatementNotIF) {
            increaseComplexityByOne();
            nesting++;
        } else if (n.getElseStmt().isPresent()) {
            // else-if: no añade anidamiento extra; el else-if suma +1 neto.
            ignoreNesting = true;
            complexity -= nesting - 1;
        }
        n.getElseStmt().ifPresent(e -> e.accept(this, arg));
        if (elseStatementNotIF) {
            nesting--;
        }
    }

    // ---------------------------------------------------------------------
    // TRY / CATCH
    // ---------------------------------------------------------------------

    @Override
    public void visit(TryStmt n, Void arg) {
        n.getResources().forEach(r -> r.accept(this, arg));
        n.getTryBlock().accept(this, arg);
        n.getCatchClauses().forEach(c -> increaseComplexityByNesting());
        nesting++;
        n.getCatchClauses().forEach(c -> c.accept(this, arg));
        nesting--;
        n.getFinallyBlock().ifPresent(f -> f.accept(this, arg));
    }

    // ---------------------------------------------------------------------
    // BUCLES
    // ---------------------------------------------------------------------

    @Override
    public void visit(ForStmt n, Void arg) {
        increaseComplexityByNesting();
        nesting++;
        super.visit(n, arg);
        nesting--;
    }

    @Override
    public void visit(ForEachStmt n, Void arg) {
        increaseComplexityByNesting();
        nesting++;
        super.visit(n, arg);
        nesting--;
    }

    @Override
    public void visit(WhileStmt n, Void arg) {
        increaseComplexityByNesting();
        nesting++;
        super.visit(n, arg);
        nesting--;
    }

    @Override
    public void visit(DoStmt n, Void arg) {
        increaseComplexityByNesting();
        nesting++;
        super.visit(n, arg);
        nesting--;
    }

    // ---------------------------------------------------------------------
    // TERNARIO
    // ---------------------------------------------------------------------

    @Override
    public void visit(ConditionalExpr n, Void arg) {
        increaseComplexityByNesting();
        nesting++;
        n.getCondition().accept(this, arg);
        n.getThenExpr().accept(this, arg);
        n.getElseExpr().accept(this, arg);
        nesting--;
    }

    // ---------------------------------------------------------------------
    // SWITCH (los case labels NO suman en sonar-java actual)
    // ---------------------------------------------------------------------

    @Override
    public void visit(SwitchStmt n, Void arg) {
        increaseComplexityByNesting();
        nesting++;
        super.visit(n, arg);
        nesting--;
    }

    // ---------------------------------------------------------------------
    // BREAK / CONTINUE (solo con etiqueta)
    // ---------------------------------------------------------------------

    @Override
    public void visit(BreakStmt n, Void arg) {
        if (n.getLabel().isPresent()) {
            increaseComplexityByOne();
        }
        super.visit(n, arg);
    }

    @Override
    public void visit(ContinueStmt n, Void arg) {
        if (n.getLabel().isPresent()) {
            increaseComplexityByOne();
        }
        super.visit(n, arg);
    }

    // ---------------------------------------------------------------------
    // LAMBDA → solo anida
    // ---------------------------------------------------------------------

    @Override
    public void visit(LambdaExpr n, Void arg) {
        nesting++;
        super.visit(n, arg);
        nesting--;
    }

    // ---------------------------------------------------------------------
    // CLASES INTERNAS / ANÓNIMAS / ENUM / RECORD → solo anidan
    // ---------------------------------------------------------------------

    @Override
    public void visit(ClassOrInterfaceDeclaration n, Void arg) {
        nesting++;
        super.visit(n, arg);
        nesting--;
    }

    @Override
    public void visit(EnumDeclaration n, Void arg) {
        nesting++;
        super.visit(n, arg);
        nesting--;
    }

    @Override
    public void visit(RecordDeclaration n, Void arg) {
        nesting++;
        super.visit(n, arg);
        nesting--;
    }

    @Override
    public void visit(AnnotationDeclaration n, Void arg) {
        nesting++;
        super.visit(n, arg);
        nesting--;
    }

    @Override
    public void visit(ObjectCreationExpr n, Void arg) {
        // Clase anónima: su cuerpo es un "execution context" adicional → anida.
        boolean hasAnonymousBody = n.getAnonymousClassBody().isPresent();
        if (hasAnonymousBody) {
            nesting++;
        }
        super.visit(n, arg);
        if (hasAnonymousBody) {
            nesting--;
        }
    }

    // ---------------------------------------------------------------------
    // && / || — cada operador +1, colapsando operadores consecutivos iguales
    // ---------------------------------------------------------------------

    @Override
    public void visit(BinaryExpr n, Void arg) {
        if (isLogical(n) && !ignored.contains(n)) {
            List<BinaryExpr> flattened = flattenLogicalExpression(n);
            BinaryExpr previous = null;
            for (BinaryExpr current : flattened) {
                if (previous == null || previous.getOperator() != current.getOperator()) {
                    increaseComplexityByOne();
                }
                previous = current;
            }
        }
        super.visit(n, arg);
    }

    private static boolean isLogical(BinaryExpr expr) {
        return expr.getOperator() == BinaryExpr.Operator.AND
                || expr.getOperator() == BinaryExpr.Operator.OR;
    }

    private List<BinaryExpr> flattenLogicalExpression(Expression expression) {
        List<BinaryExpr> result = new ArrayList<>();
        flattenLogicalExpression(expression, result);
        return result;
    }

    private void flattenLogicalExpression(Expression expression, List<BinaryExpr> result) {
        if (isLogical(expression)) {
            BinaryExpr binaryExpr = (BinaryExpr) expression;
            ignored.add(binaryExpr);
            Expression left = skipParentheses(binaryExpr.getLeft());
            Expression right = skipParentheses(binaryExpr.getRight());
            flattenLogicalExpression(left, result);
            result.add(binaryExpr);
            flattenLogicalExpression(right, result);
        }
    }

    private static boolean isLogical(Expression expression) {
        return expression instanceof BinaryExpr b && isLogical(b);
    }

    private static Expression skipParentheses(Expression expression) {
        Expression current = expression;
        while (current instanceof EnclosedExpr enclosed) {
            current = enclosed.getInner();
        }
        return current;
    }
}
