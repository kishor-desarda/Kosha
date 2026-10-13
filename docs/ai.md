# AI assistance (optional)

Kosha works fully without AI. Every AI feature is **off** until an administrator chooses an AI provider
and switches the feature on under **Admin › AI**. When it is off, no screen shows anything extra and
nothing is sent anywhere.

When it is on, the AI assistant makes **suggestions only**. A person reviews each suggestion, may edit
it, and accepts it with a click; nothing is saved before that, and every accepted suggestion is written
to the audit log (`ai_accept`) together with the change it made.

## What it does

| Feature | Where | What the person gets |
| --- | --- | --- |
| **Classification** | Part page and file page: **Suggest with AI** | A category from the installed packs, a material (from the packs' list or the materials already used), a matching standard part from the standard-parts library, and a cleaner description. Each line can be ticked, edited or left out; **Accept ticked** applies only those lines, with the same rules as the Save button (a released part's description and material only by an approver). |
| **Change-log drafts** | Change order and file page (**Change log** tab, also for a new revision): **Draft with AI** next to **Build draft** | A draft of the four change-log sections written from the changes Kosha measured, where the parts are used, stock and the engineer's note. It fills the editor; the engineer edits it and presses **Save change log**. The rule-based **Build draft** stays available and needs no AI. |
| **Ask Kosha** | **Ask Kosha** next to the search box, and on the search results page | An answer to a question, written from Kosha's own records (parts, files, change orders, inputs) that the asking person may open, with links to the records cited. When the records do not answer the question it says "I don't know" and shows the records that came closest. Answers are private to the person who asked. |
| **Drawing check** | File page of a drawing: **Check drawing**, then **Ask AI for more** | Kosha's own checks always run, with AI off too: title block filled, part number and revision matching the file, description, material (matching the part and in the packs' materials list), drawn by, scale, a general tolerance note such as ISO 2768, and dimensions (texts read from DXF drawings). With the feature on, the AI assistant adds what a checker would still question, each with what it is based on. |
| **Duplicate explanation** | **Duplicates**, on a group of look-alike parts: **Why alike?** | Kosha's own line on what is the same and what differs (size, holes, material, description, number), then one AI sentence saying why they look alike and whether one can probably replace the other. |
| **Title-block reading (OCR)** | File page of a scanned drawing (PDF or photo): **Read title block** | Part number, description, material, revision, drawn by and scale read from the bottom-right corner of the first page. Only that corner is sent, never the whole sheet. Description and material can be accepted like a classification; a part number or revision that disagrees with the file is pointed out. When no AI provider answers and Tesseract is installed on the server, Kosha reads it there. |
| **Voice** | **Ask Kosha**: **Speak** | The spoken question (English, Hindi or a mix) as text in the question box, to check and send. The recording is not kept. |

Kosha checks every answer before it shows it: a category or standard part that is not in Kosha's lists
is dropped (and the screen says so), links point only to records that were actually sent, and an answer
that cannot be read is reported as such.

## What is sent

Only what a feature needs, only to the provider the administrator chose, and only what Admin › AI allows.

Always sent when a feature is on:

- part numbers, descriptions and file names;
- the lists to choose from: categories and materials of the installed packs, standard-part candidates
  found in the library, and a few similar parts already in Kosha (part number and description);
- change-order numbers, titles, reasons and descriptions, and the measured change lists (for example
  "Diameter Ø105 changed to Ø110"), where-used and stock figures;
- the engineer's short note and the question asked;
- for Ask Kosha: short summaries of the matching records the asking person may open (at most the number
  set under Admin › AI, 12 by default).

Sent only when the administrator ticks it under **What may be sent**:

| Setting | Default | What it adds |
| --- | --- | --- |
| Item properties | on | material, type, unit, weight, make or buy, category, ERP stock |
| People's names | off | who asked for a change, who approves it, who has a file checked out |
| Text read from files | off | title-block values of drawings and models |

Never sent: file contents (drawings, models, documents, inputs' files), passwords, API keys of other
add-ins, and records the asking person may not open (restricted inputs, deleted files).

**Admin › AI › Recent requests** lists every request: who asked, for which record, what became of it,
and **What was sent**, the complete request exactly as it went to the provider.

## How it stays safe

- Every call to the provider goes through Kosha's add-in guard: a time-out (90 seconds by default,
  Admin › AI), a failure count, and an automatic switch-off after repeated crashes. Requests run in the
  background; the screen waits briefly and then checks back.
- Pressing a button twice for the same facts returns the same suggestion instead of asking again. Each
  person can have at most three requests waiting at a time.
- When the provider is off, slow or broken, the screen says "The AI assistant could not be reached; your
  work is safe. Try again later or ask your administrator." The administrator sees the provider's own
  message in the request list. Nothing on the screen is lost.
- A request cut off by a restart is reported as failed, never left waiting.

## The AI engine: a model for each job, cost and local-only data

Every request goes through one part of the Kosha server, the AI engine. No browser, add-in or user holds an AI key.

- **A model for each job.** Under **Admin › AI › A model for each job** each feature can name its own model
  (a small, cheap one for classification, a larger one for questions, a vision model for title blocks, a
  speech model for voice) and a **fallback**: another model, or another provider such as a model server
  inside the company. Model names change often; test a new one, then switch here without a software update.
  An empty model uses the one in the provider's settings. Providers that cannot switch models (their add-in
  does not say `supports_model_choice`) always use their own setting.
- **One retry.** An answer that does not fit what the job expects is asked for once more, then dropped and
  reported. A provider that cannot be reached hands the request to the fallback.
- **The log.** Every call is recorded with its job, provider, model, person, tokens, time and cost, never
  the content (table `ai_calls`). **Use and cost this month** shows it per job, with the share of
  suggestions people accepted.
- **Budget.** Enter the prices of your models per million tokens and a monthly budget. Administrators get a
  notification at 50%, 80% and 100%. At 100% the AI extras pause until the next month, unless you untick
  **Pause the AI extras when the budget is used up**; everything else keeps working.
- **Local only.** Restricted parts, and the categories, customers and folders you list, are *local only*:
  their data goes only to the provider you choose as **AI server inside the company**. Without one, the AI
  assistant skips them: classification and drawing checks say so, and Ask Kosha leaves those records out
  and says how many.

## Running it fully local

The official add-in **OpenAI-compatible AI provider** (`addins/openai-compatible/`) talks to any server
that offers the widely used OpenAI-compatible chat-completions API. Self-hosted runners offer it, so
the whole set-up can stay on your own network:

1. On a machine in your network (with enough memory, ideally a GPU), install a model runner such as
   Ollama, the llama.cpp server or vLLM, and load an instruction-following model that the runner lists.
   Smaller models work for classification and drafts; larger ones answer questions better.
2. In Kosha, open **Admin › Add-ins › OpenAI-compatible AI provider › Settings**. The address defaults
   to `http://localhost:11434/v1` (a runner on the Kosha server itself); change it to your runner's
   address, which usually ends in `/v1`. Enter the model name exactly as the runner lists it. Leave the
   API key empty if the runner needs none. Press **Test**.
3. Open **Admin › AI**, choose the provider, decide what may be sent, switch on the features you want,
   and press **Test the provider**.

With a hosted service instead, read its terms on data use first, use HTTPS, and send as little as
possible (leave people's names and file text off).

## For add-in authors

An AI provider is an add-in of kind `ai-provider` with the permission `ai:provide`, built on
`kosha_sdk.ai.AiProvider` (`health`, `complete`, optionally `embed` and `transcribe`). A provider that can
use a model other than the one in its settings sets `supports_model_choice = True` and accepts `model=`;
messages can carry images (`Message.images`, data URLs) for vision models. See the
[SDK README](../sdk/python/README.md#ai-providers); `kosha-sdk new ai-provider my-model` starts one, and
`kosha_sdk.testing.check_ai_provider` checks it.
