# Spec 239: JSON Debate History in LLM Prompts

Status: implemented

All LLM-facing complete debate histories now use a single JSON serializer. Entries are grouped by contiguous stage and contain only `stage` and `message`; each message contains `speaker` and string `content`, with missing/empty content represented as `""`. Internal metadata remains available in diagnostic snapshots but is excluded from the model-facing history shape.

Verification: prompt, experiment and post-match tests pass; Ruff and Pyright pass for changed Core modules. Prompt version is `paper-v2.1-json-history-2026-08-30`.
