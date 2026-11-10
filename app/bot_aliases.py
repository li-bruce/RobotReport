"""Display aliases are address hints, never transport authentication."""
import re


def normalize(value):
    if not isinstance(value, (list, tuple)) or len(value) > 12:
        raise ValueError('群内别名最多12个，请逐行填写')
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item.strip()) > 80 or re.search(r'[\x00-\x1f\x7f<>@＠]', item):
            raise ValueError('别名限1至80字，不包含@、换行或标签')
        item = item.strip()
        if item not in result:
            result.append(item)
    return result
