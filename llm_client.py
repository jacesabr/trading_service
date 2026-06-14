"""
llm_client.py — Provider-agnostic LLM access for the analyst and live assist.

Providers:
  anthropic : Claude (deep daily analysis). Set ANTHROPIC_API_KEY.
              Default model: claude-fable-5
  nvidia    : NVIDIA NIM, OpenAI-compatible (cheap/live assist).
              Set NVIDIA_API_KEY. Default model: meta/llama-3.3-70b-instruct
              (override with NIM_MODEL). NIM free tier is rate-limited —
              fine for on-demand questions, don't poll it.

Usage:
  from llm_client import ask
  text = ask("...prompt...", provider="anthropic")   # or "nvidia"
"""
import json
import os
import urllib.request

DEFAULTS = {"anthropic": "claude-fable-5",
            "nvidia": os.environ.get("NIM_MODEL", "meta/llama-3.3-70b-instruct")}


def _post(url, headers, payload, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          **headers})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def ask(prompt, provider="anthropic", system=None, max_tokens=2500,
        model=None):
    model = model or DEFAULTS[provider]
    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        payload = {"model": model, "max_tokens": max_tokens,
                   "messages": [{"role": "user", "content": prompt}]}
        if system:
            payload["system"] = system
        d = _post("https://api.anthropic.com/v1/messages",
                  {"x-api-key": key, "anthropic-version": "2023-06-01"},
                  payload)
        return "".join(b.get("text", "") for b in d["content"])
    if provider == "nvidia":
        key = os.environ.get("NVIDIA_API_KEY")
        if not key:
            raise RuntimeError("NVIDIA_API_KEY not set")
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
        d = _post("https://integrate.api.nvidia.com/v1/chat/completions",
                  {"Authorization": f"Bearer {key}"},
                  {"model": model, "messages": msgs,
                   "max_tokens": max_tokens, "temperature": 0.3})
        return d["choices"][0]["message"]["content"]
    raise ValueError(provider)
