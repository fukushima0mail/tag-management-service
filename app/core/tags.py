"""タグ名の解釈（「グループ:値」形式・カンマ区切り入力）"""


def normalize_tag(name: str) -> str:
    """「年代：2020」「年代 : 2020」などを「年代:2020」に揃える"""
    group, value = split_tag(name.replace("：", ":"))
    return f"{group}:{value}" if group else value


def split_tag(name: str) -> tuple[str | None, str]:
    """「グループ:値」形式のタグを (グループ, 値) に分解（グループなしは (None, タグ名)）"""
    group, sep, value = name.partition(":")
    if sep and group.strip() and value.strip():
        return group.strip(), value.strip()
    return None, name.strip()


def parse_tags(text: str) -> list[str]:
    """カンマ（, 、）区切りのタグ入力を分解"""
    names = (normalize_tag(t) for t in text.replace("、", ",").split(","))
    return list(dict.fromkeys(t for t in names if t))
