import re
import pytest
from attr.validators import matches_re

def test_matches_re_error_message_exact_match():
    compiled_pattern = re.compile(r"\d+")
    with pytest.raises(TypeError) as exc_info:
        matches_re(compiled_pattern, flags=re.IGNORECASE)
    # 精确匹配完整错误消息，变异体在消息前后添加了"XX"前缀和后缀
    assert str(exc_info.value) == "'flags' can only be used with a string pattern; pass flags to re.compile() instead"
