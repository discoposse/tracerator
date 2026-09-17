# Security Policy

## Supported versions

Security fixes land on `main` of [discoposse/tracerator](https://github.com/discoposse/tracerator).

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on that repository. Do not open a public issue for a secret leak or a remote exploit.

Include the affected path, a reproduction against a local checkout, and the impact.

## Secrets in this project

- Copy `.env.example` to `.env`. Never commit `.env`.
- `HF_TOKEN` and inference API keys stay on the host.
- Rotate any token that was pasted into a ticket, chat, or screenshot.
