"""Tests del cliente LLM OpenAI-compatible (sin red)."""

import json
import unittest
from unittest import mock

from lib import llm_client
from lib.llm_client import LLMClient, LLMError, extract_json_object


class ExtractJsonObjectTests(unittest.TestCase):
    def test_simple_object(self):
        self.assertEqual(extract_json_object('{"a": 1}'), {"a": 1})

    def test_ignores_surrounding_text(self):
        self.assertEqual(extract_json_object('bla bla {"a": {"b": 2}} fin'),
                         {"a": {"b": 2}})

    def test_braces_inside_strings(self):
        text = '{"code": "if (a) { b(); }", "n": 2}'
        self.assertEqual(extract_json_object(text)["n"], 2)

    def test_escaped_quotes(self):
        text = r'{"s": "dice \"hola\" }", "n": 1}'
        self.assertEqual(extract_json_object(text)["s"], 'dice "hola" }')

    def test_no_json_raises(self):
        with self.assertRaises(LLMError):
            extract_json_object("sin llaves")

    def test_unclosed_json_raises(self):
        with self.assertRaises(LLMError):
            extract_json_object('{"a": 1')


class ParseResponseTests(unittest.TestCase):
    def _resp(self, payload):
        resp = mock.Mock()
        if isinstance(payload, Exception):
            resp.json.side_effect = payload
        else:
            resp.json.return_value = payload
        resp.text = "cuerpo"
        return resp

    def test_valid_response(self):
        resp = self._resp({"choices": [{"message": {"content": "hola"}}],
                           "usage": {"total_tokens": 3}})
        content, usage = LLMClient._parse_response(resp)
        self.assertEqual(content, "hola")
        self.assertEqual(usage, {"total_tokens": 3})

    def test_invalid_json_raises_llm_error(self):
        with self.assertRaises(LLMError):
            LLMClient._parse_response(self._resp(ValueError("no json")))

    def test_missing_choices_raises_llm_error(self):
        with self.assertRaises(LLMError):
            LLMClient._parse_response(self._resp({"error": "boom"}))


class ClientTests(unittest.TestCase):
    def test_defaults_and_overrides(self):
        client = LLMClient(base_url="http://x/v1/", api_key="k", model="m",
                           temperature=0.7, max_tokens=10, timeout=5,
                           reasoning_effort="low")
        self.assertEqual(client.base_url, "http://x/v1")
        self.assertEqual(client.api_key, "k")
        self.assertEqual(client.temperature, 0.7)
        self.assertEqual(client.max_tokens, 10)
        self.assertEqual(client.timeout, 5)
        self.assertEqual(client.reasoning_effort, "low")

    def test_chat_payload(self):
        client = LLMClient(base_url="http://x/v1", api_key="k", model="m",
                           temperature=0.1, max_tokens=20, timeout=5,
                           reasoning_effort="high")
        with mock.patch.object(client, "_post",
                               return_value=("ok", {"total_tokens": 1})) as post:
            content, usage = client.chat([{"role": "user", "content": "hola"}],
                                         json_mode=True)
        self.assertEqual(content, "ok")
        payload = post.call_args.args[2]
        self.assertEqual(payload["model"], "m")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["reasoning_effort"], "high")
        self.assertNotIn("stream", payload)

    def test_chat_stream_sets_flag(self):
        client = LLMClient(base_url="http://x/v1", api_key="k", model="m")
        with mock.patch.object(client, "_post", return_value=("ok", None)) as post:
            client.chat([{"role": "user", "content": "x"}], stream=True)
        payload = post.call_args.args[2]
        self.assertTrue(payload["stream"])

    def test_chat_json_parses_object(self):
        client = LLMClient(base_url="http://x/v1", api_key="k", model="m")
        with mock.patch.object(client, "chat", return_value=('{"a": 1}', None)):
            data, _ = client.chat_json([{"role": "user", "content": "x"}])
        self.assertEqual(data, {"a": 1})

    def test_chat_json_falls_back_without_response_format(self):
        client = LLMClient(base_url="http://x/v1", api_key="k", model="m")
        calls = []

        def chat(messages, **kw):
            calls.append(kw.get("json_mode"))
            if kw.get("json_mode"):
                raise LLMError("HTTP 400: response_format no soportado")
            return '{"a": 2}', None

        with mock.patch.object(client, "chat", side_effect=chat):
            data, _ = client.chat_json([{"role": "user", "content": "x"}])
        self.assertEqual(data, {"a": 2})
        self.assertEqual(calls, [True, False])

    def test_chat_json_does_not_swallow_other_errors(self):
        client = LLMClient(base_url="http://x/v1", api_key="k", model="m")
        with mock.patch.object(client, "chat", side_effect=LLMError("HTTP 500")):
            with self.assertRaises(LLMError):
                client.chat_json([{"role": "user", "content": "x"}])


class PostTests(unittest.TestCase):
    def setUp(self):
        self.client = LLMClient(base_url="http://x/v1", api_key="k", model="m",
                                timeout=5)

    def _response(self, status, payload=None, text=""):
        resp = mock.Mock()
        resp.status_code = status
        resp.text = text
        resp.json.return_value = payload or {}
        return resp

    def test_retries_retryable_status(self):
        responses = [self._response(429, text="rate"), self._response(200, {"choices": [
            {"message": {"content": "ok"}}]})]
        with mock.patch.object(llm_client.requests, "post", side_effect=responses), \
             mock.patch.object(llm_client.time, "sleep"):
            content, _ = self.client._post("http://x", {}, {})
        self.assertEqual(content, "ok")

    def test_raises_on_client_error(self):
        with mock.patch.object(llm_client.requests, "post",
                               return_value=self._response(400, text="bad request")), \
             mock.patch.object(llm_client.time, "sleep"):
            with self.assertRaises(LLMError):
                self.client._post("http://x", {}, {})

    def test_raises_on_invalid_200_body(self):
        resp = self._response(200)
        resp.json.side_effect = ValueError("no json")
        with mock.patch.object(llm_client.requests, "post", return_value=resp):
            with self.assertRaises(LLMError):
                self.client._post("http://x", {}, {})

    def test_connection_error_raises_after_retries(self):
        with mock.patch.object(llm_client.requests, "post",
                               side_effect=llm_client.requests.ConnectionError("x")), \
             mock.patch.object(llm_client.time, "sleep"):
            with self.assertRaises(LLMError):
                self.client._post("http://x", {}, {})


class ConsumeSseTests(unittest.TestCase):
    def _resp(self, lines):
        resp = mock.Mock()
        resp.iter_lines.return_value = iter(lines)
        return resp

    def test_accumulates_content_and_usage(self):
        lines = [
            'data: {"choices": [{"delta": {"content": "Ho"}}]}',
            "",
            'data: {"choices": [{"delta": {"content": "la"}}]}',
            'data: {"usage": {"total_tokens": 5}}',
            "data: [DONE]",
        ]
        content, usage = LLMClient._consume_sse(self._resp(lines), 10)
        self.assertEqual(content, "Hola")
        self.assertEqual(usage, {"total_tokens": 5})

    def test_ignores_malformed_chunks(self):
        lines = ['data: {malo}', 'data: {"choices": [{"delta": {"content": "ok"}}]}',
                 "data: [DONE]"]
        content, _ = LLMClient._consume_sse(self._resp(lines), 10)
        self.assertEqual(content, "ok")

    def test_closes_response(self):
        resp = self._resp(["data: [DONE]"])
        LLMClient._consume_sse(resp, 10)
        resp.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
