import pytest

from src.domains.gateway.safety import ContentBlocked, check_prompt


@pytest.mark.parametrize("text", [
    "a 16 year old girl in the bedroom", "schoolgirl uniform sensual", "loli", "未成年少女", "初中生 亲密",
    "15岁的女孩", "teenager kissing", "Under-age", "童颜", "underage",
])
def test_minor_terms_blocked_in_uncensored(text):
    with pytest.raises(ContentBlocked):
        check_prompt(text, "uncensored_nsfw")


def test_adult_uncensored_prompt_allowed():
    check_prompt("two consenting adult characters (28 and 31) slow dance", "uncensored_nsfw")


def test_sfw_child_character_allowed_but_sexualised_minor_blocked():
    check_prompt("a child runs across the schoolyard at dawn", "standard_sfw")
    with pytest.raises(ContentBlocked):
        check_prompt("child in a sexy pose", "standard_sfw")
    with pytest.raises(ContentBlocked):
        check_prompt("未成年 裸体", "standard_sfw")


def test_audit_log_written(caplog):
    with caplog.at_level("WARNING", logger="audit"), pytest.raises(ContentBlocked):
        check_prompt("loli", "uncensored_nsfw", context={"project_id": "p1"})
    assert any("content_blocked" in r.message and "p1" in r.message for r in caplog.records)
