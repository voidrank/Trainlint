# mimo-train-harness

A **soft** guardrail harness for AR-LLM multimodal training, packaged as a Claude Code plugin.

It does one thing: **当你说得模糊时,在对的时刻把缺的细节注入 agent 的上下文。** 从不拦截、从不报错——只在你启动训练、发 demo、改训练代码、问"为什么坏了"的那一刻,把对应的那几条踩坑经验怼到 agent 眼前。

经验来自一段真实的 Duplex MiMo 音质调查(power=2.0 mel / loss 权重 / speech_mask / DeepSpeed scheduler / 假 demo / 串行瞎猜 …),28 条沉淀成一张可扩展的触发表。

---

## 设计哲学

- **软,不硬。** hook 全部 `exit 0`,只用 `additionalContext` 注入,绝不 `exit 2` 拦截。最坏情况是多说一句不相关的话,永远不会卡住工作流。
- **补全,不约束。** 你发"重新train吧"三个字,harness 替你补上"target 分布体检了吗 / power=1.0 对齐了吗 / 数据在不在 JFS"。
- **经验与机制解耦。** 加一条经验 = 往 `triggers.jsonl` 加一行,`router.py` 永不改动。

---

## 结构

```
mimo-train-harness/
├── .claude-plugin/plugin.json   # 插件清单
├── hooks/
│   ├── hooks.json               # 接线: UserPromptSubmit + PreToolUse → router.py
│   └── router.py                # 读事件 → match() → 注入; 含 self-edit 过滤
├── triggers.jsonl               # 触发表 (name/when/inject/on),28 条经验
├── tests/
│   ├── cases.jsonl              # 回归 fixtures
│   └── run.py                   # python3 tests/run.py
└── README.md
```

---

## 工作原理

两个注入点,背后同一个 router:

| 事件 | 何时触发 | 匹配什么 |
|---|---|---|
| `UserPromptSubmit` | 你每发一条消息 | 你的 prompt 文本 |
| `PreToolUse` (Bash/Edit/Write/SendUserFile) | agent 动手前 | 命令行 / 文件路径 |

router 把命中的提醒拼成 JSON 打到 stdout:

```json
{ "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "〔harness·…〕…" } }
```

Claude Code 把 `additionalContext` 作为一段带出处的上下文块喂给模型(形态同 `<system-reminder>`),排在你那句话旁边——不改你的话、不挡 agent 的手。

---

## triggers.jsonl 格式

```jsonc
{ "name": "train-data",          // 短 id,给测试/可读性
  "when": "sbatch|训练|train\\s*吧",  // 正则,大小写不敏感;匹配 prompt 或 命令/路径
  "inject": "〔harness·…〕…",     // 注入文本
  "on": "PreToolUse" }           // 可选,限定只在某事件触发
```

**加一条经验** = 加一行 → `python3 tests/run.py` 跑过 → 完事(改表零重启,router 每次重读)。

写 `when` 时避开会撞到无关路径的裸子串(如 `train` 会命中 `pretrained` / 本仓库名);self-edit(改 harness 自己的文件)已在 `router.py` 里过滤,不用在表里操心。

---

## 安装

**形态 A — settings.json(最轻,单机用)**

在 `~/.claude/settings.json` 加:

```json
"hooks": {
  "UserPromptSubmit": [
    { "hooks": [ { "type": "command",
      "command": "python3 /ABS/PATH/mimo-train-harness/hooks/router.py" } ] }
  ],
  "PreToolUse": [
    { "matcher": "Bash|Edit|Write|SendUserFile",
      "hooks": [ { "type": "command",
        "command": "python3 /ABS/PATH/mimo-train-harness/hooks/router.py" } ] }
  ]
}
```

**形态 B — 插件(分发用)**

本目录需放进一个 marketplace 父目录(`../.claude-plugin/marketplace.json` 列出它),然后:

```
/plugin marketplace add /path/to/marketplace-root
/plugin install mimo-train-harness@<marketplace-name>
/reload-plugins
```

> 同时用 A 和 B 会**双重注入** —— 装插件后请删掉 settings.json 里的 hooks 块。

---

## 测试

```bash
python3 tests/run.py     # 13/13 应全过;改 triggers.jsonl 后必跑
```

---

## ⚠️ Footgun(踩过,血泪)

`settings.json` 的 hook 指向某脚本路径时,**绝不要在没先撤 hook / 没先让新路径就位的情况下移动或删除那个脚本**。脚本缺失会让 `python3` 退出码 **2** = Claude Code 视为**拦截**,于是**每一个** Bash/Edit/Write(连子 agent 的)都被挡死,session 自身无法自救,只能靠会话外的终端恢复:

```bash
ln -sfn /real/plugin/dir /the/dead/path     # 在 Claude Code 外的终端跑
```

改 hook 脚本路径的安全顺序:**先让新路径存在 → 再改 settings → 最后删旧路径。**

---

## 28 条经验覆盖

| trigger | 覆盖的经验(分类版编号) |
|---|---|
| `train-data` | 12,13,14,15 数据/target 分布 |
| `train-infra` | 16,17,18,19,20 多节点/DeepSpeed/存储 |
| `fresh-ckpt` | 25 中毒 ckpt → 从干净 base 重训 |
| `demo-eval` | 26,27,28 demo 必须过模型 |
| `debug-method` | 21,22,23,24 静默 bug 诊断方法论 |
| `code-align` | 1,2,3,4,5 训练代码对齐 / AR-shift / 采样 / KV-cache |
| `codec` | 6,7,8,9,10,11 frozen tokenizer 脾气 |
