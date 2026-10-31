"""Mask personal data before agents read it, and put it back in the answer.

A detector is a (kind, pattern, validator) entry in DETECTORS; CNPJ, card and
phone are one more entry each.
"""

import re


def _digits(value: str) -> str:
    """Keep only the digits of a value."""

    return re.sub(r"\D", "", value)


def _valid_cpf(value: str) -> bool:
    """Say whether a CPF has two valid check digits and is not a repeated digit."""

    digits = _digits(value)

    if len(set(digits)) == 1:
        return False

    for size in (9, 10):
        total = sum(int(d) * w for d, w in zip(digits, range(size + 1, 1, -1)))

        if int(digits[size]) != (total * 10 % 11) % 10:
            return False

    return True


DETECTORS = [
    ("CPF", re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)"), _valid_cpf),
]


def mask(text: str, mapping: dict[str, str]) -> tuple[str, dict[str, str]]:
    """
    Replace every detected personal datum with a placeholder like "[CPF_1]".

    Args:
        text: The text to mask.
        mapping: Placeholder to original value, shared across calls so the same
            value keeps its token. Not modified.

    Returns:
        tuple[str, dict[str, str]]: The masked text and the updated mapping.
    """

    mapping = dict(mapping)

    for kind, pattern, is_valid in DETECTORS:

        def replace(match: re.Match) -> str:
            """Return the token of the matched value, minting one if new."""

            value = match.group()

            if not is_valid(value):
                return value

            for token, known in mapping.items():
                if token.startswith(f"[{kind}_") and _digits(known) == _digits(value):
                    return token

            token = f"[{kind}_{sum(t.startswith(f'[{kind}_') for t in mapping) + 1}]"

            mapping[token] = value

            return token

        text = pattern.sub(replace, text)

    return text, mapping


def unmask(text: str, mapping: dict[str, str]) -> str:
    """Put back the values of known placeholders, leaving unknown ones as is."""

    return re.sub(r"\[[A-Z]+_\d+\]", lambda m: mapping.get(m.group(), m.group()), text)
