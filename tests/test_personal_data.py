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


@pytest.mark.parametrize(
    "text, masked",
    [
        ("cnpj 11.222.333/0001-81 ok", "cnpj [CNPJ_1] ok"),
        ("cnpj 11222333000181 ok", "cnpj [CNPJ_1] ok"),
        ("cnpj 11.222.333/0001-82 ok", "cnpj 11.222.333/0001-82 ok"),
        ("cartao 4111 1111 1111 1111 ok", "cartao [CARTAO_1] ok"),
        ("cartao 4111-1111-1111-1111 ok", "cartao [CARTAO_1] ok"),
        ("cartao 4111111111111111 ok", "cartao [CARTAO_1] ok"),
        ("cartao 4111 1111 1111 1112 ok", "cartao 4111 1111 1111 1112 ok"),
        ("tel (11) 91234-5678 ok", "tel [TELEFONE_1] ok"),
        ("tel (11) 3456-7890 ok", "tel [TELEFONE_1] ok"),
        ("tel +55 11 91234-5678 ok", "tel [TELEFONE_1] ok"),
        ("tel 11 91234-5678 ok", "tel [TELEFONE_1] ok"),
        ("tel 11 3456-7890 ok", "tel [TELEFONE_1] ok"),
        ("tel 11912345678 ok", "tel 11912345678 ok"),
        ("tel 1134567890 ok", "tel 1134567890 ok"),
        ("emitiu 2.350 tCO2e e 1.550 e R$ 200.000 a 1.200 graus", "emitiu 2.350 tCO2e e 1.550 e R$ 200.000 a 1.200 graus"),
    ],
)
def test_mask_cnpj_card_and_phone(text, masked):
    """Check digits, Luhn and a recognizable area code gate the new detectors."""
    assert mask(text, {})[0] == masked


def test_overlaps_go_longest_first_and_a_masked_span_is_not_rematched():
    """A card is not read as a phone and a token's digits are left alone."""
    text, mapping = mask("4111111111111111 11.222.333/0001-81 529.982.247-25 (11) 91234-5678", {})

    assert text == "[CARTAO_1] [CNPJ_1] [CPF_1] [TELEFONE_1]"

    assert mask(text, mapping)[0] == text
