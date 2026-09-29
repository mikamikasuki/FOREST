# Security

FOREST is a release candidate for trusted single-owner research. The default API is local to the host. Research commands have the worker user's permissions and can read, write, or access the network within that account's authority. Path checks and process supervision are not an operating-system sandbox.

## Deployment boundary

Run the service under a dedicated account and back up its database and project files. For network access, configure owner authentication, HTTPS, and a reverse proxy. Do not publish an unauthenticated development server. A container service boundary does not automatically isolate arbitrary research tasks from the data mounted into that container.

Only run imported code after reviewing it. Treat downloaded papers, web content, tool results, and imported files as untrusted data. Provider prompts can contain project text and supplied data: use a provider appropriate for that material.

## Secrets and paid usage

Keep `.env`, `var/`, runtime databases, provider credentials, owner tokens, and deployment secret files out of source control and public attachments. Source packaging uses an explicit allowlist and common-secret checks, but generated reports and custom files still need review before sharing.

Provider and project authorization are required for external model calls. Use the shared provider budget cap and explicit pricing for paid testing. Reservations reduce overspending risk; they are not a replacement for the provider's own account limits or invoice reconciliation.

## Reporting a vulnerability

Use the hosting repository's private vulnerability reporting channel if enabled. Otherwise contact the repository maintainer privately before posting sensitive details. Do not include credentials or private research data. Include the affected revision, a minimal reproduction, expected access boundary, and observed impact. No dedicated security-response service or response-time guarantee is currently offered.

If a credential was exposed, revoke it at the provider and rotate the local secret. Preserve relevant logs privately. Avoid publishing the secret in a public issue or adding it to a reproduction test.
