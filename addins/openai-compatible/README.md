# OpenAI-compatible AI provider for Kosha

Licence: Apache-2.0. Answers Kosha's optional AI assistance (classification suggestions, change-log
drafts, **Ask Kosha**) through any server that offers the widely used OpenAI-compatible HTTP API:
`/chat/completions`, and optionally `/models` and `/embeddings`. Self-hosted runners on your own
network (for example Ollama, the llama.cpp server or vLLM) offer it, and so do many hosted services.

Kosha works fully without AI. Installing this add-in sends nothing anywhere: Kosha uses it only after an
administrator chooses it under **Admin › AI** and switches features on. See [`docs/ai.md`](../../docs/ai.md)
for what is sent and how to keep everything on your own network.

## Set up

1. Run a model on a server you trust. For a fully local set-up, use a runner on a machine in your own
   network and load a model there. Note its address (it usually ends in `/v1`) and the model name it
   lists.
2. In Kosha, open **Admin › Add-ins › OpenAI-compatible AI provider › Settings**:
   - **Address of the AI server**: `http://localhost:11434/v1` by default (a runner on the Kosha server
     itself). Change it to your server, for example `http://ai-server.local:11434/v1`.
   - **Model name**: exactly as the server lists it.
   - **API key**: only if the server needs one. Stored masked, never shown again, never in error messages.
   - **Embedding model**: optional; leave empty if the server offers none. Kosha works without.
   - **Ask the server for JSON answers**: on by default. An older server that refuses this switch is
     detected and asked without it.
   Press **Test**: Kosha checks that the server answers and offers the model.
3. Open **Admin › AI**, choose this provider, decide what may be sent, switch on the features you want and
   press **Test the provider**.

## How it behaves

- Sends only the messages Kosha builds, only to the address in the settings. Never logs them or the answers.
- One time-out per request (60 seconds by default); one quick repeat for a dropped connection or a server
  error. Kosha's add-in guard has its own time-out and switches the add-in off after repeated crashes.
- Plain messages for the usual problems: the server cannot be reached, a refused API key (401/403), an
  unknown model or wrong address (404), a busy server or usage limit (429), a server error (5xx).
- Over plain HTTP to another machine the health check reminds you that requests travel unencrypted.

## Tests

```bash
pytest -q addins/openai-compatible/tests
```

`tests/fake_chat.py` stands in for the server inside the test process (no network); the tests run the SDK
conformance kit (`kosha_sdk.testing.check_ai_provider`) against it.
