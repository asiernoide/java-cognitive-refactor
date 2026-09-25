package com.example;

import java.util.List;

/** Fixture con constructos conocidos para validar las metricas del AST analyzer. */
public class Complex {

    public int simpleIf(int a) {
        if (a > 0) {
            return 1;
        }
        return 0;
    }

    public int ifElse(int a) {
        if (a > 0) {
            return 1;
        } else {
            return 0;
        }
    }

    public int elseIfChain(int a) {
        if (a > 0) {
            return 1;
        } else if (a < 0) {
            return -1;
        }
        return 0;
    }

    public int loopWithIf(int[] values) {
        int total = 0;
        for (int v : values) {
            if (v > 0) {
                total += v;
            }
        }
        return total;
    }

    public boolean logicalAnd(boolean a, boolean b, boolean c) {
        return a && b && c;
    }

    public int ternary(int a) {
        return a > 0 ? a : -a;
    }

    public int plain(int a) {
        return a + 1;
    }

    public String lambdaNesting(List<String> items) {
        StringBuilder sb = new StringBuilder();
        items.forEach(item -> {
            if (item != null) {
                sb.append(item);
            }
        });
        return sb.toString();
    }

    public int switchCases(int a) {
        switch (a) {
            case 1:
                return 1;
            case 2:
                return 2;
            default:
                return 0;
        }
    }

    public int tryCatch(String s) {
        try {
            return Integer.parseInt(s);
        } catch (NumberFormatException e) {
            return -1;
        }
    }

    public boolean equals(Object o) {
        if (o == this) {
            return true;
        }
        if (o == null) {
            return false;
        }
        return o instanceof Complex;
    }

    public int hashCode() {
        if (true) {
            return 1;
        }
        return 0;
    }

    public void collapseCandidate(boolean a, boolean b) {
        if (a) {
            if (b) {
                System.out.println("x");
            }
        }
    }

    public void mapCandidate(List<String> in, List<String> out) {
        for (String s : in) {
            out.add(s);
        }
    }

    public void filterMapCandidate(List<String> in, List<String> out) {
        for (String s : in) {
            if (s != null) {
                out.add(s);
            }
        }
    }

    public int reduceCandidate(int[] ns) {
        int total = 0;
        for (int n : ns) {
            int v = n * 2;
            if (v > 0) {
                total += v;
            }
        }
        return total;
    }

    public Runnable anonymousClass() {
        return new Runnable() {
            @Override
            public void run() {
                if (true) {
                    System.out.println("x");
                }
            }
        };
    }

    /** CC = 20 (> umbral 15): usado por el modo scan. */
    public int veryComplex(int a, int b, int c, int d, int e) {
        int r = 0;
        if (a > 0) {
            r += 1;
        }
        if (b > 0) {
            r += 1;
        }
        if (c > 0) {
            r += 1;
        }
        if (d > 0) {
            r += 1;
        }
        if (e > 0) {
            r += 1;
        }
        if (a > 0 && b > 0) {
            r += 2;
        }
        if (c > 0 || d > 0) {
            r += 2;
        }
        for (int i = 0; i < a; i++) {
            if (i > b) {
                r += 1;
            }
        }
        while (r > 100) {
            r -= 10;
        }
        if (a > 1) {
            if (b > 1) {
                if (c > 1) {
                    r += 3;
                } else {
                    r -= 3;
                }
            }
        }
        return r;
    }
}
