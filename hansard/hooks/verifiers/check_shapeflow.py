#!/usr/bin/env python3
"""Coach-level detector for SHAPE-FLOW wiring edits.

Fires (silently, agent-directed) the moment an edit wires data into a model or changes
the model's forward / loss / shape plumbing — exactly where a data->model->loss shape
mismatch, or a shape-COMPATIBLE-but-misaligned bug (the silent kind: wrong broadcast
axis, in-place-vs-shifted loss, stream-layout axes), gets introduced.

It does NOT judge correctness and it does NOT escalate to the human. It nudges the
AGENT to DERIVE the end-to-end shape-flow itself before trusting the code (and to
persist/diff a baseline). The escalate-to-human path is a separate, complementary rule
(check_model_code.forward/.loss). This one is the "agent derives first" layer.

func(text) -> (fire: bool, message: str|None). The message comes from the checks.jsonl
rule; this returns None for it. Fails OPEN (no fire) on anything unsure.
"""
import re

# model forward / loss surface
_MODEL = re.compile(
    r"attention_mask|is_causal|causal_mask|attn_mask|softmax\(|def generate|"
    r"F\.cross_entropy|cross_entropy.*labels|\blogits\b|hidden_states", re.I)
# the strong model signals that, on their own, mark this as real model code
_MODEL_STRONG = re.compile(
    r"attention_mask|is_causal|causal_mask|attn_mask|def generate|"
    r"F\.cross_entropy|cross_entropy.*labels", re.I)
# data-pipeline surface: where a batch gets its shape before it reaches the model
_DATA = re.compile(
    r"class \w*Dataset|DataLoader|def collate|collate_fn|__getitem__|"
    r"pad_sequence|input_ids|\battention_mask\b|tokenizer\(", re.I)
# shape-manipulation ops: the actual reshaping where axes get swapped / merged / broadcast
_SHAPE_OP = re.compile(
    r"\.view\(|\.reshape\(|\.permute\(|\.transpose\(|\.unsqueeze\(|\.squeeze\(|"
    r"einsum|torch\.cat\(|torch\.stack\(|\.expand\(|\.repeat\(|rearrange\(", re.I)

# real python code markers (mirror check_model_code._CODE)
_CODE = re.compile(
    r"\bdef \w+\(|\bclass \w+|\bimport\b|self\.\w|F\.\w|torch\.|\bnn\.|logits|"
    r"hidden_states|\.view\(|\.transpose\(|einsum|\.backward\(", re.I)
# a yaml/ini config line: `key: value` (the keyword is just text, not code)
_CONFIG_LINE = re.compile(r"^[ \t]*[\w.\-]+\s*:\s*\S", re.M)


def _is_config(text):
    return bool(_CONFIG_LINE.search(text)) and not _CODE.search(text)


def wiring(text):
    if not text:
        return (False, None)
    hit_model = bool(_MODEL.search(text))
    hit_data = bool(_DATA.search(text))
    hit_shape = bool(_SHAPE_OP.search(text))
    if not (hit_model or hit_data or hit_shape):
        return (False, None)
    if _is_config(text):
        return (False, None)
    # must be real code: either python markers, or a strong model signal standing alone
    if not (_CODE.search(text) or _MODEL_STRONG.search(text)):
        return (False, None)
    # a single shape op in a plain util (no model/data context) isn't worth a full derivation
    if hit_shape and not (hit_model or hit_data) and len(_SHAPE_OP.findall(text)) < 2:
        return (False, None)
    return (True, None)  # message comes from the checks.jsonl rule
