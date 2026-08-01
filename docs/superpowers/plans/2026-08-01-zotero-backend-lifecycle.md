# Zotero Backend Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure a local BabelDOC backend launched by Zotero exits when its owning Zotero process exits, preventing stale code from being reused.

**Architecture:** Add a best-effort `psutil` ancestor detector and daemon watchdog to the Python HTTP server. The watchdog owns no translation state; it only invokes the server's normal `shutdown()` path when the exact Zotero PID/create-time identity disappears.

**Tech Stack:** Python 3.12, `psutil`, `ThreadingHTTPServer`, `unittest`.

## Global Constraints

- Do not stop or manage backends that have no Zotero ancestor.
- Identify the owner with both PID and process creation time.
- Process-inspection failures must not prevent server startup.
- Use the existing server shutdown and cleanup path.

---

### Task 1: Parent-process watchdog

**Files:**
- Modify: `local_babeldoc_server/server.py`
- Test: `tests/test_local_babeldoc_server.py`

**Interfaces:**
- Produces: `WatchedProcess(pid: int, create_time: float)`, `find_zotero_ancestor(process=None)`, `is_watched_process_alive(watched, process_factory=None)`, and `start_zotero_parent_watchdog(server, ...)`.

- [ ] **Step 1: Write failing behavior tests**

Add tests using small fake process objects and a real `threading.Event` callback target. Cover discovery of a Zotero ancestor, rejection of non-Zotero ancestors, PID reuse via changed creation time, and invocation of shutdown after the owner disappears.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest tests.test_local_babeldoc_server.ZoteroParentWatchdogTest -v`

Expected: import failure because the watchdog interfaces do not exist.

- [ ] **Step 3: Implement the minimal watchdog**

Add lazy `psutil` process access, exact process identity checks, and a daemon thread that polls every two seconds. Start it in `main()` after constructing `LocalBabelDOCServer` and before `serve_forever()`.

- [ ] **Step 4: Run focused and full tests**

Run: `python3 -m unittest tests.test_local_babeldoc_server.ZoteroParentWatchdogTest -v`

Run: `python3 -m unittest discover -s tests -p 'test_*.py'`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Commit the tests and implementation with `fix: stop orphaned Zotero translation backend`.

### Task 2: Deploy and end-to-end validation

**Files:**
- Deploy: `/home/lbz/Local-Immersive-Translate/local_babeldoc_server/server.py`
- Validate: user-authorized Alatise/Hancke PDF in Zotero storage.

**Interfaces:**
- Consumes: the watchdog implementation and existing PDF structure/completeness safeguards.
- Produces: a newly started backend process and audited validation PDFs.

- [ ] **Step 1: Deploy backend files and build the plugin**

Copy the verified backend files through the existing deployment workflow and run `pnpm build` when the local Node toolchain is available.

- [ ] **Step 2: Start a fresh backend and verify its process time**

Confirm no pre-deployment process is listening on port 8765, then start the backend through the current integration path and verify its start time is after deployment.

- [ ] **Step 3: Run the OCR=true authorized PDF translation**

Use the current Zotero model configuration with `OCRWorkaround=true`, `primaryFontFamily=serif`, table OCR enabled, reference preservation enabled, and translation completeness failure enabled.

- [ ] **Step 4: Audit output**

Require table OCR translation inputs, zero reference-like model inputs, zero empty outputs, zero unresolved substantial translations, identical source/target reference text, and successful rendering of all 17 mono and dual pages.

- [ ] **Step 5: Commit validation-related source changes if any**

Do not commit generated PDFs, API credentials, caches, or temporary diagnostic files.
