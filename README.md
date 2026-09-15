# AME DT Portal

Online web portal for the AME scanner-to-ERP workflow.

## Architecture

- **Online portal:** operator UI, Admin UI, driver/account mapping, queue/status and audit history.
- **Office PC bridge:** watches the local scanner/PDF folder and securely synchronizes document metadata/files with the online portal. The office PC remains the document-storage/scanner bridge, not the user-facing web server.
- **ERP worker:** ERP automation stays on the office PC because Microsoft SSO/MFA and the persistent ERP browser profile must remain local. The online portal sends jobs to the bridge; the bridge performs the ERP upload and reports progress/result.
- **Tablet/PC:** use the same online HTTPS portal and can install it as a standalone PWA/app.

## Target workflow

Scanner -> Office PC storage -> AME Bridge -> Online Portal -> claim -> driver -> DT -> ERP job -> local ERP worker -> verify -> archive/status.

## Security

- Do not store Microsoft/ERP passwords in the web application or GitHub.
- Use per-installation bridge authentication.
- Keep secrets in deployment environment variables, never committed source.
- Only the office bridge is allowed to retrieve local documents for ERP upload.

Initial project structure will separate `web/` and `bridge/` so the online portal can be deployed independently of the Windows scanner service.
