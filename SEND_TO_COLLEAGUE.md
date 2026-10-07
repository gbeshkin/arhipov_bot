# Send this folder to a colleague

1. Unzip `machine-in-the-shell-agent.zip`.
2. Open Terminal in the extracted folder.
3. Run:

   ```bash
   ./run.sh sync-channel
   ./run.sh chat
   ```

No Python packages or Telegram API credentials are needed. Python 3.10 or later is the only prerequisite.

The first command downloads posts from the public `@machineintheshell` channel; the second starts the English-language Q&A session.

For an immediate one-off question:

```bash
./run.sh ask "What does the channel say about coding agents?"
```

To enable LLM-written answers rather than retrieved source excerpts, see the optional `AGENT_LLM_*` variables in `README.md`.
