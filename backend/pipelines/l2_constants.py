"""L2 pipeline 共享常量。

放在独立模块，避免 config → tools → l2_take → config 循环 import。
l2_take.py 和 tools/script.py 都从这里导入，不互相依赖。
"""

# diff_type 合法枚举值集合。
# tools/script.py 和 l2_take.py 均从此处导入，保证同源（spec §4.1）。
_VALID_DIFF_TYPES: frozenset[str] = frozenset({"match", "missing", "substitution", "insertion"})
