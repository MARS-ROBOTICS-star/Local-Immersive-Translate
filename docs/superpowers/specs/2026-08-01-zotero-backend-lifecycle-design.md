# Zotero Backend Lifecycle Design

## Problem

The local BabelDOC server started by Zotero can outlive the Zotero process. A later Zotero session sees a healthy `/healthz` endpoint and reuses that old Python process, so backend fixes written to disk are not loaded. This caused a new translation to use the pre-fix table and reference behavior even after Zotero was restarted.

## Chosen design

The Python backend will detect whether it was launched underneath a Zotero ancestor. When such an ancestor exists, it records the Zotero PID and process creation time and starts a daemon watchdog. If that exact Zotero process disappears, the watchdog calls `ThreadingHTTPServer.shutdown()`, allowing normal server cleanup. A backend launched manually or by another service has no Zotero ancestor and remains unmanaged.

Process creation time is part of the identity so PID reuse cannot keep a stale backend alive. Process inspection is best-effort: missing `psutil`, access errors, or platforms that cannot expose ancestors must not prevent server startup.

## Alternatives considered

- Frontend-only shutdown cleanup: small, but it does not cover crashes or shutdown hooks that fail to run.
- Health endpoint build fingerprints: detects stale code, but requires a coordinated protocol and restart authority in both TypeScript and Python.
- Permanent systemd service: reliable on Linux, but changes deployment architecture and is not portable.

## Verification

- Unit-test Zotero ancestor discovery, PID-reuse detection, manual-launch behavior, and the watchdog shutdown callback.
- Run the full Python test suite and production build/type check.
- Kill the already orphaned process, deploy the backend, and confirm the next server process has a new start time.
- Re-run the authorized Alatise/Hancke PDF with Zotero's actual `OCRWorkaround=true` option and audit table OCR blocks, reference exclusion, empty/unchanged translations, and rendered pages.
