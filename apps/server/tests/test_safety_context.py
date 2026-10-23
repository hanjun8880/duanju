"""Adult-context idioms must not trip the minors guard; minors + sexual content always blocks."""
import pytest

from src.domains.gateway.safety import ContentBlocked, check_prompt


@pytest.mark.parametrize("text", [
    "少年将军策马冲出城门，身后烽烟四起", "少女时代的成员在舞台上谢幕", "她二十八岁，仍有少女感",
    "少年英雄横刀立马", "a minor character walks past in the background", "he suffered a minor injury",
    "一位二十五岁的女律师", "十八岁生日那天他参军了",
])
def test_adult_context_allowed_in_uncensored(text):
    check_prompt(text, "uncensored_nsfw")


@pytest.mark.parametrize("text", [
    "少年 床戏", "少女 性感 姿势", "少年将军 床戏", "少女时代 裸体", "teen nude", "十五岁的女孩 亲密",
    "loli sexy", "初中生 诱惑", "underage erotic", "成年少女 色情",
])
@pytest.mark.parametrize("mode", ["uncensored_nsfw", "standard_sfw"])
def test_minor_plus_sexual_always_blocked(text, mode):
    with pytest.raises(ContentBlocked):
        check_prompt(text, mode)


@pytest.mark.parametrize("text", ["少年将军（28岁）与公主的床戏", "成年的少年将军 亲密戏"])
def test_idiom_with_explicit_adult_age_allowed(text):
    check_prompt(text, "uncensored_nsfw")


@pytest.mark.parametrize("text", ["十六岁的少女站在窗边", "一个七岁的孩子", "少女站在窗边", "少年在操场跑步"])
def test_bare_or_numeric_minor_blocked_in_uncensored(text):
    with pytest.raises(ContentBlocked):
        check_prompt(text, "uncensored_nsfw")


def test_chinese_numeral_ages():
    for t in ["十五岁", "十七岁的他", "七岁"]:
        with pytest.raises(ContentBlocked):
            check_prompt(t, "uncensored_nsfw")
    for t in ["二十五岁", "十八岁", "三十七岁的男人"]:
        check_prompt(t, "uncensored_nsfw")


def test_sfw_nonsexual_youth_still_allowed():
    check_prompt("少年在操场跑步", "standard_sfw")
    check_prompt("少女时代的回忆", "standard_sfw")
