"""文字を落とさない禁則付き自動改行。明示改行は原文のまま扱う。"""
NO_START = set('、。，．・：；？！‼⁇⁈⁉ー々ヽヾゝゞ〃仝〻ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ」』）］｝〉》】〕〙〗〟’”｠»)]},.!?:;')
NO_END = set('「『（［｛〈《【〔〘〖〝‘“｟«([{')


def wrap_clusters(clusters, fits, *, natural=False):
    remaining = list(clusters)
    result = []
    while remaining:
        end = 0
        while end < len(remaining) and fits(remaining[:end+1]):end += 1
        if not end:return None
        if end < len(remaining):
            if natural:
                breaks=[i+1 for i,c in enumerate(remaining[:end]) if c[-1] in "。！？、 " and i+1 >= end*.55 and remaining[i+1][0] not in NO_START]
                if breaks:end=breaks[-1]
            while end > 0 and (remaining[end][0] in NO_START or remaining[end-1][-1] in NO_END or
                               remaining[end] == remaining[end-1] and remaining[end] in ('…','―')):
                end -= 1
            if not end:return None
        result.append(remaining[:end]);remaining = remaining[end:]
    return result or [[]]
