package com.example;

import java.util.ArrayList;
import java.util.List;

/** Fixture para el bucle de refactorizacion: un foreach candidato a map. */
public class Loop {

    public List<String> trimAll(List<String> input) {
        List<String> result = new ArrayList<>();
        for (String value : input) {
            result.add(value.trim());
        }
        return result;
    }
}
