"""
apps.registry.services.split_full_name — the single canonical implementation
of the full_name -> (first, middle, last) split, factored out after a review
found three call sites (consultant-as-payer registration, two seed scripts)
each re-deriving it with a naive `.partition(" ")` that dumped every token
past the first into last_name instead of following the same convention the
0007 backfill migration and Payer.first_name's own docstring establish.
"""
from apps.registry.services import split_full_name


def test_single_token_goes_entirely_to_first_name():
    assert split_full_name("Orivenlimited") == ("Orivenlimited", "", "")


def test_two_tokens_split_first_and_last():
    assert split_full_name("Erado Consulting") == ("Erado", "", "Consulting")


def test_multi_token_middle_gets_everything_between():
    assert split_full_name("Golden Gate Consulting Ltd") == ("Golden", "Gate Consulting", "Ltd")


def test_blank_input_returns_all_blank():
    assert split_full_name("") == ("", "", "")
    assert split_full_name("   ") == ("", "", "")


def test_reconstructs_the_original_string_via_join():
    for name in ["Orivenlimited", "Erado Consulting", "Golden Gate Consulting Ltd"]:
        first, middle, last = split_full_name(name)
        assert " ".join(part for part in (first, middle, last) if part) == name
