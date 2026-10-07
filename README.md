# Machine in the Shell — terminal agent

An English-language terminal agent for the public Telegram archive at `@machineintheshell`. It downloads real channel posts, stores them locally in SQLite, retrieves relevant post content, and prints direct Telegram sources with every answer.

It needs only Python 3.10+; no third-party packages or Telegram API credentials are required.

## Start with the real channel

```bash
python3 agent.py sync-channel
python3 agent.py chat
```

`sync-channel` downloads public posts from `https://t.me/s/machineintheshell`. By default it scans up to 50 pages (roughly 1,000 posts). It is safe to rerun: existing post fragments are updated instead of duplicated.

Ask one question without opening the chat:

```bash
python3 agent.py ask "What does the channel say about coding agents?"
```

Useful commands:

```bash
python3 agent.py sync-channel --pages 10
python3 agent.py sync-channel --channel another_public_channel
python3 agent.py stats
```

## Bring your own export

`ingest` also accepts a Telegram Desktop JSON export with a `messages` field, a JSONL list of posts, or Markdown (separate posts with a line containing `---`).

```bash
python3 agent.py ingest /path/to/result.json
```

The local database is created at `data/archive.db` and is excluded from Git. The small `examples/channel-export.json` file is only a fixture for offline testing; it is not the knowledge base.

## Optional LLM answer synthesis

Without a model, the agent returns the relevant source excerpts. To produce a concise, cited answer, set any OpenAI-compatible `/chat/completions` endpoint:

```bash
export AGENT_LLM_URL="https://your-provider.example/v1/chat/completions"
export AGENT_LLM_KEY="..."
export AGENT_LLM_MODEL="..."
python3 agent.py chat
```

The model receives only the question and the retrieved post fragments. Its prompt requires English, archive-grounded answers and source citations. If the provider is unavailable, the agent falls back to local retrieval automatically.
