"""承诺门 —— 「我说过要交付什么」的确定性判定器（B-2 冻结规格）。

**冻结**：本文件的词表、阈值、函数签名与判定顺序由主控 2026-11-11 定稿
（本机 12/12 全绿），后续批次**不得**加 weak-term / 同义词 / 后缀拼接等扩展。

**职责**：只做**文本判定**，不连库、不读环境、不写日志、不 import 其它业务
模块（顶部 import 只允许 ``datetime.timedelta`` 与 ``now_local``）。账的读写
在 :mod:`agent_core.commitments`；本模块只回答四个问题：

- ``should_extract``：这句话是不是「我答应了要交付」？→ 是才登记
- ``references_commitment``：这段文本是否指向某条已登记承诺？
- ``is_receipt``：这句是不是用户回执？
- ``should_close``：这条承诺本轮能否销账？

**边界**：``should_extract`` 刻意保守（疑问句 / 完成 / 否定 / 进行中 一律不
提取）——漏提取只损失一条提醒，误提取会让「提了就必须交付」的注入段长期挂着
假承诺，后者对体验伤害更大。
"""

from datetime import timedelta

from utils.local_time import now_local

MIN_TERM_LEN = 2
COMMITMENT_TTL_HOURS = 168

COMPLETION_MARKERS = ('了', '过', '已经', '已发', '已投递', '已送达', '已整理', '好了', '弄完', '做完', '搞定', '完成', '记好了', '查好了', '看好了', '发过', '给过', '刚才', '刚刚')
NEGATION_MARKERS = ('不', '别', '没', '没有', '尚未')
ONGOING_MARKERS = ('还在弄', '仍在', '正在', '还在整')
RECEIPT_MARKERS = ('收到', '拿到了', '看到了', '谢谢', '好的', '已读', '用上了', '看了')
PROMISE_VERBS = ('发', '给你', '帮你', '整理', '查', '写', '找', '标', '传', '准备', '弄', '安排')
SELF_MARKERS = ('我', '帮你', '给你', '发你', '发给你', '传你')
STOP_TOKENS = ('我们', '你们', '我', '你', '他', '她', '它', '帮你', '给你', '这个', '那个', '一下', '的', '了', '过', '已', '刚', '刚才', '好了', '就', '都', '也', '还', '在', '和', '把', '是', '吧', '呢')
GENERIC_BIGRAMS = frozenset(('我帮', '帮你', '你的', '我的', '这个', '那个', '就是', '还是', '已经', '刚才', '不过', '然后', '因为', '所以', '如果', '一下'))
SENTENCE_DELIMS = '。！？!?；;\n，,'


def _is_cjk(ch):
    return 0x4E00 <= ord(ch) <= 0x9FFF


def split_sentences(text):
    out, buf = [], []
    for ch in text:
        if ch in SENTENCE_DELIMS:
            if buf:
                out.append(''.join(buf).strip())
                buf = []
        else:
            buf.append(ch)
    if buf:
        out.append(''.join(buf).strip())
    return [s for s in out if s]


def core_terms(text):
    s = text
    for tok in STOP_TOKENS:
        s = s.replace(tok, ' ')
    terms, run = [], []
    for ch in s + ' ':
        if _is_cjk(ch):
            run.append(ch)
            continue
        joined = ''.join(run)
        if len(joined) == 2:
            terms.append(joined)
        elif len(joined) > 2:
            for i in range(len(joined) - 1):
                terms.append(joined[i:i + 2])
        run = []
    out = []
    for t in terms:
        if len(t) < MIN_TERM_LEN or t in GENERIC_BIGRAMS or t in out:
            continue
        out.append(t)
    return out


def _any_in(text, markers):
    return any(m in text for m in markers)


def _is_question(text):
    t = text.strip()
    return t.endswith('?') or t.endswith('？')


def should_extract(text):
    if not text or not text.strip():
        return False
    if _is_question(text):
        return False
    if not _any_in(text, SELF_MARKERS):
        return False
    for s in split_sentences(text) or [text]:
        if _any_in(s, COMPLETION_MARKERS) or _any_in(s, NEGATION_MARKERS) or _any_in(s, ONGOING_MARKERS):
            return False
    if not _any_in(text, PROMISE_VERBS):
        return False
    return bool(core_terms(text))


def references_commitment(detail, reply):
    terms = core_terms(detail)
    if not terms:
        return False
    for s in split_sentences(reply) or [reply]:
        for t in terms:
            if t in s:
                return True
    return False


def is_receipt(text):
    for s in split_sentences(text) or [text]:
        if _any_in(s, NEGATION_MARKERS):
            continue
        if _any_in(s, RECEIPT_MARKERS):
            return True
    return False


def should_close(detail, reply, delivered_payload=None, acted_on=False):
    if acted_on and delivered_payload is not None:
        return True
    if not references_commitment(detail, reply):
        return False
    if delivered_payload is not None and references_commitment(detail, delivered_payload):
        return True
    return is_receipt(reply)


def expires_at(now=None):
    base = now if now is not None else now_local()
    return base + timedelta(hours=COMMITMENT_TTL_HOURS)
