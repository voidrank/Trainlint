# DESIGN — 设计哲学 / meta knowledge

> **动手加任何 trigger / check 之前,先读这一页。**
> 这个文件是本 plugin 的"记忆"。代码(`triggers.jsonl` / `checks.jsonl`)会不断变长——
> 未来的工作几乎都只是"加逻辑"。但**为什么这么设计**是不变的根。任何后来的人(包括未来
> 的 Claude)加的逻辑,如果违反下面的原则,宁可不加。规则是叶子,这一页是树干。

---

## 0. 一句话:它是一个"门卫"

站在 user 和 agent(Claude)之间。每次 **user 发消息** 或 **agent 要动手**(跑命令 / 改文件 /
发文件),门卫先看一眼,只做四件事之一:

| 门卫的动作 | 通道(代码里) | 谁感知到 |
|---|---|---|
| **放过** | 无输出 | 没人 |
| **悄悄提醒 agent** | `additionalContext` (coach) | 只有 agent |
| **拦下并提醒 user** | `systemMessage` (escalate) | user + agent |
| **直接打回 agent** | `permissionDecision: deny` (reject) | 只有 agent(user 不被打扰) |

整个 plugin 干的事,就是这个门卫。别的都是细节。

---

## 1. 它在防什么 —— 静默失败(这是存在理由)

我们训练踩的那些坑(power=2.0 mel、speech_mask、loss 权重、scheduler 被覆盖、假 demo……)
**全是静默的**:不报错、loss 照样降、train/infer 一致地错、人和 agent 都没发现,等发现已经
烧了几天 GPU。

> **门卫的最深职责:在 agent 快要犯这些老错误的那一刻,把"静默"变"响"——
> 要么自己拦下,要么提醒 user,要么悄悄纠正 agent。绝不让坑再静默地溜过去。**

加任何规则前先问:"我这条是不是在把一个**静默**的失败模式变响?" 如果它防的是一个会自己
报错崩溃的问题,那不需要门卫,编译器/异常自己会喊。

---

## 2. 核心原则:把每个决定,路由给"真正有能力判它"的那一方

这是整套设计的灵魂。不是"在对的时刻提醒 agent",而是**按可验证性分流**:

| 谁能判这件事 | 怎么处理 | 例子 |
|---|---|---|
| **机器能判**(事实/结构,可机检) | 自动 **reject**,静默打回,**不烦 user** | 数据放了 JFS、改了锁定的 base 配置 |
| **只有人能判**(设计判断,code 才是 ground truth,agent 不可靠) | **escalate**,停下,明确请 user 看那段 code | attention mask 设计、mask 互斥布局 |
| **都判不准但低风险** | **coach**,静默轻推 agent | 一般 best-practice 提醒 |
| **无关** | 放过 | 读文件、`ls` |

判据:**只影响"怎么做" → 静默;改变/违背了 user 的指令、或让 user 在不知情中担风险 → 出声。**

---

## 3. (未来的)小模型只分流,绝不当判官

规则匹配是 regex(`classifier.py` 的兜底)。未来会接一个小快模型做意图分流。**但记住:**

> **绝不把"对错/不可逆"的决定放在一个概率判官后面。**
> 小模型只回答**容易**的问题("这是哪类动作、谁该判它"),**不回答**难的("这到底对不对")。
> 判对错的永远是:确定性脚本(`checks.py`)或人。

用三道护栏把小模型的失误框成廉价:
1. **致命的机检项不走模型**——由动作结构直接触发(`checks.py`),模型 misroute 也挡不住它们。
2. **不对称调参**:高风险类宁可错报(user 多瞄一眼)不可漏报。
3. 模型在"人验类"只负责**把 user 的眼睛叫到正确位置**,不需要它判对。

---

## 4. 三个不可违反的安全不变量

1. **预筛 default-open**(`prefilter.py`):只丢掉**能证明无害**的(纯读、文档、self-edit);
   拿不准一律放过去检查。**门口绝不按 topic/关键词筛**——那是 default-closed,会把没匹配上
   关键词的危险动作**静默漏掉**,等于白搭后面的判断。
2. **router 整体 fail-open**(`router.py`):任何内部报错 → 什么都不做、不输出。门卫自己出 bug,
   绝不能因此卡住工作流。
3. **拦截只用 `permissionDecision: deny`,永远不用非零退出码。** router 永远 `exit 0`。
   > ⚠️ **血泪 footgun**:hook 指向的脚本一旦缺失,`python` 退出码 2 = Claude Code 视为拦截,
   > 于是**每一个** Bash/Edit/Write(连子 agent 的)全被挡死,session 自身无法自救,只能靠会话
   > 外的终端恢复。所以(a)拦截走 JSON 不走 exit code;(b)改 hook 指向的脚本路径时,**先让
   > 新路径就位 → 再改 settings → 最后删旧路径**。

---

## 5. 软为默认,硬有理由 —— 强度是刻意的,不是装饰

绝大多数干预是**悄悄话**(coach)。**出声**(escalate)和**打回**(deny)是留给真正配得上的
少数。"该不该让 user 知道"本身就是一个设计维度:

- 只是"怎么把活干好" → 静默(user 没要求看你的 checklist)。
- 改变/违背了 user 的指令、或让 user 不知情担风险 → 出声。
- 不可逆 / 信任关键 / 机器确定违规 → 打回。

加规则时**默认给 coach**;只有想清楚"为什么 user 必须知道"才升 escalate;只有"机器 100% 确定
这是违规且该弹回"才升 reject。

---

## 6. 三层:机制 / 通用规则 / 项目facts —— 把"原理"和"事实"分开

早期版本把原理和项目事实焊在同一条规则里(`/jfs/`、`power=1.0`、`code 351`),换个项目整条死。
现在裂成三层,越往下越易变:

1. **机制**(`router.py` + `prefilter/checks/classifier/facts`)—— 不动。
2. **通用规则** —— 两类:
   - `triggers.jsonl` SECTION 1(**可移植内核**):流程/诊断纪律,**零 facts**,换项目原样能用。
   - `triggers.jsonl` SECTION 2 + `checks.jsonl`:**通用原理**,项目相关的字符串一律写成 `{{占位符}}`。
3. **项目 facts**(`project.<name>.json`)—— 填空层。`{{bad_storage_re}}`=`/jfs/|/nas/`、
   `{{preproc_trap_re}}`=`MelSpectrogram\(...`、`{{frozen_component}}`=`MiMo audio tokenizer` …

`facts.py` 在 match/inject/message 用之前把 `{{...}}` 展开成当前项目的事实。

> **判据:写规则时,凡是只对本项目成立的字符串(路径、码号、库名、文件名),都抽成 `{{fact}}`。
> 规则体里只留"原理"。** 这样"换项目 = 换一份 facts,不改规则"(见 §10)。

加一条经验:原理 → 加规则行(用 `{{}}` 引 facts);事实 → 进 `project.<name>.json`;
再加一个 `tests/cases.jsonl` 用例 + 跑 `python3 tests/run.py`。

**日常工作就是"加规则行 / 填 facts",所以这页"为什么"必须写下来——数据会长,原则不能漂。**

---

## 7. 我们试过、又否决的方案(别走回头路)

- **硬闸 + 收据制度**(每个动作必须出示一张 hash 绑定的 PASS receipt 才放行):太重、太脆,
  收据缺失会卡死整个流程,还逼着 user 走流程。**否决**,换成现在的软注入。
- **门口用关键词 regex 当唯一筛子**:default-closed,换种说法就漏。**否决**,门口改成只看
  "读/写·对内外"的结构筛。
- **让(小)模型直接判对错**:概率判官会在"这么多 check"上失误。**否决**,模型只分流。

---

## 8. 加一条规则/题前,问自己这六个问题

0. **换个项目这条还活吗?活的是哪条原则?**(最重要的过滤)
   - 把**原则**写进规则体 / quiz 的 headline;把**项目实例**(路径、码号、库名、参数值)抽进
     `project.<name>.json` 的 `{{fact}}`,或在 quiz 里降级成"…只是实例"。
   - 判据:如果换个项目这条就**字面失效**,那它考的不是知识、是 trivia——抽出背后的原则。
   - 例:`power=2.0` 不是 domain 知识,是"冻结组件契约"原则的一个实例(换 CLIP 就是 image norm)。
1. 我防的是一个**静默**失败吗?(不是 → 可能不需要门卫)
2. 这件事**谁能判**?机器 → check(reject);只有人 → check(escalate);都不准 → trigger(coach)。
3. 我给的档位**配得上**吗?(默认 coach,升级要有理由)
4. 我的 `match` 会不会**误伤**无关的路径/措辞?(给 `unless` 或收紧;加一个反例进 tests)
5. 它**fail 的时候**安全吗?(规则只该制造"放过/提醒",绝不该让 router 崩 → 那会 fail-open 成静默)

---

## 9. 文件地图

```
hooks/prefilter.py     stage1 结构预筛(读/写·default-open)
hooks/checks.py        stage3 确定性致命项引擎(读 checks.jsonl)
hooks/checks.jsonl     reject/escalate 的策略表(通用原理 + {{facts}})← 最该 review
hooks/classifier.py    stage2 意图分流(现 regex 兜底,待接小模型)
hooks/facts.py         项目 facts 加载 + {{占位符}} 展开
hooks/router.py        orchestrator:三级合并 → 落通道;fail-open;永远 exit 0
triggers.jsonl         coach 规则:SECTION1 可移植内核 / SECTION2 通用原理+{{facts}}
project.mimo.json      MiMo 的 facts(填 {{占位符}});换项目复制成 project.<name>.json
.active-project        (可选)写项目名;否则 env HARNESS_PROJECT,否则默认 mimo
tests/                 加规则必跑;cases.jsonl 是行为快照
```

## 10. 移植到另一个项目

规则不动,只换 facts:

1. `cp project.mimo.json project.<新项目>.json`,把每个键填成新项目的事实
   (frozen 组件、不可靠存储正则、预处理陷阱正则、reference 实现、锁定配置正则 …)。
2. `echo <新项目> > .active-project`(或设 `HARNESS_PROJECT`)。
3. `triggers.jsonl` SECTION 1(流程/诊断)原样保留;SECTION 2 + `checks.jsonl` 里若有
   新项目特有、现有原理没覆盖的失败模式,**加新的通用原理规则**(继续用 `{{}}` 引 facts)。
4. `python3 tests/run.py`(给新项目补几条 cases)。

例:做一个 ViT 分类项目 → `project.vit.json` 里 `frozen_component`=CLIP、
`preproc_trap_re`=图像 normalize 调用、`preproc_ok_re`=正确的 mean/std……
通用规则 `preproc-matches-frozen-config` 自动对 CLIP 生效,一行规则没改。

## 11. Quiz:教原则,不教 trivia

`quiz.jsonl` 是知识层:harness **提醒**,quiz **测**操作员是否真懂。每题是一节小课:
`principle`(可迁移法则,headline)→ `context`(为什么会碰到)→ `q` → `naive`(常见错答=认知缺口)
→ `why`(因果机制)→ `a`(以原则收口,domain 降级成"实例")。

铁律(同 §8 第 0 条):**题考的是 `principle`,domain 只是讲清它的那道疤。** 两道题如果背后是同
一条原则(如 `np.zeros`→OOD 和 `power=2.0`→OOD 都是 `frozen-component-contract`),它们就该
**共享同一个 `principle`**——这正是"原则可迁移、实例不可迁移"的体现。

quiz-gate 是 OPT-IN(env `HARNESS_QUIZ=1` 或 `.quiz-gate` 文件):高风险动作时按 `when` 弹出
一道相关题(带 `context`),**只 surface 不阻塞**。它测你懂没,不替你拦。
