from aeko.config.exceptions import AekoNotConfiguredError
from aeko.engine.runtime import RUNTIME


class Aeko:
    """
    Entry point for configuring the SDK.

    Nothing in the SDK reads the environment: the consuming API supplies the
    credentials and model choices here, and calling `config()` again (or
    `reset()`) rebuilds every agent so the change actually takes effect.
    """

    @staticmethod
    def config(api_key: str, *, fast_model: str | None = None, slow_model: str | None = None,
               max_tokens: int | None = None, report_max_tokens: int | None = None) -> None:
        """
        Configure the SDK for this process.

        Args:
            api_key: The Gemini API key backing every agent.
            fast_model: Model id for the router, FAQ, orchestrator and guardrail.
            slow_model: Model id for the specialist analysts.
            max_tokens: Output cap for the conversational flow.
            report_max_tokens: Output cap for the inventory report flow, which
                writes far longer answers than a chat turn.

        Raises:
            AekoNotConfiguredError: If `api_key` is empty or not a string.
        """

        if not api_key or not isinstance(api_key, str):
            raise AekoNotConfiguredError("Aeko.config() requires a non-empty API key.")

        RUNTIME.configure(
            api_key=api_key,
            fast_model=fast_model,
            slow_model=slow_model,
            max_tokens=max_tokens,
            report_max_tokens=report_max_tokens,
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
