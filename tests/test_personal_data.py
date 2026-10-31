"""The pure personal data masker."""

import pytest

from aeko.shared.personal_data import mask, unmask


@pytest.mark.parametrize(
    "text, masked",
    [
        ("meu cpf 529.982.247-25 ok", "meu cpf [CPF_1] ok"),
        ("meu cpf 52998224725 ok", "meu cpf [CPF_1] ok"),
        ("cpf 529.982.247-26 invalido", "cpf 529.982.247-26 invalido"),
        ("cpf 111.111.111-11 repetido", "cpf 111.111.111-11 repetido"),
        ("codigo 1529982247250", "codigo 1529982247250"),
        ("sem dados", "sem dados"),
    ],
)
def test_mask_cpf(text, masked):
    """Only a CPF with valid check digits, not glued to other digits, is masked."""
    assert mask(text, {})[0] == masked


def test_the_same_cpf_reuses_its_token_and_a_new_one_gets_the_next():
    """Same value, same token — however it was typed."""
    text, mapping = mask("529.982.247-25 e 52998224725 e 168.995.350-09", {})

    assert text == "[CPF_1] e [CPF_1] e [CPF_2]"

    assert mapping == {"[CPF_1]": "529.982.247-25", "[CPF_2]": "168.995.350-09"}


def test_mask_keeps_numbering_across_calls_sharing_a_mapping():
    """History and question share one mapping."""
    _, mapping = mask("529.982.247-25", {})

    text, mapping = mask("168.995.350-09 e 529.982.247-25", mapping)

    assert text == "[CPF_2] e [CPF_1]"


def test_unmask_restores_known_placeholders_and_leaves_unknown_ones():
    """Unmask is a no-op without a mapping."""
    mapping = {"[CPF_1]": "529.982.247-25"}

    assert unmask("cpf [CPF_1] e [CPF_9]", mapping) == "cpf 529.982.247-25 e [CPF_9]"

    assert unmask("cpf [CPF_1]", {}) == "cpf [CPF_1]"
