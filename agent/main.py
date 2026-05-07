import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

try:
    from .config import load_project_env
except ImportError:
    from config import load_project_env

# Load env before importing libraries that may inspect configuration on import.
load_project_env()

from browser_use import Agent, BrowserProfile, BrowserSession
from browser_use.llm.exceptions import ModelProviderError
from browser_use.llm.messages import AssistantMessage, BaseMessage, SystemMessage, UserMessage
from browser_use.llm.views import ChatInvokeCompletion, ChatInvokeUsage
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langchain_core.messages import AIMessage as LCAIMessage
from langchain_core.messages import HumanMessage, SystemMessage as LCSystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)


class _PasswordRedactFilter(logging.Filter):
    """Mask passwords that may appear in log records."""

    def __init__(self) -> None:
        super().__init__()
        self._patterns: list[str] = []

    def add_password(self, password: str | None) -> None:
        if password and password not in self._patterns:
            self._patterns.append(password)

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pwd in self._patterns:
            msg = msg.replace(pwd, "***REDACTED***")
        record.msg = msg
        record.args = ()
        return True


_password_filter = _PasswordRedactFilter()

if not logging.getLogger().handlers:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(_password_filter)
    for handler in logging.getLogger("browser_use").handlers:
        handler.addFilter(_password_filter)
    # Also add filter to browser_use logger even if handlers are not yet attached
    logging.getLogger("browser_use").addFilter(_password_filter)

RUN_TIMEOUT_SECONDS = int(os.getenv("AGENT_RUN_TIMEOUT_SECONDS", "600"))
PAGE_LOAD_WAIT_SECONDS = float(os.getenv("PAGE_LOAD_WAIT_SECONDS", "5"))
GOOGLE_SHEET_ID_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

ACCESS_DENIED_MARKERS = (
    "403",
    "access denied",
    "you need access",
    "request access",
    "you don't have access",
    "you need permission",
    "sign in with a different account",
)

LOGIN_MARKERS = (
    "accounts.google.com",
    "sign in",
    "use your google account",
    "enter your password",
    "choose an account",
)


class PagePreflight(BaseModel):
    url: str
    title: str
    page_text: str


class PreflightError(RuntimeError):
    pass


class WorkflowIntent(BaseModel):
    model_config = ConfigDict(extra="allow")

    action: str
    target: str | None = None
    value: str | None = None
    context: str | None = None


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int | str | None = None
    title: str
    url_hint: str | None = None
    intents: list[WorkflowIntent] = Field(default_factory=list)
    verification: str | None = None


class WorkflowPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str | None = None
    title: str
    starting_url: str
    auth_required: bool = False
    auth_hint: str | None = None
    steps: list[WorkflowStep] = Field(default_factory=list)
    email: str | None = None
    password: str | None = None


@dataclass
class BrowserUseChatNVIDIA:
    model: str
    api_key: str

    def __post_init__(self) -> None:
        self._llm = ChatNVIDIA(model=self.model, api_key=self.api_key)

    @property
    def provider(self) -> str:
        return "nvidia"

    @property
    def name(self) -> str:
        return self.model

    @property
    def model_name(self) -> str:
        return self.model

    def _to_langchain_message(self, message: BaseMessage) -> Any:
        content = message.content

        if isinstance(content, list):
            normalized_content = []
            for part in content:
                if part.type == "text":
                    normalized_content.append({"type": "text", "text": part.text})
                elif part.type == "image_url":
                    normalized_content.append(
                        {"type": "image_url", "image_url": {"url": part.image_url.url}}
                    )
                elif part.type == "refusal":
                    normalized_content.append({"type": "text", "text": part.refusal})
            content = normalized_content

        if isinstance(message, SystemMessage):
            return LCSystemMessage(content=content)
        if isinstance(message, AssistantMessage):
            return LCAIMessage(content=content or "")
        if isinstance(message, UserMessage):
            return HumanMessage(content=content)

        return HumanMessage(content=str(content))

    @staticmethod
    def _extract_text(response: Any) -> str:
        content = getattr(response, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
            return "".join(parts)
        return str(content)

    @staticmethod
    def _extract_usage(response: Any) -> ChatInvokeUsage | None:
        usage = getattr(response, "usage_metadata", None) or getattr(response, "usage", None)
        if not usage:
            return None

        prompt_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
        completion_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or 0
        total_tokens = usage.get("total_tokens") or (prompt_tokens + completion_tokens)

        return ChatInvokeUsage(
            prompt_tokens=prompt_tokens,
            prompt_cached_tokens=usage.get("prompt_cached_tokens"),
            prompt_cache_creation_tokens=usage.get("prompt_cache_creation_tokens"),
            prompt_image_tokens=usage.get("prompt_image_tokens"),
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    async def ainvoke(
        self, messages: list[BaseMessage], output_format: type[Any] | None = None, **kwargs: Any
    ) -> ChatInvokeCompletion[Any]:
        lc_messages = [self._to_langchain_message(message) for message in messages]
        llm = self._llm.with_structured_output(output_format) if output_format else self._llm
        filtered_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key not in {"session_id"}
        }
        response = await llm.ainvoke(lc_messages, **filtered_kwargs)

        if output_format is not None:
            return ChatInvokeCompletion(completion=response, usage=None, stop_reason=None)

        return ChatInvokeCompletion(
            completion=self._extract_text(response),
            usage=self._extract_usage(response),
            stop_reason=getattr(response, "response_metadata", {}).get("finish_reason"),
        )


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    normalized = haystack.lower()
    return any(needle in normalized for needle in needles)


def _format_workflow_intent(intent: WorkflowIntent) -> str:
    action = intent.action.strip().lower()
    target = intent.target or "the relevant element"
    context = f" ({intent.context})" if intent.context else ""

    if action == "observe":
        return f"- Observe {target}{context} to confirm it is visible."
    if action == "type":
        if intent.value:
            return f"- Click {target}{context} and type {intent.value!r}."
        return f"- Click {target}{context} and type the required text."
    if action == "press":
        key_label = intent.target or intent.value or "the required key"
        return f"- Press {key_label}{context}."

    value = f" with value {intent.value!r}" if intent.value else ""
    return f"- Perform '{intent.action}' on {target}{context}{value}."


def _build_workflow_task(workflow: WorkflowPayload) -> str:
    lines = [
        "You are a browser automation agent. Execute the workflow steps exactly in order.",
        "Do not skip or reorder steps. If any step fails, stop and report the failure.",
        f"Workflow title: {workflow.title}.",
    ]

    if workflow.run_id:
        lines.append(f"Run ID: {workflow.run_id}.")

    lines.append(f"Starting URL: {workflow.starting_url}. Navigate there first.")

    if workflow.auth_required:
        lines.append("Authentication is required for this workflow.")
        if workflow.auth_hint:
            lines.append(f"Auth hint: {workflow.auth_hint}")
        lines.append("If credentials are provided in sensitive_data, use them to sign in.")

    for index, step in enumerate(workflow.steps, start=1):
        step_id = step.id if step.id is not None else index
        lines.append("")
        lines.append(f"Step {step_id}: {step.title}")
        if step.url_hint:
            lines.append(f"- If you are not on {step.url_hint}, navigate there.")
        if step.intents:
            lines.append("Actions:")
            for intent in step.intents:
                lines.append(_format_workflow_intent(intent))
        if step.verification:
            lines.append(f"Verification: {step.verification}")

    return "\n".join(lines).strip()


def _resolve_sheet_id(payload: dict) -> str | None:
    sheet_id = payload.get("sheet_id")
    if sheet_id:
        return str(sheet_id).strip()

    sheet_url = payload.get("sheet_url")
    if sheet_url is not None:
        match = GOOGLE_SHEET_ID_PATTERN.search(str(sheet_url).strip())
        if match:
            return match.group(1)
        return None

    env_sheet_id = os.getenv("SHEET_ID")
    if env_sheet_id:
        return env_sheet_id.strip()
    return None


async def _collect_page_preflight(browser_session: BrowserSession) -> PagePreflight:
    page = await browser_session.get_current_page()
    if page is None:
        raise PreflightError("Browser page was not available after startup")

    await asyncio.sleep(PAGE_LOAD_WAIT_SECONDS)

    url = await page.get_url()
    title = await page.get_title()
    logger.info("Preflight page snapshot: url=%s title=%s", url, title)

    try:
        page_text = await page._extract_clean_markdown()
        if isinstance(page_text, tuple):
            page_text = page_text[0]
    except Exception as exc:
        logger.warning("Preflight markdown extraction failed: %s", exc)
        page_text = ""

    return PagePreflight(url=url, title=title, page_text=page_text[:12000])


def _validate_preflight(
    preflight: PagePreflight, sheet_url: str, email: str | None, password: str | None
) -> None:
    combined = "\n".join([preflight.url, preflight.title, preflight.page_text])
    has_login = _contains_any(combined, LOGIN_MARKERS)
    has_access_denied = _contains_any(combined, ACCESS_DENIED_MARKERS)
    has_credentials = bool(email and password)

    # Log what we see so debugging is easier
    logger.info(
        "Preflight validation: url=%s login_markers=%s access_denied_markers=%s has_credentials=%s",
        preflight.url,
        has_login,
        has_access_denied,
        has_credentials,
    )

    # If we see login markers and have credentials, let the agent handle login.
    # Don't treat this as a preflight failure.
    if has_login and has_credentials:
        logger.info("Login page detected and credentials are available; letting agent handle authentication.")
        return

    # If login is required but we have no credentials, that's a real failure.
    if has_login and not has_credentials:
        raise PreflightError(
            "Google sign-in is required for this sheet, but GOOGLE_EMAIL or GOOGLE_PASSWORD is missing."
        )

    # Only treat as access-denied if we are NOT on a login page.
    if has_access_denied:
        raise PreflightError(
            "Google Sheets returned an access-denied page for this sheet. "
            "Confirm that the account configured in GOOGLE_EMAIL has permission to open the document."
        )

    if "docs.google.com" not in preflight.url and "accounts.google.com" not in preflight.url:
        raise PreflightError(
            f"Unexpected page loaded during preflight: {preflight.url or 'unknown URL'}"
        )


def _create_agent(
    task_description: str,
    sensitive_data: dict[str, str],
    nvidia_key: str,
) -> tuple[Agent, BrowserSession]:
    llm = BrowserUseChatNVIDIA(
        model="meta/llama-3.3-70b-instruct",
        api_key=nvidia_key,
    )
    cdp_url = os.getenv("BROWSER_CDP_URL")
    user_data_dir = os.getenv("BROWSER_USER_DATA_DIR")
    profile_directory = os.getenv("BROWSER_PROFILE_DIRECTORY")
    demo_mode = _env_bool("BROWSER_DEMO_MODE", True)
    headless = _env_bool("BROWSER_HEADLESS", False)
    browser_profile = BrowserProfile(
        headless=headless,
        demo_mode=demo_mode,
        cdp_url=cdp_url or None,
        user_data_dir=user_data_dir or None,
        profile_directory=profile_directory or "Default",
    )
    browser_session = BrowserSession(browser_profile=browser_profile)
    logger.info(
        "Configured browser session: headless=%s demo_mode=%s use_vision=%s timeout_seconds=%s",
        browser_profile.headless,
        browser_profile.demo_mode,
        False,
        RUN_TIMEOUT_SECONDS,
    )
    agent = Agent(
        task=task_description,
        llm=llm,
        use_vision=False,
        browser_session=browser_session,
        demo_mode=demo_mode,
        sensitive_data=sensitive_data,
    )
    return agent, browser_session


async def update_sheet(payload: dict) -> dict:
    value = payload.get("value")
    email = payload.get("email") or os.getenv("GOOGLE_EMAIL")
    password = payload.get("password") or os.getenv("GOOGLE_PASSWORD")
    sheet_id = _resolve_sheet_id(payload)
    
    if not value:
        return {"status": "error", "message": "Missing value"}
    if not sheet_id:
        return {
            "status": "error",
            "message": "Missing or invalid Google Sheet link/sheet_id",
        }

    logger.info(
        "Starting sheet update run: sheet_id_present=%s email_present=%s value_length=%s",
        bool(sheet_id),
        bool(email),
        len(str(value)),
    )
    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"

    # Redact password from all log output before any task execution
    if password:
        _password_filter.add_password(password)

    # Define the task for the browser-use agent
    # Credentials are passed via sensitive_data so they do not appear in prompts/logs.
    task_description = f"""
    Navigate to {sheet_url}.
    If you see a "Sign in" button (top right), click it first.
    If you see a Google sign-in or login page, complete the login flow:
    - Enter the email from sensitive_data into the email field and click Next.
    - Enter the password from sensitive_data into the password field and click Next.
    - If you are asked to choose an account, pick the email from sensitive_data.
    After login, wait for the spreadsheet grid to load.
    If the page still shows an access-denied or request-access message AFTER attempting login, then stop and report that the account does not have permission.
    Do not type into account chips, avatar buttons, or unrelated controls.
    Only proceed once the spreadsheet grid is visible.
    Once the Google Sheet is fully loaded, find the first empty cell in column A.
    Click the empty cell, type the value '{value}', and press Enter to save it.
    Verify that the value appears in column A after saving.
    """

    # Uses NVIDIA's free NIM-hosted Llama 3.1 model
    # Get your free API key at: https://build.nvidia.com/settings/api-keys
    nvidia_key = os.getenv("NVIDIA_API_KEY")
    if not nvidia_key:
        return {"status": "error", "message": "NVIDIA_API_KEY is not set in environment"}
    sensitive_data: dict[str, str] = {}
    if email and password:
        sensitive_data = {
            "google_email": email,
            "google_password": password,
        }
    agent, browser_session = _create_agent(task_description, sensitive_data, nvidia_key)

    try:
        logger.info("Starting browser session preflight")
        await browser_session.start()
        page = await browser_session.get_current_page()
        if page is None:
            raise PreflightError("Browser page was not available after browser startup")
        await page.goto(sheet_url)
        preflight = await _collect_page_preflight(browser_session)
        _validate_preflight(preflight, sheet_url, email, password)
        logger.info("Preflight succeeded; handing off to browser-use agent")
        logger.info("Launching browser-use agent run")
        result = await asyncio.wait_for(agent.run(), timeout=RUN_TIMEOUT_SECONDS)
        logger.info("Agent run completed")
        return {"status": "ok", "result": str(result)}
    except PreflightError as e:
        logger.exception("Preflight failed")
        return {"status": "error", "message": str(e)}
    except asyncio.TimeoutError:
        logger.exception("Agent run timed out after %s seconds", RUN_TIMEOUT_SECONDS)
        return {
            "status": "error",
            "message": f"Agent run timed out after {RUN_TIMEOUT_SECONDS} seconds",
        }
    except ModelProviderError as e:
        logger.exception("LLM provider error")
        return {
            "status": "error",
            "message": f"NVIDIA API is temporarily overloaded or unavailable. Try again in a few minutes. Details: {e}",
        }
    except Exception as e:
        logger.exception("Agent run failed")
        return {"status": "error", "message": str(e)}
    finally:
        try:
            await browser_session.stop()
        except Exception:
            logger.exception("Browser session cleanup failed")


async def run_workflow(payload: dict) -> dict:
    try:
        workflow = WorkflowPayload.model_validate(payload)
    except ValidationError as exc:
        return {"status": "error", "message": f"Invalid workflow payload: {exc}"}

    email = workflow.email or os.getenv("WORKFLOW_EMAIL") or os.getenv("GOOGLE_EMAIL")
    password = workflow.password or os.getenv("WORKFLOW_PASSWORD") or os.getenv("GOOGLE_PASSWORD")

    if password:
        _password_filter.add_password(password)

    task_description = _build_workflow_task(workflow)

    nvidia_key = os.getenv("NVIDIA_API_KEY")
    if not nvidia_key:
        return {"status": "error", "message": "NVIDIA_API_KEY is not set in environment"}

    sensitive_data: dict[str, str] = {}
    if email and password:
        sensitive_data = {
            "login_email": email,
            "login_password": password,
        }

    agent, browser_session = _create_agent(task_description, sensitive_data, nvidia_key)

    try:
        logger.info("Starting browser session for workflow run: %s", workflow.title)
        await browser_session.start()
        page = await browser_session.get_current_page()
        if page is None:
            raise PreflightError("Browser page was not available after browser startup")
        if workflow.starting_url:
            await page.goto(workflow.starting_url)
        result = await asyncio.wait_for(agent.run(), timeout=RUN_TIMEOUT_SECONDS)
        logger.info("Workflow run completed")
        response = {"status": "ok", "result": str(result)}
        if workflow.run_id:
            response["run_id"] = workflow.run_id
        return response
    except asyncio.TimeoutError:
        logger.exception("Workflow run timed out after %s seconds", RUN_TIMEOUT_SECONDS)
        return {
            "status": "error",
            "message": f"Workflow run timed out after {RUN_TIMEOUT_SECONDS} seconds",
        }
    except ModelProviderError as e:
        logger.exception("LLM provider error")
        return {
            "status": "error",
            "message": f"NVIDIA API is temporarily overloaded or unavailable. Try again in a few minutes. Details: {e}",
        }
    except Exception as e:
        logger.exception("Workflow run failed")
        return {"status": "error", "message": str(e)}
    finally:
        try:
            await browser_session.stop()
        except Exception:
            logger.exception("Browser session cleanup failed")


async def run_custom_task(payload: dict) -> dict:
    task = payload.get("task")
    url = payload.get("url")
    email = payload.get("email")
    password = payload.get("password")

    if not task:
        return {"status": "error", "message": "Missing task"}

    # Redact password from all log output before any task execution
    if password:
        _password_filter.add_password(password)

    instructions: list[str] = []
    if url:
        instructions.append(f"Navigate to {url}.")
    instructions.append(
        "If a login is required, use the credentials stored in sensitive_data to sign in."
    )
    instructions.append(task)
    task_description = "\n".join(instructions)

    nvidia_key = os.getenv("NVIDIA_API_KEY")
    if not nvidia_key:
        return {"status": "error", "message": "NVIDIA_API_KEY is not set in environment"}

    sensitive_data: dict[str, str] = {}
    if email and password:
        sensitive_data = {
            "login_email": email,
            "login_password": password,
        }

    agent, browser_session = _create_agent(task_description, sensitive_data, nvidia_key)

    try:
        logger.info("Starting browser session for custom task")
        await browser_session.start()
        result = await asyncio.wait_for(agent.run(), timeout=RUN_TIMEOUT_SECONDS)
        logger.info("Custom task run completed")
        return {"status": "ok", "result": str(result)}
    except asyncio.TimeoutError:
        logger.exception("Custom task timed out after %s seconds", RUN_TIMEOUT_SECONDS)
        return {
            "status": "error",
            "message": f"Custom task timed out after {RUN_TIMEOUT_SECONDS} seconds",
        }
    except ModelProviderError as e:
        logger.exception("LLM provider error")
        return {
            "status": "error",
            "message": f"NVIDIA API is temporarily overloaded or unavailable. Try again in a few minutes. Details: {e}",
        }
    except Exception as e:
        logger.exception("Custom task run failed")
        return {"status": "error", "message": str(e)}
    finally:
        try:
            await browser_session.stop()
        except Exception:
            logger.exception("Browser session cleanup failed")

if __name__ == "__main__":
    # Local testing execution
    async def test_run():
        print("Testing agent execution locally...")
        res = await update_sheet({"value": "Test Data from Local Execution"})
        print(res)

    asyncio.run(test_run())
