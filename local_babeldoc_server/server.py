#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import mimetypes
import os
import shutil
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
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
DEFAULT_DATA_DIR = REPO_ROOT / ".local-babeldoc"
DEFAULT_BABELDOC_REPO = REPO_ROOT / "BabelDOC"

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
        "base_url": "",
        "api_key": "env:DEEPSEEK_API_KEY",
        "model": "",
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
        "base_url": "",
        "api_key": "env:GEMINI_API_KEY",
        "model": "",
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
    },
    "models": MODEL_DEFAULTS,
}

logger = logging.getLogger("local_babeldoc_server")

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

    def _create_translator(
        self,
        model_key: str,
        lang_out: str,
        model_config: dict[str, Any] | None = None,
    ):
        from babeldoc.translator.translator import OpenAITranslator

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
        missing = [
            name
            for name, value in (
                ("base_url", base_url),
                ("api_key", api_key),
                ("model", model),
            )
            if not value
        ]
        if missing:
            label = model_cfg.get("label", model_key)
            raise ValueError(
                f"model '{label}' ({model_key}) is missing: {', '.join(missing)}"
            )

        babeldoc_cfg = self.config["babeldoc"]
        return OpenAITranslator(
            lang_in=babeldoc_cfg.get("lang_in", "en"),
            lang_out=lang_out,
            model=model,
            base_url=base_url,
            api_key=api_key,
            ignore_cache=bool(model_cfg.get("ignore_cache", False)),
            enable_json_mode_if_requested=bool(
                babeldoc_cfg.get("enable_json_mode_if_requested", False)
            ),
            send_dashscope_header=bool(
                babeldoc_cfg.get("send_dashscope_header", False)
            ),
            send_temperature=bool(babeldoc_cfg.get("send_temperature", True)),
            reasoning=model_cfg.get("reasoning"),
        )

    def _run_babeldoc(self, pdf_id: str) -> None:
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
        translator = self._create_translator(
            job.request_model,
            lang_out,
            job.model_config,
        )
        qps = int(babeldoc_cfg.get("qps") or 4)
        set_translate_rate_limiter(qps)

        job_output_dir = self.output_dir / pdf_id
        job_working_dir = self.working_dir / pdf_id
        job_output_dir.mkdir(parents=True, exist_ok=True)
        job_working_dir.mkdir(parents=True, exist_ok=True)

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
            term_extraction_translator=translator,
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
            disable_same_text_fallback=bool(
                babeldoc_cfg.get("disable_same_text_fallback", False)
            ),
            metadata_extra_data=f"local_zotero_{pdf_id}",
        )

        result = asyncio.run(self._consume_translate_events(pdf_id, async_translate, config))
        mono_path = result.no_watermark_mono_pdf_path or result.mono_pdf_path
        dual_path = result.no_watermark_dual_pdf_path or result.dual_pdf_path
        if not mono_path:
            raise RuntimeError("BabelDOC did not produce a translation-only PDF")
        if not dual_path:
            raise RuntimeError("BabelDOC did not produce a dual PDF")

        self.update_job(
            pdf_id,
            status="success",
            stage="completed",
            progress=100.0,
            message="",
            translation_pdf_path=str(mono_path),
            dual_pdf_path=str(dual_path),
            total_seconds=float(getattr(result, "total_seconds", 0.0) or 0.0),
        )

    async def _consume_translate_events(self, pdf_id: str, async_translate, config):
        result = None
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
            self._send_json(HTTPStatus.OK, {"status": "ok"})
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
            translator = self.server.state._create_translator(
                model_key,
                normalize_lang_out(target_language),
                model_config,
            )
            translated = (translator.do_translate("Hello.") or "").strip()
            if not translated:
                raise RuntimeError(
                    f"model '{model_key}' returned an empty response during test"
                )
            self._send_ok(
                {
                    "model": model_key,
                    "message": "model API is reachable",
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
        job = self.server.state.get_job(pdf_id)
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
        job = self.server.state.get_job(pdf_id)
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
        job = self.server.state.get_job(pdf_id)
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
