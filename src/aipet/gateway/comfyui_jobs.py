"""Background ComfyUI image job orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse
from typing import Any

from aipet.gateway.models import Message

logger = logging.getLogger(__name__)
@dataclass
class ComfyUIJob:
    job_id: str
    session_id: str
    prompt: str
    negative_prompt: str
    width: int
    height: int
    workflow: str
    seed: int = -1
    steps: int = -1
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    prompt_id: str | None = None
    file_url: str | None = None
    error: str | None = None


class ComfyUIJobManager:
    """Run long image-generation jobs in the background and push updates."""

    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway
        self._jobs: dict[str, ComfyUIJob] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    async def submit(self, session_id: str, payload: dict[str, Any]) -> ComfyUIJob:
        workflow = str(payload.get("workflow", "z_image_turbo"))
        prompt = payload.get("prompt", "")
        width = int(payload.get("width", 1024))
        height = int(payload.get("height", 1024))
        # Deduplicate: if the same session already has a running/queued job
        # with the same workflow AND the same core parameters, return the
        # existing one instead of spawning another background task.
        # Different prompts (e.g. "draw a cat" vs "draw a dog") are allowed
        # to run concurrently.
        for existing in self._jobs.values():
            if (
                existing.session_id == session_id
                and existing.workflow == workflow
                and existing.prompt == prompt
                and existing.width == width
                and existing.height == height
                and existing.status in ("queued", "running")
            ):
                logger.info(
                    "ComfyUI job deduplicated",
                    extra={
                        "session_id": session_id,
                        "workflow": workflow,
                        "existing_job_id": existing.job_id,
                    },
                )
                return existing

        job = ComfyUIJob(
            job_id=f"imgjob_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            prompt=payload["prompt"],
            negative_prompt=payload.get("negative_prompt", ""),
            width=int(payload.get("width", 1024)),
            height=int(payload.get("height", 1024)),
            workflow=workflow,
            seed=int(payload.get("seed", -1)),
            steps=int(payload.get("steps", -1)),
        )
        self._jobs[job.job_id] = job
        await self._emit_status(job)
        self._tasks[job.job_id] = asyncio.create_task(self._run(job))
        return job

    async def _run(self, job: ComfyUIJob) -> None:
        import importlib
        comfyui_skill = importlib.import_module("data.skills.comfyui")

        try:
            job.status = "running"
            job.updated_at = time.time()
            await self._emit_status(job)

            file_url = await comfyui_skill.generate_image_sync_result(
                prompt=job.prompt,
                negative_prompt=job.negative_prompt,
                width=job.width,
                height=job.height,
                seed=job.seed,
                steps=job.steps,
                workflow=job.workflow,
                progress_callback=lambda stage, details=None: asyncio.create_task(
                    self._emit_progress(job, stage, details or {})
                ),
            )

            job.status = "completed"
            job.file_url = file_url
            job.updated_at = time.time()
            await self._emit_status(job)
            await self._publish_completion(job)
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            job.updated_at = time.time()
            logger.error("ComfyUI job failed", exc_info=True, extra={
                "job_id": job.job_id,
                "workflow": job.workflow,
                "session_id": job.session_id,
                "error": job.error,
            })
            await self._emit_status(job)
            await self._publish_failure(job)
        finally:
            self._tasks.pop(job.job_id, None)

    async def _emit_status(self, job: ComfyUIJob) -> None:
        await self.gateway._broadcast(
            {
                "type": "event",
                "method": "image.job.status",
                "payload": {
                    "session_id": job.session_id,
                    "job_id": job.job_id,
                    "status": job.status,
                    "workflow": job.workflow,
                    "prompt": job.prompt,
                    "file_url": job.file_url,
                    "error": job.error,
                },
            }
        )

    async def _emit_progress(self, job: ComfyUIJob, stage: str, details: dict[str, Any]) -> None:
        await self.gateway._broadcast(
            {
                "type": "event",
                "method": "image.job.progress",
                "payload": {
                    "session_id": job.session_id,
                    "job_id": job.job_id,
                    "status": job.status,
                    "stage": stage,
                    "details": details,
                    "workflow": job.workflow,
                },
            }
        )

    async def _publish_completion(self, job: ComfyUIJob) -> None:
        if not job.file_url:
            return

        local_src = job.file_url
        if job.file_url.startswith("file://"):
            parsed = urlparse(job.file_url)
            local_src = unquote(parsed.path.lstrip("/"))

        markdown = (
            f"图片生成完成啦！\n\n"
            f"![生成的图片]({job.file_url})\n\n"
            f"工作流：`{job.workflow}`"
        )
        msg = Message(role="assistant", content=markdown)
        await self.gateway.sessions.add_message(job.session_id, msg)
        await self.gateway._broadcast(
            {
                "type": "event",
                "method": "chat.message",
                "payload": {
                    "session_id": job.session_id,
                    "message_id": msg.id,
                    "role": "assistant",
                    "content": msg.content,
                    "timestamp": msg.created_at.isoformat(),
                },
            }
        )
        await self.gateway._broadcast(
            {
                "type": "event",
                "method": "canvas.show",
                "payload": self.gateway.canvas.to_dict(
                    self.gateway.canvas.create(
                        canvas_type="image",
                        data={
                            "src": local_src,
                            "caption": f"ComfyUI 已完成：{job.workflow}",
                        },
                        title="图片生成完成",
                        width=320,
                    )
                ),
            }
        )

    async def _publish_failure(self, job: ComfyUIJob) -> None:
        msg = Message(
            role="assistant",
            content=(
                f"图片后台生成失败了。\n\n工作流：`{job.workflow}`\n错误：{job.error or '未知错误'}"
            ),
        )
        await self.gateway.sessions.add_message(job.session_id, msg)
        await self.gateway._broadcast(
            {
                "type": "event",
                "method": "chat.message",
                "payload": {
                    "session_id": job.session_id,
                    "message_id": msg.id,
                    "role": "assistant",
                    "content": msg.content,
                    "timestamp": msg.created_at.isoformat(),
                },
            }
        )

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._tasks.values()):
            with contextlib.suppress(asyncio.CancelledError):
                await task
