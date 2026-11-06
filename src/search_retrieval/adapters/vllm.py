"""OpenAI-compatible vLLM stream and local chat-template token counter."""

from collections.abc import Generator
import json
from time import monotonic
from urllib.error import URLError
from urllib.request import Request, urlopen

from search_retrieval.query.delivery import DeliveryRejected


class ChatTemplate:
    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def count(self, messages: list[dict[str, str]]) -> int:
        return len(self.tokenizer.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True, return_dict=False
        ))


class VllmBackend:
    def __init__(self, url: str, model: str, *, api_key: str | None = None):
        self.url = url.rstrip("/")
        self.model = model
        self.api_key = api_key

    def stream(
        self, messages: list[dict[str, str]], max_tokens: int, deadline: float
    ) -> Generator[str, None, None]:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise DeliveryRejected("Generation deadline expired")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            self.url + "/v1/chat/completions",
            json.dumps({
                "model": self.model, "messages": messages, "max_tokens": max_tokens,
                "temperature": 0, "stream": True,
            }).encode(), headers, method="POST",
        )
        finish = None
        try:
            with urlopen(request, timeout=remaining) as response:
                for raw in response:
                    if monotonic() >= deadline:
                        raise DeliveryRejected("Generation deadline expired")
                    if not raw.startswith(b"data:"):
                        continue
                    data = raw[5:].strip()
                    if data == b"[DONE]":
                        if finish == "stop":
                            return
                        raise DeliveryRejected("vLLM did not finish a complete answer")
                    choices = json.loads(data)["choices"]
                    if choices:
                        item = choices[0]
                        finish = item.get("finish_reason") or finish
                        token = item["delta"].get("content")
                        if token:
                            if not isinstance(token, str):
                                raise DeliveryRejected("vLLM returned an invalid token")
                            yield token
        except (URLError, OSError, ValueError, KeyError, TypeError) as error:
            raise DeliveryRejected("vLLM stream failed") from error
        raise DeliveryRejected("vLLM stream ended without its terminal event")
