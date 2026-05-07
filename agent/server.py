from pathlib import Path

import asyncio
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict
from typing import Optional
import uvicorn

try:
    from .config import load_project_env
except ImportError:  # Allows local script execution from the package directory.
    from config import load_project_env

load_project_env()


try:
    from .main import update_sheet, run_custom_task, run_workflow
except ImportError:  # Allows local script execution from the package directory.
    from main import update_sheet, run_custom_task, run_workflow

FRONTEND_HTML = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


async def _run_agent_task(coro_func, *args, **kwargs):
    """Run an async agent function in a thread with its own event loop.

    On Windows the default uvicorn loop is SelectorEventLoop which cannot
    spawn subprocesses (needed by Playwright).  Running in a dedicated
    thread with ProactorEventLoop solves this.
    """
    def _thread_target():
        if sys.platform.startswith("win"):
            loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(coro_func(*args, **kwargs))
            finally:
                loop.close()
        else:
            return asyncio.run(coro_func(*args, **kwargs))

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _thread_target)

app = FastAPI(title="Browser-Use Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

class RunPayload(BaseModel):
    value: str
    email: Optional[str] = None
    password: Optional[str] = None
    sheet_id: Optional[str] = None
    sheet_url: Optional[str] = None


class CustomTaskPayload(BaseModel):
    task: str
    url: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None


class WorkflowRunPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    if FRONTEND_HTML.is_file():
        return HTMLResponse(content=FRONTEND_HTML.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Frontend not found</h1>", status_code=404)

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.post("/run")
async def run_agent(payload: RunPayload):
    try:
        result = await _run_agent_task(update_sheet, payload.model_dump())
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("message"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/run-workflow")
async def run_workflow_endpoint(payload: WorkflowRunPayload):
    try:
        result = await _run_agent_task(run_workflow, payload.model_dump())
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("message"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/run-task")
async def run_custom(payload: CustomTaskPayload):
    try:
        result = await _run_agent_task(run_custom_task, payload.model_dump())
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("message"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("agent.server:app", host="0.0.0.0", port=8002, reload=True)
