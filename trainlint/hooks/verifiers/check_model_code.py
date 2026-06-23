#!/usr/bin/env python3
"""FP-filter verifiers for the forward/loss escalations.

The forward/mask and AR-shift rules used to fire on a bare KEYWORD — so they cried wolf on a
data-pipeline script or a yaml config that merely *mentions* those words (the megafish false
alarms). These verifiers gate that: they confirm the edit is ACTUALLY model/loss CODE before
escalating. They do NOT judge correctness (a human still does that) — they only kill the obvious
false positives (configs, keyword-with-no-code), keeping recall on genuine model edits.

func(text) -> (fire: bool, message: str|None).
"""
import re

_FWD_KW = re.compile(r"attention_mask|is_causal|causal_mask|attn_mask|softmax\(|\btop_p\b|\btop_k\b|\bsampler\b|def generate", re.I)
_FWD_STRONG = re.compile(r"attention_mask|is_causal|causal_mask|attn_mask|softmax\(|def generate", re.I)
_LOSS_KW = re.compile(r"text_loss|cross_entropy.*labels|F\.cross_entropy", re.I)
_SHIFT = re.compile(r"\[:,\s*:-1\]|\[:,\s*1:\]|shift_logits|shift_labels")

# real python model/training-code markers
_CODE = re.compile(r"\bdef \w+\(|\bclass \w+|\bimport\b|self\.\w|F\.\w|torch\.|\bnn\.|logits|hidden_states|\.view\(|\.transpose\(|einsum|\.backward\(", re.I)
# a yaml/ini config line: `key: value` (note the COLON; `key = value` is python, not config)
_CONFIG_LINE = re.compile(r"^[ \t]*[\w.\-]+\s*:\s*\S", re.M)


def _is_config(text):
    """Mostly `key: value` lines and no python -> it's a config/yaml, the keyword is just text."""
    return bool(_CONFIG_LINE.search(text)) and not _CODE.search(text)


def forward(text):
    if not text or not _FWD_KW.search(text):
        return (False, None)
    if _is_config(text):
        return (False, None)
    # a lone weak keyword (e.g. a DataLoader `sampler=`) with no model-code context -> not forward
    if not _FWD_STRONG.search(text) and not _CODE.search(text):
        return (False, None)
    return (True, "VERIFY (confirmed model code, not a config): this edit touches forward / mask / "
                  "sampling / generate logic — correctness can't be auto-checked, only a human reading "
                  "it can, and a mistake here is a silent disaster. Have the user review this diff.")


def loss(text):
    if not text or not _LOSS_KW.search(text):
        return (False, None)
    if _SHIFT.search(text):
        return (False, None)              # the AR off-by-one slice IS present -> fine
    if _is_config(text) or not _CODE.search(text):
        return (False, None)              # a yaml/config that merely contains 'loss'
    return (True, "VERIFY (confirmed a loss function, not a config): edited the loss but the AR "
                  "off-by-one slice (logits[:,:-1] vs labels[:,1:]) is missing — echo-collapse (one "
                  "token repeated forever) comes from exactly this. Confirm the shift is still there.")
