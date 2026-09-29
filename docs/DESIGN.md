# Research trajectories, explicit state and selective checks

FOREST's design addresses the preservation of meaning across a research trajectory: the question being investigated, the conditions of an experiment, and the evidence behind a conclusion. This note develops conditional results for the mechanisms in the implementation.

## 1. The sequential problem

A research decision changes the state in which later decisions are made. An incorrect premise can therefore influence both the next action and the evidence available to assess it. In imitation learning, Ross, Gordon and Bagnell [1] formalize a related distribution shift. Under their assumptions, imitation error $\epsilon$ measured on expert-visited states admits a worst-case excess-cost bound $T^2\epsilon$ over a horizon $T$. Their learner-distribution analysis obtains $uT\epsilon$ with an additional bound $u$ on error cost-to-go. Those results concern their learning problem and assumptions; storing a research graph does not transfer the DAgger guarantee to FOREST.

Language-model evidence gives a complementary motivation. Liu et al. [2] show position-dependent use of relevant evidence in long contexts. Laban et al. [3] study instructions revealed across multiple turns and find that early assumptions can persist into later answers. These task-specific observations motivate retaining authoritative state and retrieving relevant originals. ReAct [4] studies interleaved reasoning, actions and observations, supporting the use of external feedback during a trajectory.

FOREST also treats excessive caution as a decision-policy problem. An analysis should identify what is most likely, what would change that judgment, and which affordable observation would resolve the decisive uncertainty. Writing rules express that requirement; the mathematical results below concern evidence admission and dependency structure.

## 2. State and handoffs

Represent durable research state as

$$
S_t=(g_t,G_t,A_t,H_t),\qquad
c_t=R(S_t,n_t),\qquad
z_t\sim\pi_\theta(\cdot\mid g_t,c_t).
$$

Here $g_t$ is the current goal and constraints, $G_t$ the dependency graph with ordinary revisions, $A_t$ the artifact catalog, and $H_t$ the action history. Retrieval $R$ selects context for the active node $n_t$. The model proposes $z_t$, tools produce observations, and supported handoff checks decide whether material can become current downstream input. Both accepted and rejected outcomes can be inspected.

The full retained state can exceed a model request's context window. Retrieval gives access to omitted originals; semantic understanding remains a property of the model and its actions. Revisions record deliberate changes to goals and protocols. The graph remains editable throughout the project.

## 3. Conditional error-admission bound

Fix $N$ proposed handoffs, counting retries. Let $\mathcal F_{t-1}$ include the entire preceding history. Let $B_t$ mean that proposal $t$ violates a specified contract, and $C_t$ mean that it is accepted as current downstream input. Define

$$
q_t=\Pr(B_t\mid\mathcal F_{t-1}),\qquad
\alpha_t=\Pr(C_t\mid B_t,\mathcal F_{t-1}).
$$

Set $q_t\alpha_t=0$ on histories where $q_t=0$. Then

$$
\Pr\!\left(\bigcup_{t=1}^{N}(B_t\cap C_t)\right)
\leq\min\!\left\{1,\sum_{t=1}^{N}\mathbb E[q_t\alpha_t]\right\}.
$$

**Proof.** By conditional probability and total expectation, $\Pr(B_t\cap C_t)=\mathbb E[q_t\alpha_t]$. Apply the union bound. The result allows adaptive proposals and correlated errors. A run that stops early can be padded with empty handoffs.

The product separates proposal quality from check quality. For the same candidate distribution at a boundary, a check with $\alpha_t<1$ reduces admission of the violations it detects. Retrieval can influence $q_t$ by changing the evidence available to the model. Its direction and magnitude require measurement. Across full policies, changed checks can also change future proposals and retry counts; the bound alone supplies no end-to-end ranking.

A contract must identify the property being assessed. A revision check can detect stale graph state. A numeric binding can identify a recorded value. Neither check determines whether a research hypothesis is true. Arbitrary workspace writes also occur outside these admission boundaries. Apply the proposition only to the selected handoffs and named properties.

## 4. Progress matters: rejection must discriminate

Consider a stationary retry model in which independent candidate/check trials have invalid-candidate probability $q$, false acceptance $\alpha$, and false rejection $\beta$. The probability of any acceptance in one trial is

$$
s=q\alpha+(1-q)(1-\beta)>0.
$$

Retrying until acceptance gives

$$
\Pr(\text{accepted candidate is invalid})=\frac{q\alpha}{s},
\qquad \mathbb E[\text{attempts}]=\frac1s.
$$

**Proof.** An accepted error on trial $k$ has probability $(1-s)^{k-1}q\alpha$. Summing the geometric series gives $q\alpha/s$; the waiting time is geometric with mean $1/s$. For $0<q<1$, rearranging $q\alpha/s<q$ yields

$$
\alpha<1-\beta.
$$

Thus a check improves accepted-result quality exactly when it accepts valid candidates more readily than invalid ones. If proposing costs $c_p$ and checking costs $c_v$, expected cost per accepted result is $(c_p+c_v)/s$ under the same assumptions. Rejecting everything gives no completed result; indiscriminate caution can increase cost without improving quality.

This special case explains the need to report valid completion and resource use alongside error rates. Adaptive research retries generally violate stationarity, so these formulas describe the decision trade-off rather than predict FOREST's observed outcomes. Its research policy asks for explicit judgments and discriminating experiments; it does not estimate $q$, $\alpha$ or $\beta$ automatically.

## 5. Local repair through declared dependencies

For this locality result, let $G_d$ be the directed acyclic graph of computational dependencies, including declared artifact inputs. For changed nodes $U$, define the affected set

$$
I_{G_d}(U)=U\cup\operatorname{Desc}_{G_d}(U).
$$

Assume the graph contains every relevant dependency, each node is deterministic conditional on its declared inputs and recorded local state, and those inputs and local states outside $I_{G_d}(U)$ remain unchanged, including code, environment and random state. Then outputs outside $I_{G_d}(U)$ need no recomputation after the edit.

**Proof.** A node outside the closure has no changed ancestor. In topological order, its declared inputs and local state remain unchanged, so its output remains reusable under the stated assumptions. For node costs $c_v$, recomputing the closure costs $\sum_{v\in I_{G_d}(U)}c_v$ instead of rerunning all nodes. This is a locality result; the saving depends on the graph.

FOREST's impact analyzer follows typed graph relationships and explicit input references. It distinguishes semantic, statistical, wording and layout changes so that re-analysis or rendering can replace a full experiment rerun where appropriate. Undeclared shared files, external services and random state can violate the locality assumptions and should be represented in task inputs.

## 6. Implementation correspondence

| Mechanism | Implemented check or behavior | Scope |
| --- | --- | --- |
| Current context | [`ContextBuilder`](../research/kernel/context.py) and [retained context](../research/agents/context_store.py) select current controls, preserve originals and expose paginated reads. | Availability of relevant state; comprehension requires model use. |
| Graph consistency | [`GraphCommandService`](../research/kernel/graph.py) checks expected revisions, project ownership, endpoints and execution cycles. | Graph mutations through the command service. |
| Input admission | [Scheduler](../services/worker/scheduler.py) and [worker](../services/worker/main.py) check bound inputs and current producer runs. | Declared dependencies at supported scheduling boundaries. |
| Observed execution | [Agent runtime](../research/agents/runtime.py) records tool identities and process results, checks required files and rejects active managed processes at completion. | Execution and file observation; process success alone does not establish a result's methodology or causal origin. |
| Evidence binding | [Paper validation](../research/paper/evidence.py) resolves metric/source IDs and checks declared critical contrary references. | Reference consistency. Unbound numeric prose is flagged; semantic entailment requires review. |
| Decisive research | [Policy](../research/agents/policy.py), [protocol validation](../research/validation/protocol.py) and [progress monitoring](../research/agents/progress.py) specify estimates, experimental duties and action on repeated unchanged reads. | Decision structure and observable progress; no automatic calibration of scientific forecasts. |

## 7. How to test the design claim

The central empirical question is whether explicit state and selective checks increase valid research progress per unit cost over long trajectories. Compare the same model, starting materials, tools and budgets across the full system and ablations of relevant-state retrieval, dependency checks, evidence binding and decision policy. Retain the available originals in every condition so a retrieval comparison isolates access strategy.

Use independent tasks with declared output contracts and recorded real execution. Assess invalid accepted handoffs, valid completed deliverables, repair scope, tokens, elapsed time and compute. Preserve failures and exhausted budgets in the denominator. Use paired task-level effects and uncertainty intervals; repeated decisions within a task are correlated. Assess scientific conclusions separately against primary evidence and the experimental protocol.

This protocol assigns each comparison a duty: overall effectiveness, context mechanism, error containment, repair locality or productive decision-making. A reduction in accepted errors accompanied by collapsed completion would fail the central claim. Evidence of sustained valid progress at comparable cost would support it.

## References

1. Ross, S., Gordon, G. J., and Bagnell, J. A. **A Reduction of Imitation Learning and Structured Prediction to No-Regret Online Learning.** AISTATS, 2011. [Proceedings and paper](https://proceedings.mlr.press/v15/ross11a.html).
2. Liu, N. F., et al. **Lost in the Middle: How Language Models Use Long Contexts.** TACL, 2024. [Paper](https://aclanthology.org/2024.tacl-1.9/).
3. Laban, P., et al. **LLMs Get Lost in Multi-Turn Conversation.** ICLR, 2026. [Paper](https://arxiv.org/abs/2505.06120).
4. Yao, S., et al. **ReAct: Synergizing Reasoning and Acting in Language Models.** ICLR, 2023. [Paper](https://arxiv.org/abs/2210.03629).
5. Kwon, W., et al. **Efficient Memory Management for Large Language Model Serving with PagedAttention.** SOSP, 2023. [Paper](https://arxiv.org/abs/2309.06180).
