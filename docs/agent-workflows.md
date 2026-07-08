# Agent workflow files

This repository intentionally shares only small, project-scoped agent workflow
files. Keep reusable project coordination in git; keep personal preferences,
session state, credentials, and local experiments out of git.

## Shared in the repository

| Path | Include? | Rationale |
| --- | --- | --- |
| `AGENTS.md` | Yes | Cross-agent project entry point and Beads workflow pointer. |
| `CLAUDE.md` | Yes | Claude-specific entry point; mirror substantive agent guidance from `AGENTS.md`. |
| `.agents/skills/beads/` | Yes | Project Beads skill so agents can recover durable task-tracking workflow without relying on a user's global install. |
| `.claude/settings.json` | Yes | Repository-scoped Beads session hook only; no user preferences or secrets. |
| `.codex/config.toml` | Yes | Enables repository-scoped Codex hooks. |
| `.codex/hooks.json` | Yes | Repository-scoped Beads context refresh hooks only. |
| `.beads/README.md`, `.beads/config.yaml`, `.beads/metadata.json`, `.beads/hooks/`, `.beads/interactions.jsonl` | Yes | Shared issue-tracker configuration and audit/history files. |

## Keep private or generated

| Path/pattern | Include? | Rationale |
| --- | --- | --- |
| `.env` | No | Local credentials and deployment endpoints. Use `.env.example` for safe defaults. |
| `.venv/`, `.pytest_cache/`, `__pycache__/`, `dist/` | No | Generated local tooling/build artifacts. |
| `.beads/embeddeddolt/`, `.beads/proxieddb/`, `.beads/.local_version`, `.beads/last-touched`, `.beads-credential-key` | No | Local Beads/Dolt database state, locks, timestamps, or credentials. |
| `.agents/private/`, `.agents/tmp/` | No | Local-only agent experiments, scratch prompts, or private workflows. |
| `.claude/settings.local.json` | No | Personal Claude permissions, model preferences, or local hooks. |
| `.codex/config.local.toml`, `.codex/sessions/`, `.codex/history*`, `.codex/logs/`, `.codex/tmp/` | No | Personal Codex preferences, transcripts, logs, and scratch state. |

## Rule of thumb

Share files that are deterministic, project-specific, and safe for every clone.
Do not share files that identify a person, machine, account, service endpoint,
credential, session transcript, local approval, or experimental private workflow.