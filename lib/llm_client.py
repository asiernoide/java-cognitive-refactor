"""
Cliente OpenAI-compatible para el LLM (refactorización guiada).

Contrato único: POST {base}/chat/completions, de modo que funciona con
OpenAI, OpenRouter, Ollama, LM Studio, vLLM, etc. sin lógica específica
de modelo. Toda la configuración se lee de variables de entorno (.env):

    LLM_BASE_URL    URL base de la API (default https://api.openai.com/v1)
    LLM_API_KEY     Clave de API (cabecera Bearer)
    LLM_MODEL       Nombre del modelo (default gpt-4o-mini)
    LLM_TEMPERATURE Temperatura (default 0.2, determinismo para refactor)
    LLM_MAX_TOKENS  Máximo de tokens por respuesta (default 2048)
    LLM_TIMEOUT     Timeout de la petición en segundos (default 120)
    LLM_REASONING_EFFORT  Esfuerzo de razonamiento para modelos con thinking
                    (p.ej. DeepSeek V4: low/high/max; default: no se envía)

Uso:
    from lib import llm_client
    client = llm_client.LLMClient()
    text, usage = client.chat([{"role": "user", "content": "..."}])
    data, usage = client.chat_json([...])   # devuelve dict parseado

Probe (para comprobar la API key / proveedor):
    python lib/llm_client.py
"""

import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 2048
DEFAULT_TIMEOUT = 120
MAX_RETRIES = 2

_RETRY_STATUS = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """Error de la llamada al LLM (HTTP, conexión o formato)."""


class LLMClient:
    def __init__(self, base_url=None, api_key=None, model=None, temperature=None,
                 max_tokens=None, timeout=None, reasoning_effort=None):
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("LLM_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_MODEL
        self.temperature = float(
            temperature if temperature is not None
            else os.getenv("LLM_TEMPERATURE", DEFAULT_TEMPERATURE)
        )
        self.max_tokens = int(
            max_tokens if max_tokens is not None
            else os.getenv("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS)
        )
        self.timeout = int(
            timeout if timeout is not None
            else os.getenv("LLM_TIMEOUT", DEFAULT_TIMEOUT)
        )
        self.reasoning_effort = (
            reasoning_effort if reasoning_effort is not None
            else os.getenv("LLM_REASONING_EFFORT") or None
        )

    def chat(self, messages, json_mode=False, temperature=None, max_tokens=None,
             reasoning_effort=None):
        """Envía un chat y devuelve (contenido, usage). usage es dict o None."""
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        effort = reasoning_effort if reasoning_effort is not None else self.reasoning_effort
        if effort:
            payload["reasoning_effort"] = effort
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "opencode-tfm-refactor/0.1",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        return self._post(url, headers, payload)

    def chat_json(self, messages, temperature=None, max_tokens=None):
        """Devuelve (dict_parseado, usage). Pide JSON; si el proveedor no
        soporta response_format, reintenta sin él y extrae el primer objeto JSON."""
        try:
            content, usage = self.chat(messages, json_mode=True,
                                       temperature=temperature, max_tokens=max_tokens)
        except LLMError as e:
            if "response_format" not in str(e):
                raise
            content, usage = self.chat(messages, json_mode=False,
                                       temperature=temperature, max_tokens=max_tokens)
        return extract_json_object(content), usage

    def _post(self, url, headers, payload):
        last_err = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            except requests.RequestException as e:
                last_err = LLMError(f"Error de conexión: {e}")
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return content, data.get("usage")

            body = resp.text[:400]
            if resp.status_code in _RETRY_STATUS:
                last_err = LLMError(f"HTTP {resp.status_code}: {body}")
                time.sleep(2 ** attempt)
                continue

            raise LLMError(f"HTTP {resp.status_code}: {body}")

        raise last_err if last_err else LLMError("Error desconocido al llamar al LLM")


def extract_json_object(text):
    """Extrae y parsea el primer objeto JSON {...} del texto (fallback robusto
    cuando el proveedor no soporta response_format y añade texto extra)."""
    start = text.find("{")
    if start == -1:
        raise LLMError("No se encontró un objeto JSON en la respuesta: " + text[:200])
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError as e:
                    raise LLMError(f"JSON inválido en la respuesta: {e}") from e
    raise LLMError("JSON sin cerrar en la respuesta del LLM")


if __name__ == "__main__":
    print("Probando el cliente LLM (OpenAI-compatible)...")
    client = LLMClient()
    if not client.api_key:
        print("AVISO: LLM_API_KEY no está definida en .env")
    print(f"  base_url : {client.base_url}")
    print(f"  model    : {client.model}")
    text, usage = client.chat([{"role": "user", "content": "Responde solo con: OK"}])
    print("Respuesta:", text.strip())
    print("Usage:", usage)