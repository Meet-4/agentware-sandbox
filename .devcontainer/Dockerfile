FROM python:3.12-slim

# agentkit is stdlib-only (no pip dependencies); install editable so the
# `agentkit` package + `agentkit.trigger` server are importable.
WORKDIR /workspace
COPY . .

# Codespaces / devcontainer runtime. The agent itself runs on the host too —
# nothing here is required for the webhook server; this image only gives
# judges a one-click second route (clone → Codespaces → run).
RUN pip install --no-cache-dir -e . && python -m pytest -q

EXPOSE 8765
CMD ["python", "-m", "agentkit.trigger", "serve", "--port", "8765"]
