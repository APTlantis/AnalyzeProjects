import requests, json

def ask_model_json(model, prompt, host, api_key=None, prefer="auto"):
    """
    Send a JSON-structured summarization prompt.

    Tries Ollama first (POST {host}/api/generate with format=json).
    If the endpoint is not found (HTTP 404), falls back to an
    OpenAI-compatible endpoint (POST {host}/v1/chat/completions with
    response_format=json_object).
    """
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Normalize preference
    prefer = (prefer or "auto").lower()

    # Helper lambdas for endpoints
    def _call_ollama():
        r = requests.post(
            f"{host}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "format": "json",
                "stream": False,
            },
            headers=headers,
            timeout=180,
        )
        if r.status_code == 404:
            raise _OllamaNotFound("Ollama endpoint '/api/generate' returned 404")
        r.raise_for_status()
        data = r.json().get("response", "")
        js = _extract_first_json(data)
        return json.loads(js)

    def _call_openai():
        r2 = requests.post(
            f"{host}/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a precise assistant. Return only valid JSON that matches the user's requested schema."},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
            headers={**headers, "Content-Type": "application/json"},
            timeout=180,
        )
        r2.raise_for_status()
        content = (
            r2.json()
            .get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        js = _extract_first_json(content)
        return json.loads(js)

    # Decision logic based on preference
    if prefer == "ollama":
        return _call_ollama()
    if prefer == "openai":
        return _call_openai()

    # prefer == "auto" (default): try Ollama then fallback to OpenAI-compatible
    try:
        return _call_ollama()
    except _OllamaNotFound:
        pass
    except requests.exceptions.RequestException:
        # Connection or other request error – attempt fallback
        pass

    return _call_openai()

def _extract_first_json(text):
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    raise ValueError("No JSON is found in the model output.")


class _OllamaNotFound(Exception):
    pass
