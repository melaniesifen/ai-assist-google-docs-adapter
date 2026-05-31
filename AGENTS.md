# AGENTS.md

## Repo Purpose

`ai-assist-google-docs-adapter` owns the Google Docs connector boundary: resource listing, context reads, verified ranges/anchors/revisions, target verification, and safe replace/insert write-back.

## Agent Instructions

- Read `README.md`, `ai-assist-platform-context.md`, `../ai-assist-architecture/lld-context-connectors.md`, and `../ai-assist-architecture/lld-actions-writeback.md` before changing behavior.
- Keep Google-specific API behavior isolated here. Do not add prompt construction or model provider calls.
- Use injected token-provider and Google-client boundaries; do not introduce plaintext token storage.
- MVP write-back is limited to safe replace/insert after verification.
- Validate resource identity, revision or concurrency marker, target range/anchor, and original text hash before mutation.
- Never apply mutations based only on client-supplied selected text.
- Add tests for permission errors, quota errors, stale revisions, range/hash mismatch, no-mutation conflict behavior, and safe successful replace/insert.

## Commands

- Run tests with `python3 -m unittest discover -s tests`.
- The current package uses only the Python standard library. Do not add third-party dependencies without repo-local manifests and documented install/test commands.

## Review Notes

Before committing, review for unsafe retries, duplicate mutations, token/document text leakage, and whether conflicts stop before provider mutation.

## Commit Messages

All commits in this repo must use this format:

```text
docs/feat/fix/(or another appropriate type): title of change

problem: <description of problem>
solution: <description of solution>
impact: <impact of this change>
reference: <reference to this change in the docs if applicable>
```
