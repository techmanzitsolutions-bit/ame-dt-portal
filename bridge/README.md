# AME Office Bridge

This component runs on the office Windows PC only.

Responsibilities:

1. Watch the configured scanner folder (production target: `D:\AME-Production\Incoming`).
2. Detect PDFs only after the scanner has finished writing them.
3. Register document metadata with the online AME portal.
4. Keep the original PDF on the office PC until an ERP upload job requires it.
5. Execute ERP automation locally with the account's persistent browser profile.
6. Report progress, replacement-required state, verification result and audit information back to the online portal.
7. Move a verified successful PDF to the local Uploaded archive.

The bridge must never commit ERP/Microsoft credentials, session cookies, bridge tokens, scanned PDFs or ERP profile data to GitHub.
