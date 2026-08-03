#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import quote
from urllib.parse import unquote
from urllib.parse import urlencode
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, "") and str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from local_babeldoc_server.translation_quality import validate_translation
from local_babeldoc_server.pdf_output_quality import audit_side_by_side_source
from local_babeldoc_server.pdf_output_quality import (
    ensure_side_by_side_source_preserved,
)

DEFAULT_DATA_DIR = REPO_ROOT / ".local-babeldoc"
DEFAULT_BABELDOC_REPO = REPO_ROOT / "BabelDOC"
BACKEND_BUILD = "translation-runtime-safety-v1"

GEMINI_STANDARD_PRICE_PROFILES: dict[str, tuple[str, str, str]] = {
    "gemini-3.6-flash": ("1.5", "7.5", "0.15"),
    "gemini-3.1-flash-lite": ("0.25", "1.50", "0.025"),
}

MODEL_DEFAULTS: dict[str, dict[str, str]] = {
    "kimi": {
        "label": "Kimi",
        "base_url": "",
        "api_key": "env:KIMI_API_KEY",
        "model": "",
    },
    "qwen-1": {
        "label": "Qwen",
        "base_url": "",
        "api_key": "env:QWEN_API_KEY",
        "model": "",
    },
    "deepseek": {
        "label": "DeepSeek",
        "provider": "deepseek",
        "api_surface": "openai-chat-completions",
        "base_url": "",
        "api_key": "env:DEEPSEEK_API_KEY",
        "model": "",
        "input_usd_per_million": "0.27",
        "output_usd_per_million": "1.10",
        "cached_input_usd_per_million": "0.135",
    },
    "glm-paid-1": {
        "label": "GLM 4.7",
        "base_url": "",
        "api_key": "env:GLM_API_KEY",
        "model": "",
    },
    "gpt-1": {
        "label": "OpenAI",
        "base_url": "",
        "api_key": "env:OPENAI_API_KEY",
        "model": "",
    },
    "gemini-1": {
        "label": "Gemini",
        "provider": "google",
        "api_surface": "interactions-v1",
        "base_url": "",
        "api_key": "env:GEMINI_API_KEY",
        "model": "",
        "pricing_model": "gemini-3.6-flash",
        "service_tier": "standard",
        "input_usd_per_million": "1.5",
        "output_usd_per_million": "7.5",
        "cached_input_usd_per_million": "0.15",
    },
    "glm-free-1": {
        "label": "GLM-4-Flash",
        "base_url": "",
        "api_key": "env:GLM_API_KEY",
        "model": "",
    },
}

DEFAULT_CONFIG: dict[str, Any] = {
    "server": {
        "host": "127.0.0.1",
        "port": 8765,
        "public_base_url": "",
        "token": "",
        "max_concurrent_jobs": 1,
    },
    "babeldoc": {
        "repo_path": str(DEFAULT_BABELDOC_REPO),
        "data_dir": str(DEFAULT_DATA_DIR),
        "lang_in": "en",
        "qps": 4,
        "pool_max_workers": 4,
        "term_pool_max_workers": 2,
        "report_interval_seconds": 0.5,
        "max_pages_per_part": 20,
        "watermark": "no_watermark",
        "debug": False,
        "enable_json_mode_if_requested": False,
        "send_dashscope_header": False,
        "send_temperature": True,
        "disable_same_text_fallback": False,
        "skip_scanned_detection": False,
        "skip_form_render": False,
        "skip_curve_render": False,
        "remove_non_formula_lines": False,
        "enable_native_table_translation": True,
        "enable_table_ocr": True,
        "preserve_references": True,
        "preserve_toc_layout": True,
        "enable_translation_quality_guard": True,
    "translation_retry_chunk_sizes": [700, 350],
    "max_requests_per_document": 500,
    "max_estimated_cost_jpy": 300,
        "usd_to_jpy": 150,
        "max_semantic_attempts_per_paragraph": 2,
        "max_billable_exposures_per_paragraph": 2,
        "explicit_transport_retries": 1,
        "batch_target_source_tokens": 2400,
        "batch_max_source_tokens": 3200,
        "batch_max_paragraphs": 40,
        "max_output_tokens": 4096,
        "max_table_ocr_paragraphs": 200,
        "max_ocr_blocks_per_table": 100,
        "max_table_ocr_to_native_ratio": 0.5,
        "ratio_check_min_native_paragraphs": 20,
        "min_table_translation_scale": 0.55,
        "table_bbox_tolerance": 0.5,
        "fail_on_unresolved_translation": False,
    },
    "models": MODEL_DEFAULTS,
}

logger = logging.getLogger("local_babeldoc_server")


class TranslationCompletenessError(RuntimeError):
    pass


@dataclass(frozen=True)
class WatchedProcess:
    pid: int
    create_time: float


def _is_zotero_process(process: Any) -> bool:
    try:
        name = str(process.name() or "").casefold().removesuffix(".exe")
        command = list(process.cmdline() or [])
    except Exception:
        return False
    executable = ""
    if command:
        executable = Path(str(command[0])).name.casefold().removesuffix(".exe")
    return name in {"zotero", "zotero-bin"} or executable in {
        "zotero",
        "zotero-bin",
    }


def find_zotero_ancestor(process: Any = None) -> WatchedProcess | None:
    if process is None:
        try:
            import psutil

            process = psutil.Process()
        except Exception:
            logger.warning(
                "Unable to inspect the backend parent process; "
                "Zotero lifecycle watchdog is disabled",
                exc_info=True,
            )
            return None
    try:
        parents = process.parents()
    except Exception:
        logger.warning(
            "Unable to inspect backend ancestors; "
            "Zotero lifecycle watchdog is disabled",
            exc_info=True,
        )
        return None
    for parent in parents:
        if not _is_zotero_process(parent):
            continue
        try:
            return WatchedProcess(
                pid=int(parent.pid),
                create_time=float(parent.create_time()),
            )
        except Exception:
            logger.warning(
                "Unable to record Zotero process identity; "
                "lifecycle watchdog is disabled",
                exc_info=True,
            )
            return None
    return None


def is_watched_process_alive(
    watched: WatchedProcess,
    process_factory: Any = None,
) -> bool:
    if process_factory is None:
        try:
            import psutil

            process_factory = psutil.Process
        except Exception:
            return True
    try:
        process = process_factory(watched.pid)
        return abs(float(process.create_time()) - watched.create_time) < 0.001
    except Exception as exc:
        try:
            import psutil

            if isinstance(exc, psutil.AccessDenied):
                return True
        except Exception:
            pass
        return False


def start_zotero_parent_watchdog(
    server: Any,
    *,
    current_process: Any = None,
    process_factory: Any = None,
    poll_interval: float = 2.0,
) -> threading.Thread | None:
    watched = find_zotero_ancestor(current_process)
    if watched is None:
        return None

    def watch_owner() -> None:
        while is_watched_process_alive(watched, process_factory):
            time.sleep(poll_interval)
        logger.info(
            "Owning Zotero process %s exited; stopping local BabelDOC server",
            watched.pid,
        )
        server.shutdown()

    thread = threading.Thread(
        target=watch_owner,
        name="zotero-parent-watchdog",
        daemon=True,
    )
    thread.start()
    logger.info("Watching owning Zotero process: pid=%s", watched.pid)
    return thread


@dataclass(frozen=True)
class TranslationAudit:
    tracking_file_count: int = 0
    tracking_missing: bool = False
    attempted_count: int = 0
    accepted_count: int = 0
    recovered_count: int = 0
    unresolved_count: int = 0
    source_preserved_count: int = 0
    empty_replacement_count: int = 0
    protected_token_mismatch_count: int = 0
    reference_excluded_count: int = 0
    unresolved: tuple[dict[str, Any], ...] = ()


_REFERENCE_HEADING_RE = re.compile(
    r"^\s*(references|bibliography|works\s+cited|literature\s+cited)\s*$",
    re.IGNORECASE,
)
_REFERENCE_ENTRY_RE = re.compile(r"^\s*\[\s*\d+\s*]\s+\S+")


def _is_reference_like(text: str) -> bool:
    source = (text or "").strip()
    return bool(
        _REFERENCE_HEADING_RE.match(source) or _REFERENCE_ENTRY_RE.match(source)
    )


def audit_translation_completion(
    working_dir: Path,
    local_quality: dict[str, Any] | None,
    target_language: str,
    *,
    expected_translation: bool = False,
) -> TranslationAudit:
    attempted_count = 0
    accepted_count = 0
    empty_replacement_count = 0
    protected_token_mismatch_count = 0
    reference_excluded_count = 0
    unresolved: list[dict[str, Any]] = []
    source_preserved_count = 0

    tracking_paths = sorted(working_dir.rglob("translate_tracking.json"))
    for tracking_path in tracking_paths:
        with tracking_path.open("r", encoding="utf-8") as handle:
            tracking = json.load(handle)
        for section in ("page", "cross_page", "cross_column"):
            for page_index, page in enumerate(tracking.get(section, [])):
                for paragraph_index, paragraph in enumerate(
                    page.get("paragraph", [])
                ):
                    source = paragraph.get("input") or ""
                    if not source.strip():
                        continue
                    if _is_reference_like(source):
                        reference_excluded_count += 1
                        continue
                    attempted_count += 1
                    target = paragraph.get("output")
                    if target is None or not str(target).strip():
                        empty_replacement_count += 1
                        unresolved.append(
                            {
                                "section": section,
                                "page_index": page_index,
                                "paragraph_index": paragraph_index,
                                "source_preview": source[:240],
                                "reasons": ["empty_target"],
                            }
                        )
                        continue
                    validation = validate_translation(
                        source,
                        str(target),
                        target_language,
                    )
                    if validation.accepted:
                        accepted_count += 1
                        continue
                    if str(target).strip() == source.strip():
                        source_preserved_count += 1
                    if "protected_token_mismatch" in validation.reasons:
                        protected_token_mismatch_count += 1
                    unresolved.append(
                        {
                            "section": section,
                            "page_index": page_index,
                            "paragraph_index": paragraph_index,
                            "source_preview": source[:240],
                            "reasons": list(validation.reasons),
                        }
                    )

    local_quality = local_quality or {}
    local_unresolved = int(local_quality.get("unresolved_count") or 0)
    local_source_preserved = sum(
        1
        for item in local_quality.get("unresolved", [])
        if isinstance(item, dict) and item.get("source_preserved") is True
    )
    unresolved_count = max(len(unresolved), local_unresolved)
    if local_unresolved > len(unresolved):
        unresolved.extend(local_quality.get("unresolved", []))

    return TranslationAudit(
        tracking_file_count=len(tracking_paths),
        tracking_missing=bool(expected_translation and not tracking_paths),
        attempted_count=attempted_count,
        accepted_count=accepted_count,
        recovered_count=int(local_quality.get("recovered_count") or 0),
        unresolved_count=unresolved_count,
        source_preserved_count=max(
            source_preserved_count,
            local_source_preserved,
        ),
        empty_replacement_count=empty_replacement_count,
        protected_token_mismatch_count=protected_token_mismatch_count,
        reference_excluded_count=reference_excluded_count,
        unresolved=tuple(unresolved),
    )


def ensure_translation_complete(
    audit: TranslationAudit,
    fail_on_unresolved: bool,
    *,
    abort_reason: str | None = None,
) -> bool:
    if getattr(audit, "tracking_missing", False):
        raise TranslationCompletenessError(
            "translation tracking is missing; completion cannot be audited"
        )
    if audit.empty_replacement_count:
        if fail_on_unresolved:
            if abort_reason:
                raise TranslationCompletenessError(
                    f"translation stopped by {abort_reason}: "
                    f"{audit.empty_replacement_count} untranslated paragraph "
                    "attempts; source text was preserved"
                )
            raise TranslationCompletenessError(
                f"translation completeness check found "
                f"{audit.empty_replacement_count} empty paragraph replacements"
            )
        audit.empty_replacement_count = 0
    if fail_on_unresolved and audit.unresolved_count:
        source_preserved_count = int(
            getattr(audit, "source_preserved_count", 0) or 0
        )
        attempted_count = int(getattr(audit, "attempted_count", 0) or 0)
        warning_allowed = (
            abort_reason is None
            and audit.unresolved_count == source_preserved_count
            and audit.unresolved_count <= 3
            and audit.unresolved_count / max(1, attempted_count) <= 0.01
        )
        if warning_allowed:
            return True
        raise TranslationCompletenessError(
            f"translation completeness check found "
            f"{audit.unresolved_count} unresolved translatable paragraphs"
        )
    return False

PROXY_ENV_NAMES = (
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
)


@dataclass
class Job:
    pdf_id: str
    object_key: str
    file_name: str
    request_model: str
    target_language: str
    model_config: dict[str, Any] | None
    options: dict[str, Any]
    created_at: float
    status: str = "queued"
    stage: str = "Waiting in line"
    progress: float = 0.0
    message: str = ""
    error: str = ""
    translation_pdf_path: str = ""
    dual_pdf_path: str = ""
    total_seconds: float = 0.0


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path | None) -> dict[str, Any]:
    config = DEFAULT_CONFIG
    if path and path.exists():
        with path.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        config = deep_merge(config, loaded)
    config["models"] = deep_merge(MODEL_DEFAULTS, config.get("models", {}))
    return config


def resolve_config_value(value: str | None) -> str:
    if not value:
        return ""
    if value.startswith("env:"):
        return os.environ.get(value[4:], "")
    return value


def required_nonnegative_decimal(
    config: dict[str, Any],
    key: str,
) -> Decimal:
    value = config.get(key)
    if value is None or str(value).strip() == "":
        raise ValueError(f"model pricing is missing: {key}")
    try:
        result = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"invalid model pricing for {key}: {value!r}") from error
    if not result.is_finite() or result < 0:
        raise ValueError(f"model pricing must be non-negative: {key}")
    return result


def normalize_proxy_env() -> None:
    for name in PROXY_ENV_NAMES:
        value = os.environ.get(name)
        if not value:
            continue
        if value.lower().startswith("socks://"):
            normalized = f"socks5://{value[len('socks://') :]}"
            os.environ[name] = normalized
            logger.info("Normalized %s proxy scheme from socks:// to socks5://", name)


def safe_object_key(value: str) -> str:
    name = Path(unquote(value)).name
    if not name:
        raise ValueError("empty object key")
    return name


def normalize_lang_out(value: str) -> str:
    normalized = (value or "zh").strip().lower().replace("_", "-")
    aliases = {
        "zh-cn": "zh",
        "zh-hans": "zh",
        "zh-sg": "zh",
        "zh-tw": "zh-tw",
        "zh-hant": "zh-tw",
        "en-us": "en",
        "en-gb": "en",
        "ja-jp": "ja",
        "ko-kr": "ko",
    }
    return aliases.get(normalized, normalized)


def resolve_repo_relative_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def dual_mode_to_babeldoc(value: str) -> tuple[bool, bool]:
    # lort: left original, right translation
    # ltro: left translation, right original
    # uodt: alternating pages, original first
    # utdo: alternating pages, translation first
    mode = (value or "lort").lower()
    return mode in {"ltro", "utdo"}, mode in {"uodt", "utdo"}


class AppState:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        babeldoc_cfg = config["babeldoc"]
        data_dir = resolve_repo_relative_path(babeldoc_cfg["data_dir"])
        self.data_dir = data_dir
        self.upload_dir = data_dir / "uploads"
        self.output_dir = data_dir / "outputs"
        self.working_dir = data_dir / "working"
        for directory in (self.upload_dir, self.output_dir, self.working_dir):
            directory.mkdir(parents=True, exist_ok=True)

        server_cfg = config["server"]
        self.token = server_cfg.get("token", "")
        self.public_base_url = server_cfg.get("public_base_url", "").rstrip("/")
        max_jobs = int(server_cfg.get("max_concurrent_jobs") or 1)
        self.job_semaphore = threading.Semaphore(max_jobs)
        self.jobs: dict[str, Job] = {}
        self.jobs_lock = threading.RLock()
        self.doc_layout_model = None
        self.doc_layout_lock = threading.Lock()
        self.babeldoc_compat_handle = None
        self.babeldoc_compat_lock = threading.Lock()
        self.table_ocr_runtime = None
        self.table_ocr_lock = threading.Lock()
        self.babeldoc_repo = resolve_repo_relative_path(babeldoc_cfg["repo_path"])
        if self.babeldoc_repo.exists():
            sys.path.insert(0, str(self.babeldoc_repo))

    def make_public_url(
        self,
        handler: BaseHTTPRequestHandler,
        path: str,
        *,
        signed: bool = False,
    ) -> str:
        base = self.public_base_url
        if not base:
            host = handler.headers.get("Host") or (
                f"{self.config['server']['host']}:{self.config['server']['port']}"
            )
            base = f"http://{host}/zotero"
        url = f"{base.rstrip('/')}/{path.lstrip('/')}"
        if signed and self.token:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urlencode({'token': self.token})}"
        return url

    def is_authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        if not self.token:
            return True
        parsed = urlparse(handler.path)
        query_token = parse_qs(parsed.query).get("token", [""])[0]
        if query_token == self.token:
            return True
        auth = handler.headers.get("Authorization", "")
        return auth == f"Bearer {self.token}"

    def get_upload_path(self, object_key: str) -> Path:
        return self.upload_dir / safe_object_key(object_key)

    def add_job(self, job: Job) -> None:
        with self.jobs_lock:
            self.jobs[job.pdf_id] = job

    def get_job(self, pdf_id: str) -> Job | None:
        with self.jobs_lock:
            return self.jobs.get(pdf_id)

    def get_job_or_recover_finished(self, pdf_id: str) -> Job | None:
        job = self.get_job(pdf_id)
        if job is not None:
            return job
        return self._recover_finished_job_from_outputs(pdf_id)

    def _recover_finished_job_from_outputs(self, pdf_id: str) -> Job | None:
        safe_pdf_id = safe_object_key(pdf_id)
        output_dir = self.output_dir / safe_pdf_id
        if not output_dir.is_dir():
            return None

        mono_path = self._latest_pdf(output_dir, "*.mono.pdf")
        dual_path = self._latest_pdf(output_dir, "*.dual.pdf")
        if not mono_path or not dual_path:
            return None

        created_at = max(mono_path.stat().st_mtime, dual_path.stat().st_mtime)
        job = Job(
            pdf_id=safe_pdf_id,
            object_key="",
            file_name=mono_path.name,
            request_model="",
            target_language="",
            model_config=None,
            options={},
            created_at=created_at,
            status="success",
            stage="completed",
            progress=100.0,
            translation_pdf_path=str(mono_path),
            dual_pdf_path=str(dual_path),
        )
        self.add_job(job)
        logger.info("Recovered completed BabelDOC job from outputs: %s", safe_pdf_id)
        return job

    def update_job(self, pdf_id: str, **updates: Any) -> None:
        with self.jobs_lock:
            job = self.jobs[pdf_id]
            for key, value in updates.items():
                setattr(job, key, value)

    def start_job(self, pdf_id: str) -> None:
        thread = threading.Thread(
            target=self._run_job_thread,
            args=(pdf_id,),
            name=f"babeldoc-job-{pdf_id}",
            daemon=True,
        )
        thread.start()

    def _run_job_thread(self, pdf_id: str) -> None:
        with self.job_semaphore:
            try:
                self.update_job(
                    pdf_id,
                    status="running",
                    stage="Create Task",
                    progress=0.0,
                    message="",
                )
                self._run_babeldoc(pdf_id)
            except Exception as exc:
                logger.exception("BabelDOC job failed: %s", pdf_id)
                self.update_job(
                    pdf_id,
                    status="failed",
                    stage="failed",
                    message=str(exc),
                    error=traceback.format_exc(),
                )

    def _get_doc_layout_model(self):
        with self.doc_layout_lock:
            if self.doc_layout_model is not None:
                return self.doc_layout_model
            from babeldoc.docvision.doclayout import DocLayoutModel

            self.doc_layout_model = DocLayoutModel.load_onnx()
            return self.doc_layout_model

    def _get_table_ocr_runtime(self):
        if not self.config["babeldoc"].get("enable_table_ocr", True):
            return None
        with self.table_ocr_lock:
            if self.table_ocr_runtime is None:
                from local_babeldoc_server.table_ocr import TableOcrRuntime
                from local_babeldoc_server.table_ocr import create_rapidocr_engine

                self.table_ocr_runtime = TableOcrRuntime(create_rapidocr_engine)
            return self.table_ocr_runtime

    def _ensure_babeldoc_compat(self) -> None:
        with self.babeldoc_compat_lock:
            if self.babeldoc_compat_handle is not None:
                return
            from local_babeldoc_server.babeldoc_compat import (
                install_babeldoc_compat,
            )

            self.babeldoc_compat_handle = install_babeldoc_compat(
                self.config["babeldoc"],
                self._get_table_ocr_runtime(),
            )

    def _create_translator(
        self,
        model_key: str,
        lang_out: str,
        model_config: dict[str, Any] | None = None,
        *,
        runtime: Any = None,
        resolved_model_config: dict[str, Any] | None = None,
        default_llm_request_category: str = "batch",
    ):
        model_cfg = resolved_model_config or self._resolve_model_config(
            model_key,
            model_config,
        )
        if runtime is not None:
            from local_babeldoc_server.translation_runtime import (
                RuntimeBackedTranslator,
            )

            babeldoc_cfg = self.config["babeldoc"]
            return RuntimeBackedTranslator(
                runtime=runtime,
                lang_in=babeldoc_cfg.get("lang_in", "en"),
                lang_out=lang_out,
                model=model_cfg["model"],
                ignore_cache=bool(model_cfg.get("ignore_cache", False)),
                default_llm_request_category=default_llm_request_category,
            )

        from babeldoc.translator.translator import OpenAITranslator

        babeldoc_cfg = self.config["babeldoc"]
        return OpenAITranslator(
            lang_in=babeldoc_cfg.get("lang_in", "en"),
            lang_out=lang_out,
            model=model_cfg["model"],
            base_url=model_cfg["base_url"],
            api_key=model_cfg["api_key"],
            ignore_cache=bool(model_cfg.get("ignore_cache", False)),
            enable_json_mode_if_requested=bool(
                babeldoc_cfg.get("enable_json_mode_if_requested", False)
            ),
            send_dashscope_header=bool(
                babeldoc_cfg.get("send_dashscope_header", False)
            ),
            send_temperature=bool(babeldoc_cfg.get("send_temperature", True)),
            reasoning=model_cfg.get("reasoning"),
            thinking=model_cfg.get("thinking"),
        )

    def _resolve_model_config(
        self,
        model_key: str,
        model_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        model_cfg = self.config["models"].get(model_key)
        if not model_cfg:
            raise ValueError(f"model '{model_key}' is not configured")
        model_cfg = dict(model_cfg)
        if model_config:
            override_cfg = {
                "base_url": model_config.get("base_url")
                or model_config.get("baseUrl")
                or "",
                "api_key": model_config.get("api_key")
                or model_config.get("apiKey")
                or "",
                "model": model_config.get("model") or "",
            }
            model_cfg.update({k: v for k, v in override_cfg.items() if v})

        base_url = resolve_config_value(model_cfg.get("base_url"))
        api_key = resolve_config_value(model_cfg.get("api_key"))
        model = resolve_config_value(model_cfg.get("model"))
        provider = str(model_cfg.get("provider") or "").casefold()
        required_values = [("api_key", api_key), ("model", model)]
        if provider not in {"google", "gemini"}:
            required_values.insert(0, ("base_url", base_url))
        missing = [
            name
            for name, value in required_values
            if not value
        ]
        if missing:
            label = model_cfg.get("label", model_key)
            raise ValueError(
                f"model '{label}' ({model_key}) is missing: {', '.join(missing)}"
            )

        model_cfg.update(
            {"base_url": base_url, "api_key": api_key, "model": model}
        )
        if provider in {"google", "gemini"}:
            profile = GEMINI_STANDARD_PRICE_PROFILES.get(model)
            pricing_model = str(model_cfg.get("pricing_model") or "")
            if profile is None and pricing_model and pricing_model != model:
                raise ValueError(
                    f"Gemini model '{model}' has no configured price profile"
                )
            if profile is not None:
                input_price, output_price, cached_price = profile
                model_cfg.update(
                    {
                        "pricing_model": model,
                        "input_usd_per_million": input_price,
                        "output_usd_per_million": output_price,
                        "cached_input_usd_per_million": cached_price,
                    }
                )
        return model_cfg

    def _create_translation_runtime(
        self,
        job: Job,
        working_dir: Path,
        *,
        adapter: Any = None,
    ) -> tuple[Any, dict[str, Any]]:
        from local_babeldoc_server.translation_audit import TranslationAuditWriter
        from local_babeldoc_server.translation_budget import DocumentBudget
        from local_babeldoc_server.translation_runtime import TranslationRuntime
        from local_babeldoc_server.translation_types import PricingSnapshot
        from local_babeldoc_server.translator_adapters import resolve_adapter

        model_cfg = self._resolve_model_config(
            job.request_model,
            job.model_config,
        )
        if adapter is None:
            adapter = resolve_adapter(model_cfg)
        babeldoc_cfg = self.config["babeldoc"]
        usd_to_jpy = required_nonnegative_decimal(
            babeldoc_cfg,
            "usd_to_jpy",
        )
        if usd_to_jpy == 0:
            raise ValueError("usd_to_jpy must be greater than zero")
        pricing = PricingSnapshot(
            provider=str(model_cfg.get("provider") or "compatible"),
            model=str(model_cfg["model"]),
            api_surface=str(
                model_cfg.get("api_surface") or "openai-chat-completions"
            ),
            service_tier=str(model_cfg.get("service_tier") or "standard"),
            input_usd_per_million=required_nonnegative_decimal(
                model_cfg,
                "input_usd_per_million",
            ),
            output_usd_per_million=required_nonnegative_decimal(
                model_cfg,
                "output_usd_per_million",
            ),
            cached_input_usd_per_million=required_nonnegative_decimal(
                model_cfg,
                "cached_input_usd_per_million",
            ),
            usd_to_jpy=usd_to_jpy,
            tax_included=bool(model_cfg.get("tax_included", False)),
            captured_at=datetime.now(timezone.utc).isoformat(),
        )
        max_cost_usd = Decimal(
            str(babeldoc_cfg.get("max_estimated_cost_jpy", 300))
        ) / usd_to_jpy
        budget = DocumentBudget(
            max_requests=int(
                babeldoc_cfg.get("max_requests_per_document", 150)
            ),
            max_cost_usd=max_cost_usd,
            max_semantic_attempts_per_paragraph=int(
                babeldoc_cfg.get("max_semantic_attempts_per_paragraph", 2)
            ),
            max_billable_exposures_per_paragraph=int(
                babeldoc_cfg.get("max_billable_exposures_per_paragraph", 2)
            ),
        )
        writer = TranslationAuditWriter(working_dir, pricing)
        return (
            TranslationRuntime(
                adapter=adapter,
                budget=budget,
                audit_writer=writer,
                pricing=pricing,
                explicit_transport_retries=int(
                    babeldoc_cfg.get("explicit_transport_retries", 1)
                ),
            ),
            model_cfg,
        )

    def test_model(
        self,
        model_key: str,
        lang_out: str,
        model_config: dict[str, Any] | None = None,
        *,
        adapter: Any = None,
    ) -> dict[str, str]:
        test_id = uuid.uuid4().hex
        job = Job(
            pdf_id=f"model-test-{test_id}",
            object_key="",
            file_name="",
            request_model=model_key,
            target_language=lang_out,
            model_config=model_config,
            options={},
            created_at=time.time(),
        )
        runtime, resolved_model_cfg = self._create_translation_runtime(
            job,
            self.working_dir / "model-tests" / test_id,
            adapter=adapter,
        )
        translator = self._create_translator(
            model_key,
            lang_out,
            model_config,
            runtime=runtime,
            resolved_model_config=resolved_model_cfg,
        )
        response_format = {
            "type": "text",
            "mime_type": "application/json",
            "schema": {
                "type": "object",
                "properties": {"translation": {"type": "string"}},
                "required": ["translation"],
                "additionalProperties": False,
            },
        }
        output = translator.do_translate(
            "Hello.",
            rate_limit_params={
                "document_id": job.pdf_id,
                "request_category": "model_test",
                "response_format": response_format,
                "max_output_tokens": 128,
            },
        )
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as error:
            raise RuntimeError("model test returned invalid structured JSON") from error
        translation = str(payload.get("translation") or "").strip()
        if not translation:
            raise RuntimeError(
                f"model '{model_key}' returned an empty response during test"
            )
        return {"translation": translation}

    def _run_babeldoc(self, pdf_id: str) -> None:
        self._ensure_babeldoc_compat()
        from babeldoc.format.pdf.high_level import async_translate
        from babeldoc.format.pdf.translation_config import TranslationConfig
        from babeldoc.format.pdf.translation_config import WatermarkOutputMode
        from babeldoc.translator.translator import set_translate_rate_limiter

        job = self.get_job(pdf_id)
        if job is None:
            raise ValueError(f"job not found: {pdf_id}")

        source_path = self.get_upload_path(job.object_key)
        if not source_path.exists():
            raise FileNotFoundError(f"uploaded PDF not found: {job.object_key}")

        babeldoc_cfg = self.config["babeldoc"]
        lang_out = normalize_lang_out(job.target_language)
        job_output_dir = self.output_dir / pdf_id
        job_working_dir = self.working_dir / pdf_id
        job_output_dir.mkdir(parents=True, exist_ok=True)
        job_working_dir.mkdir(parents=True, exist_ok=True)
        runtime, resolved_model_cfg = self._create_translation_runtime(
            job,
            job_working_dir,
        )
        translator = self._create_translator(
            job.request_model,
            lang_out,
            job.model_config,
            runtime=runtime,
            resolved_model_config=resolved_model_cfg,
        )
        term_translator = self._create_translator(
            job.request_model,
            lang_out,
            job.model_config,
            runtime=runtime,
            resolved_model_config=resolved_model_cfg,
            default_llm_request_category="terminology",
        )
        qps = int(babeldoc_cfg.get("qps") or 4)
        set_translate_rate_limiter(qps)

        split_strategy = None
        max_pages = int(babeldoc_cfg.get("max_pages_per_part") or 0)
        if max_pages > 0:
            split_strategy = TranslationConfig.create_max_pages_per_part_split_strategy(
                max_pages
            )

        dual_translate_first, use_alternating = dual_mode_to_babeldoc(
            job.options.get("dual_mode", "lort")
        )
        primary_font_family = job.options.get("primaryFontFamily")
        if primary_font_family == "none":
            primary_font_family = None

        watermark_mode = WatermarkOutputMode.NoWatermark
        if babeldoc_cfg.get("watermark") == "watermarked":
            watermark_mode = WatermarkOutputMode.Watermarked

        config = TranslationConfig(
            input_file=str(source_path),
            output_dir=str(job_output_dir),
            working_dir=str(job_working_dir),
            translator=translator,
            term_extraction_translator=term_translator,
            debug=bool(babeldoc_cfg.get("debug", False)),
            lang_in=babeldoc_cfg.get("lang_in", "en"),
            lang_out=lang_out,
            no_dual=False,
            no_mono=False,
            qps=qps,
            doc_layout_model=self._get_doc_layout_model(),
            skip_clean=False,
            dual_translate_first=dual_translate_first,
            disable_rich_text_translate=bool(
                job.options.get("disable_rich_text_translate", False)
            ),
            enhance_compatibility=bool(
                job.options.get("enhance_compatibility", False)
            ),
            use_alternating_pages_dual=use_alternating,
            report_interval=float(babeldoc_cfg.get("report_interval_seconds") or 0.5),
            watermark_output_mode=watermark_mode,
            split_strategy=split_strategy,
            skip_scanned_detection=bool(
                babeldoc_cfg.get("skip_scanned_detection", False)
            ),
            ocr_workaround=bool(job.options.get("OCRWorkaround", False)),
            auto_enable_ocr_workaround=bool(
                job.options.get("autoEnableOcrWorkAround", False)
            ),
            custom_system_prompt=job.options.get("customSystemPrompt"),
            pool_max_workers=int(babeldoc_cfg.get("pool_max_workers") or qps),
            term_pool_max_workers=int(
                babeldoc_cfg.get("term_pool_max_workers")
                or babeldoc_cfg.get("pool_max_workers")
                or qps
            ),
            auto_extract_glossary=bool(
                job.options.get("autoExtractGlossary", True)
            ),
            primary_font_family=primary_font_family,
            save_auto_extracted_glossary=False,
            merge_alternating_line_numbers=True,
            skip_form_render=bool(babeldoc_cfg.get("skip_form_render", False)),
            skip_curve_render=bool(babeldoc_cfg.get("skip_curve_render", False)),
            remove_non_formula_lines=bool(
                babeldoc_cfg.get("remove_non_formula_lines", False)
            ),
            disable_same_text_fallback=(
                bool(babeldoc_cfg.get("enable_translation_quality_guard", True))
                or bool(babeldoc_cfg.get("disable_same_text_fallback", False))
            ),
            metadata_extra_data=f"local_zotero_{pdf_id}",
        )
        config.translation_retry_chunk_sizes = list(
            babeldoc_cfg.get("translation_retry_chunk_sizes", [700, 350])
        )
        config.local_translation_runtime = runtime
        config.local_part_index = 0
        config.local_ocr_safety_snapshots = {}
        config.max_table_ocr_paragraphs = int(
            babeldoc_cfg.get("max_table_ocr_paragraphs", 200)
        )
        config.max_ocr_blocks_per_table = int(
            babeldoc_cfg.get("max_ocr_blocks_per_table", 100)
        )
        config.max_table_ocr_to_native_ratio = float(
            babeldoc_cfg.get("max_table_ocr_to_native_ratio", 0.5)
        )
        config.ratio_check_min_native_paragraphs = int(
            babeldoc_cfg.get("ratio_check_min_native_paragraphs", 20)
        )
        config.batch_target_source_tokens = int(
            babeldoc_cfg.get("batch_target_source_tokens", 2400)
        )
        config.batch_max_source_tokens = int(
            babeldoc_cfg.get("batch_max_source_tokens", 3200)
        )
        config.batch_max_paragraphs = int(
            babeldoc_cfg.get("batch_max_paragraphs", 40)
        )

        try:
            result = asyncio.run(
                self._consume_translate_events(pdf_id, async_translate, config)
            )
        finally:
            runtime.close()
        audit = audit_translation_completion(
            job_working_dir,
            getattr(config, "local_translation_quality", None),
            lang_out,
            expected_translation=bool(
                getattr(result, "total_valid_character_count", 0) or 0
            ),
        )
        runtime_budget = getattr(runtime, "budget", None)
        runtime_budget_snapshot = (
            runtime_budget.snapshot() if runtime_budget is not None else None
        )
        completed_with_warnings = ensure_translation_complete(
            audit,
            bool(babeldoc_cfg.get("fail_on_unresolved_translation", True)),
            abort_reason=getattr(runtime_budget_snapshot, "abort_reason", None),
        )
        mono_path = result.no_watermark_mono_pdf_path or result.mono_pdf_path
        dual_path = result.no_watermark_dual_pdf_path or result.dual_pdf_path
        if not mono_path:
            raise RuntimeError("BabelDOC did not produce a translation-only PDF")
        if not dual_path:
            raise RuntimeError("BabelDOC did not produce a dual PDF")

        dual_mode = (job.options.get("dual_mode") or "lort").casefold()
        output_audit = None
        if dual_mode in {"lort", "ltro"}:
            output_audit = audit_side_by_side_source(
                source_path,
                dual_path,
                dual_mode,
            )
            ensure_side_by_side_source_preserved(output_audit)

        budget_snapshot = runtime.budget.snapshot()
        ocr_report = getattr(config, "local_ocr_safety_report", None)
        ocr_snapshots = list(
            getattr(config, "local_ocr_safety_snapshots", {}).values()
        )
        native_paragraphs = sum(
            item.native_paragraphs for item in ocr_snapshots
        )
        table_ocr_paragraphs = sum(
            item.table_ocr_paragraphs for item in ocr_snapshots
        )
        image_ocr_paragraphs = sum(
            item.image_ocr_paragraphs for item in ocr_snapshots
        )
        if not ocr_snapshots and ocr_report is not None:
            native_paragraphs = ocr_report.native_paragraphs
            table_ocr_paragraphs = ocr_report.table_ocr_paragraphs
            image_ocr_paragraphs = ocr_report.image_ocr_paragraphs
        runtime.audit_writer.checkpoint(
            {
                "reserved_cost_usd": budget_snapshot.reserved_cost,
                "committed_cost_usd": budget_snapshot.committed_cost,
                "in_flight_requests": budget_snapshot.in_flight_requests,
                "completed_requests": budget_snapshot.completed_requests,
                "abort_reason": budget_snapshot.abort_reason,
                "native_paragraphs": native_paragraphs,
                "table_ocr_paragraphs": table_ocr_paragraphs,
                "image_ocr_paragraphs": image_ocr_paragraphs,
            }
        )
        runtime.audit_writer.publish(job_output_dir)

        self.update_job(
            pdf_id,
            status="success",
            stage="completed",
            progress=100.0,
            message=(
                (
                    "Translation completed with warnings: "
                    f"attempted={audit.attempted_count}, "
                    f"recovered={audit.recovered_count}, "
                    f"source_preserved={audit.source_preserved_count}"
                    if completed_with_warnings
                    else (
                        "Translation audit passed: "
                        f"attempted={audit.attempted_count}, "
                        f"recovered={audit.recovered_count}, unresolved=0"
                    )
                )
                + (
                    f", source_render_pages={output_audit.page_count}"
                    if output_audit is not None
                    else ""
                )
            ),
            translation_pdf_path=str(mono_path),
            dual_pdf_path=str(dual_path),
            total_seconds=float(getattr(result, "total_seconds", 0.0) or 0.0),
        )

    async def _consume_translate_events(self, pdf_id: str, async_translate, config):
        async for event in async_translate(config):
            event_type = event.get("type")
            if event_type in {"progress_start", "progress_update", "progress_end"}:
                progress = float(event.get("overall_progress") or 0.0)
                self.update_job(
                    pdf_id,
                    status="running",
                    stage=event.get("stage") or "processing",
                    progress=progress,
                )
                if progress >= 100:
                    fallback_result = self._make_result_from_output_dir(
                        config.output_dir
                    )
                    if fallback_result is not None:
                        logger.info(
                            "BabelDOC result files are ready before finish event: %s",
                            pdf_id,
                        )
                        return fallback_result
            elif event_type == "error":
                raise RuntimeError(str(event.get("error") or "BabelDOC error"))
            elif event_type == "finish":
                result = event.get("translate_result")
                if result is None:
                    raise RuntimeError("BabelDOC finished without a result")
                return result
        raise RuntimeError("BabelDOC finished without a result")

    def _make_result_from_output_dir(self, output_dir: str | Path):
        output_path = Path(output_dir)
        mono_path = self._latest_pdf(output_path, "*.mono.pdf")
        dual_path = self._latest_pdf(output_path, "*.dual.pdf")
        if not mono_path or not dual_path:
            return None
        return SimpleNamespace(
            no_watermark_mono_pdf_path=mono_path,
            mono_pdf_path=None,
            no_watermark_dual_pdf_path=dual_path,
            dual_pdf_path=None,
            total_seconds=0.0,
        )

    @staticmethod
    def _latest_pdf(output_dir: Path, pattern: str) -> Path | None:
        candidates = [
            path
            for path in output_dir.glob(pattern)
            if path.is_file() and path.stat().st_size > 0
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)


class LocalBabelDOCServer(ThreadingHTTPServer):
    def __init__(self, server_address, state: AppState):
        super().__init__(server_address, LocalBabelDOCHandler)
        self.state = state


class LocalBabelDOCHandler(BaseHTTPRequestHandler):
    server: LocalBabelDOCServer

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        try:
            self._handle_get()
        except Exception as exc:
            logger.exception("GET failed: %s", self.path)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:
        try:
            self._handle_post()
        except Exception as exc:
            logger.exception("POST failed: %s", self.path)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_PUT(self) -> None:
        try:
            self._handle_put()
        except Exception as exc:
            logger.exception("PUT failed: %s", self.path)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def log_message(self, fmt, *args) -> None:
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def _normalized_path(self) -> str:
        path = urlparse(self.path).path
        if path == "/zotero":
            return "/"
        if path.startswith("/zotero/"):
            return path[len("/zotero") :]
        return path

    def _handle_get(self) -> None:
        parsed = urlparse(self.path)
        path = self._normalized_path()

        if path == "/healthz":
            self._send_json(
                HTTPStatus.OK,
                {"status": "ok", "backend_build": BACKEND_BUILD},
            )
            return

        if not self.server.state.is_authorized(self):
            self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return

        if path == "/check-key":
            self._send_ok(True)
            return

        if path == "/pdf-upload-url":
            object_key = f"{uuid.uuid4().hex}.pdf"
            upload_url = self.server.state.make_public_url(
                self,
                f"upload/{quote(object_key)}",
                signed=True,
            )
            self._send_ok(
                {
                    "result": {
                        "objectKey": object_key,
                        "preSignedURL": upload_url,
                        "imgUrl": "",
                    },
                    "id": int(time.time() * 1000),
                    "exception": "",
                    "status": "ok",
                    "isCanceled": False,
                    "isCompleted": False,
                    "isCompletedSuccessfully": False,
                    "creationOptions": 0,
                    "asyncState": None,
                    "isFaulted": False,
                }
            )
            return

        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "pdf" and parts[2] == "process":
            self._send_process_status(parts[1])
            return

        if len(parts) == 3 and parts[0] == "pdf" and parts[2] == "temp-url":
            self._send_temp_urls(parts[1])
            return

        if len(parts) == 3 and parts[0] == "download":
            self._send_download(parts[1], parts[2])
            return

        self._send_error(HTTPStatus.NOT_FOUND, f"not found: {parsed.path}")

    def _handle_post(self) -> None:
        path = self._normalized_path()
        if not self.server.state.is_authorized(self):
            self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return

        if path == "/test-model":
            body = self._read_json_body()
            model_key = str(body.get("requestModel") or "")
            target_language = str(body.get("targetLanguage") or "zh-CN")
            model_config = (
                body.get("modelConfig")
                if isinstance(body.get("modelConfig"), dict)
                else None
            )
            result = self.server.state.test_model(
                model_key,
                normalize_lang_out(target_language),
                model_config,
            )
            self._send_ok(
                {
                    "model": model_key,
                    "message": "model API is reachable",
                    "translation": result["translation"],
                }
            )
            return

        if path == "/backend-babel-pdf":
            body = self._read_json_body()
            object_key = safe_object_key(str(body.get("objectKey", "")))
            upload_path = self.server.state.get_upload_path(object_key)
            if not upload_path.exists():
                self._send_error(
                    HTTPStatus.BAD_REQUEST,
                    f"uploaded PDF not found for objectKey: {object_key}",
                )
                return
            pdf_id = uuid.uuid4().hex
            job = Job(
                pdf_id=pdf_id,
                object_key=object_key,
                file_name=str(body.get("fileName") or object_key),
                request_model=str(body.get("requestModel") or ""),
                target_language=str(body.get("targetLanguage") or "zh-CN"),
                model_config=body.get("modelConfig")
                if isinstance(body.get("modelConfig"), dict)
                else None,
                options=body,
                created_at=time.time(),
            )
            self.server.state.add_job(job)
            self.server.state.start_job(pdf_id)
            self._send_ok(pdf_id)
            return

        self._send_error(HTTPStatus.NOT_FOUND, f"not found: {path}")

    def _handle_put(self) -> None:
        path = self._normalized_path()
        if not self.server.state.is_authorized(self):
            self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
            return

        if path.startswith("/upload/"):
            object_key = safe_object_key(path[len("/upload/") :])
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0:
                self._send_error(HTTPStatus.BAD_REQUEST, "empty upload")
                return
            data = self.rfile.read(length)
            upload_path = self.server.state.get_upload_path(object_key)
            upload_path.write_bytes(data)
            self.send_response(HTTPStatus.OK)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")
            return

        self._send_error(HTTPStatus.NOT_FOUND, f"not found: {path}")

    def _send_process_status(self, pdf_id: str) -> None:
        job = self.server.state.get_job_or_recover_finished(pdf_id)
        if not job:
            self._send_error(HTTPStatus.NOT_FOUND, f"job not found: {pdf_id}")
            return
        if job.status == "success":
            status = "ok"
            progress = 100.0
        elif job.status == "failed":
            status = "failed"
            progress = job.progress
        else:
            status = ""
            progress = job.progress
        self._send_ok(
            {
                "overall_progress": progress,
                "currentStageName": job.stage,
                "status": status,
                "message": job.message,
                "num_pages": 0,
            }
        )

    def _send_temp_urls(self, pdf_id: str) -> None:
        job = self.server.state.get_job_or_recover_finished(pdf_id)
        if not job:
            self._send_error(HTTPStatus.NOT_FOUND, f"job not found: {pdf_id}")
            return
        if job.status != "success":
            self._send_error(HTTPStatus.CONFLICT, "job is not complete")
            return
        self._send_ok(
            {
                "translationOnlyPdfOssUrl": self.server.state.make_public_url(
                    self,
                    f"download/{quote(pdf_id)}/translation",
                    signed=True,
                ),
                "translationDualPdfOssUrl": self.server.state.make_public_url(
                    self,
                    f"download/{quote(pdf_id)}/dual",
                    signed=True,
                ),
                "waterMask": False,
                "monoFileUrl": self.server.state.make_public_url(
                    self,
                    f"download/{quote(pdf_id)}/translation",
                    signed=True,
                ),
            }
        )

    def _send_download(self, pdf_id: str, kind: str) -> None:
        job = self.server.state.get_job_or_recover_finished(pdf_id)
        if not job:
            self._send_error(HTTPStatus.NOT_FOUND, f"job not found: {pdf_id}")
            return
        if kind == "translation":
            file_path = Path(job.translation_pdf_path)
        elif kind == "dual":
            file_path = Path(job.dual_pdf_path)
        else:
            self._send_error(HTTPStatus.NOT_FOUND, f"unknown result kind: {kind}")
            return
        if not file_path.exists():
            self._send_error(HTTPStatus.NOT_FOUND, "result file not found")
            return

        content = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._send_cors_headers()
        self.send_header(
            "Content-Type",
            mimetypes.guess_type(file_path.name)[0] or "application/pdf",
        )
        self.send_header("Content-Length", str(len(content)))
        self.send_header(
            "Content-Disposition",
            f"attachment; filename={json.dumps(file_path.name)}",
        )
        self.end_headers()
        self.wfile.write(content)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        data = self.rfile.read(length)
        return json.loads(data.decode("utf-8"))

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")

    def _send_json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_ok(self, data: Any) -> None:
        self._send_json(HTTPStatus.OK, {"code": 0, "data": data})

    def _send_error(self, status: int, message: str) -> None:
        self._send_json(status, {"code": 1, "message": message})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local BabelDOC server for Zotero")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("config.local.json"),
        help="JSON config path. If it does not exist, defaults are used.",
    )
    parser.add_argument("--host", help="Override server host")
    parser.add_argument("--port", type=int, help="Override server port")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--reset-data",
        action="store_true",
        help="Delete local uploads, working files, and outputs before starting.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    normalize_proxy_env()
    config = load_config(args.config)
    if args.host:
        config["server"]["host"] = args.host
    if args.port:
        config["server"]["port"] = args.port

    data_dir = resolve_repo_relative_path(config["babeldoc"]["data_dir"])
    if args.reset_data and data_dir.exists():
        shutil.rmtree(data_dir)

    state = AppState(config)
    host = config["server"]["host"]
    port = int(config["server"]["port"])
    server = LocalBabelDOCServer((host, port), state)
    start_zotero_parent_watchdog(server)
    logger.info("Local BabelDOC server listening on http://%s:%s/zotero", host, port)
    logger.info("BabelDOC repo: %s", state.babeldoc_repo)
    logger.info("Data dir: %s", state.data_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping local BabelDOC server")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
