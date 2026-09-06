"""Public SDK configuration entry point."""

from aeko.config.exceptions import AekoNotConfiguredError
from aeko.engine.runtime import RUNTIME
from aeko.shared import log_failure, log_success
from aeko.config.constants import CONFIG_LOG_MODULE

LOG_MODULE = CONFIG_LOG_MODULE


class Aeko:
    """
    Entry point for configuring the SDK.

    Nothing in the SDK reads the environment: the consuming API supplies the
    credentials and model choices here, and calling `config()` again (or
    `reset()`) rebuilds every agent so the change actually takes effect.
    """

    @staticmethod
    def config(api_key: str, *, fast_model: str | None = None, slow_model: str | None = None,
               max_tokens: int | None = None, report_max_tokens: int | None = None,
               temperature: float | None = None, top_p: float | None = None,
               top_k: int | None = None) -> None:
        """
        Configure the SDK for this process.

        Args:
            api_key: The Gemini API key backing every agent.
            fast_model: Model id for the router, FAQ, orchestrator and guardrail.
            slow_model: Model id for the specialist analysts.
            max_tokens: Output cap for the conversational flow.
            report_max_tokens: Output cap for the inventory report flow, which
                writes far longer answers than a chat turn.
            temperature: Sampling temperature for all models.
            top_p: Nucleus sampling probability for all models.
            top_k: Candidate sampling limit for all models.

        Raises:
            AekoNotConfiguredError: If `api_key` is empty or not a string.
        """

        if not api_key or not isinstance(api_key, str):
            log_failure(
                LOG_MODULE,
                "SDK configuration refused: missing or invalid API key.",
            )
            raise AekoNotConfiguredError("Aeko.config() requires a non-empty API key.")

        RUNTIME.configure(
            api_key=api_key,
            fast_model=fast_model,
            slow_model=slow_model,
            max_tokens=max_tokens,
            report_max_tokens=report_max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
        )

        log_success(
            LOG_MODULE,
            "SDK configured"
            f" (fast_model={RUNTIME.fast_model}, slow_model={RUNTIME.slow_model},"
            f" max_tokens={RUNTIME.max_tokens},"
            f" report_max_tokens={RUNTIME.report_max_tokens})",
        )

    @staticmethod
    def is_configured() -> bool:
        """
        Report whether an API key has been supplied.

        Returns:
            bool: True once `config()` has run successfully.
        """

        return bool(RUNTIME.api_key)

    @staticmethod
    def reset() -> None:
        """Clear every setting, including registered tools, back to defaults."""

        RUNTIME.reset()

        log_success(LOG_MODULE, "SDK reset to its defaults.")
