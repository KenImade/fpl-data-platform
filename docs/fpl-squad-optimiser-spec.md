# FPL Squad Optimiser — Subsystem Specification

**Companion to:** `fpl-api-concept.md`
**Scope:** Phase 5
**Date:** July 2026

---

## 1. Problem statement

Given a manager's current squad, bank balance, available free transfers and chips, plus a set of personal preferences, produce the transfer and selection plan that maximises expected points over the next *H* gameweeks.

This is a **mixed-integer linear program**, not a machine learning problem. It has an exact optimum. The interesting engineering is in the constraints, the preference model, and — most of all — in stopping the optimiser from confidently acting on differences that are pure model noise.

### The failure mode to design against

An optimiser will take a 0.15-point expected difference between two midfielders and restructure your entire squad around it. Your prediction intervals are perhaps ±3 points. The optimiser does not know this and will not tell you.

Three defences, built in from the start:

1. **Discount future gameweeks** so a speculative week-6 projection can't drive a week-1 transfer.
2. **Report the margin** — always re-solve with the top recommendation forbidden and show the user the gap. If it's 0.2 points over six weeks, say so plainly.
3. **Offer alternatives, not an answer.** Return the top *k* plans. A single confident recommendation misrepresents what you actually know.

---

## 2. Game rules as configuration

Every one of these has changed at some point, and hardcoding them makes historical backtests of the optimiser silently wrong.

```yaml
# rulesets/2026-27.optimiser.yml
squad:
  size: 15
  budget_initial: 100.0
  quotas: { GKP: 2, DEF: 5, MID: 5, FWD: 3 }
  max_per_club: 3

starting_xi:
  size: 11
  formation_bounds:
    GKP: { min: 1, max: 1 }
    DEF: { min: 3, max: 5 }
    MID: { min: 2, max: 5 }
    FWD: { min: 1, max: 3 }
  captain_multiplier: 2

transfers:
  free_per_gameweek: 1
  max_banked: 5          # VERIFY — this changed in recent seasons
  hit_cost: 4

pricing:
  # Sell price = purchase price + floor(profit / 2) to nearest 0.1
  # VERIFY against current rules; this materially changes optimal play
  profit_share: 0.5
  rounding: 0.1

chips:
  wildcard:        { uses: 2, scope: split_by_half }   # VERIFY
  free_hit:        { uses: 2, scope: split_by_half }
  bench_boost:     { uses: 2, scope: split_by_half }
  triple_captain:  { uses: 2, scope: split_by_half, multiplier: 3 }
```

> **Sell-price rules deserve special attention.** They create path dependence: what you can afford in GW10 depends on what you paid in GW3. An optimiser that assumes you sell at current market price will produce plans you cannot actually execute. This is the most common correctness bug in public FPL optimisers.

---

## 3. MILP formulation

### Sets

| Symbol | Meaning |
|---|---|
| $P$ | Candidate players, indexed $p$ |
| $W = \{1 \dots H\}$ | Horizon gameweeks, indexed $w$ |
| $T$ | Clubs, indexed $t$ |
| $R$ | Positions $\{\text{GKP}, \text{DEF}, \text{MID}, \text{FWD}\}$ |

### Parameters

| Symbol | Meaning |
|---|---|
| $\mu_{pw}$ | Expected points for player $p$ in gameweek $w$ |
| $\sigma_{pw}$ | Standard deviation of those points |
| $b_{pw}$ | Purchase price |
| $v_p$ | Sell price of a currently-held player (from purchase history) |
| $\beta_p$ | User bias, in points per gameweek |
| $\delta$ | Per-gameweek discount factor, default $0.87$ |
| $\rho$ | Bench weight, default $0.10$ |
| $\gamma$ | Risk aversion coefficient, default $0$ |
| $\text{ft}_0, m_0$ | Free transfers and bank at the start of the horizon |

### Decision variables

$$
\begin{aligned}
x_{pw} &\in \{0,1\} && \text{player } p \text{ in squad in } w \\
s_{pw} &\in \{0,1\} && \text{player } p \text{ in starting XI} \\
c_{pw} &\in \{0,1\} && \text{captain} \\
e_{pw} &\in \{0,1\} && \text{vice-captain} \\
\tau^{+}_{pw}, \tau^{-}_{pw} &\in \{0,1\} && \text{transferred in / out} \\
f_w &\in \mathbb{Z}_{\ge 0} && \text{free transfers available entering } w \\
h_w &\in \mathbb{Z}_{\ge 0} && \text{point hits taken in } w \\
m_w &\ge 0 && \text{bank balance after transfers in } w
\end{aligned}
$$

### Objective

$$
\max \sum_{w \in W} \delta^{\,w-1} \left[
\underbrace{\sum_{p} \tilde\mu_{pw}\,(s_{pw} + c_{pw})}_{\text{starters, captain doubled}}
+ \underbrace{\rho \sum_{p} \tilde\mu_{pw}\,(x_{pw} - s_{pw})}_{\text{bench}}
+ \underbrace{\sum_{p} \beta_p\, x_{pw}}_{\text{preferences}}
- \underbrace{4\,h_w}_{\text{hits}}
\right]
$$

where $\tilde\mu_{pw} = \mu_{pw} - \gamma\,\sigma_{pw}$ is the risk-adjusted expectation.

Notes on the terms:

- **Bench weight $\rho$.** Bench players only score via autosubs, which require a starter to blank. A flat 0.10 is a reasonable approximation; a refinement weights by bench order and by each starter's probability of not appearing. Not worth the complexity in v1.
- **Discount $\delta$.** At 0.87, gameweek 6 carries about half the weight of gameweek 1. This is the main lever against noise-chasing, and it should be user-tunable.
- **Captain via $c_{pw}$ added to $s_{pw}$** gives the captain $2\times$, since the player is counted once as a starter and once as captain.

### Constraints

**Squad composition** — for all $w$:

$$\sum_{p} x_{pw} = 15, \qquad \sum_{p:\,\text{pos}(p)=r} x_{pw} = q_r \;\; \forall r \in R, \qquad \sum_{p:\,\text{club}(p)=t} x_{pw} \le 3 \;\; \forall t \in T$$

**Starting XI** — for all $w$:

$$\sum_{p} s_{pw} = 11, \qquad s_{pw} \le x_{pw}, \qquad L_r \le \sum_{p:\,\text{pos}(p)=r} s_{pw} \le U_r$$

**Captaincy** — for all $w$:

$$\sum_{p} c_{pw} = 1, \quad \sum_{p} e_{pw} = 1, \quad c_{pw} \le s_{pw}, \quad e_{pw} \le s_{pw}, \quad c_{pw} + e_{pw} \le 1$$

**Squad continuity:**

$$x_{pw} - x_{p,w-1} = \tau^{+}_{pw} - \tau^{-}_{pw}, \qquad \tau^{+}_{pw} + \tau^{-}_{pw} \le 1$$

with $x_{p,0}$ fixed to the manager's current squad.

**Budget:**

$$m_w = m_{w-1} + \sum_{p} v_p\,\tau^{-}_{pw} - \sum_{p} b_{pw}\,\tau^{+}_{pw}, \qquad m_w \ge 0$$

**Transfers, hits and free-transfer rollover.** Let $u_w = \sum_p \tau^{+}_{pw}$ be transfers made. Then:

$$h_w \ge u_w - f_w, \qquad h_w \ge 0$$

$$f_{w+1} \le f_w - (u_w - h_w) + 1, \qquad 1 \le f_{w+1} \le \text{FT}_{\max}$$

The rollover constraint is subtle and worth understanding rather than copying. Free transfers *consumed* is $\min(u_w, f_w)$, which is nonlinear. But at the optimum $h_w = \max(0, u_w - f_w)$, because hits cost points and the solver minimises them — so $u_w - h_w$ equals exactly $\min(u_w, f_w)$. Expressing consumption that way keeps the constraint linear. The upper bound on $f_{w+1}$ suffices because more free transfers is weakly better, so the solver naturally pushes it to the bound.

**Naive alternatives fail.** Introducing a separate "free transfers used" variable bounded only from above lets the solver set it to zero while still making transfers, manufacturing free transfers from nothing. Test for this explicitly.

### 3.1 Two modes: building and shuffling

"Creating a squad" and "shuffling an existing one" are the same objective over meaningfully different constraint sets, and they should be explicit modes rather than an inferred special case.

| | **Build mode** | **Transfer mode** |
|---|---|---|
| Trigger | New season, wildcard, free hit, or exploratory "what's the best £100m squad with Saka in it" | In-season transfer planning |
| Current squad | None — $x_{p,0} = 0$ for all $p$ | Fixed to the manager's actual squad |
| Budget | Flat 100.0 (or user-supplied) | Bank + sell-price proceeds |
| Sell prices | Not applicable | Path-dependent on purchase history |
| Transfers, hits, rollover | All dropped | Fully modelled |
| Problem size | Much smaller — no continuity or transfer variables | Full formulation |
| Candidate pool | **No pre-filtering needed.** Solve over all ~700 players. | Filtered pool per §6 |
| Typical solve time | Well under a second | Seconds |

Build mode is the one where a pin is most natural — "give me the best possible squad that contains Saka and at least two Chelsea players" is a clean, fast, satisfying query, and it's probably how most users will first meet the tool. It's also the easier thing to ship: no sell-price logic, no rollover linearisation, no path dependence. 

**Build mode first.** It exercises the composition constraints, the preference layer and the cost-of-bias report end to end, and gives you something demonstrable well before the transfer machinery is correct.

---

## 4. Preferences and bias

This is the requested feature and the main product differentiator. Preferences come in two flavours, and users need both. **The primary mechanism is hard constraints** — "Saka is in my squad, full stop" and "give me between 1 and 3 Arsenal players" — with soft biases as the secondary, exploratory mode.

### 4.1 Hard constraints — non-negotiable

| Preference | Formulation |
|---|---|
| Pin player $p$ to squad | $x_{pw} = 1 \;\; \forall w$ |
| Pin player $p$ to starting XI | $s_{pw} = 1 \;\; \forall w$ |
| Own $p$ at some point in the horizon | $\sum_w x_{pw} \ge 1$ |
| Never own player $p$ | $x_{pw} = 0 \;\; \forall w$ |
| At least $k$ players from club $t$ ($1 \le k \le 3$) | $\sum_{p:\,\text{club}(p)=t} x_{pw} \ge k$ |
| At least $k$ from club $t$ **in the XI** | $\sum_{p:\,\text{club}(p)=t} s_{pw} \ge k$ |
| At most $k$ from club $t$ | $\sum_{p:\,\text{club}(p)=t} x_{pw} \le k$ |
| Never take a hit | $h_w = 0 \;\; \forall w$ |
| At most $n$ transfers in $w$ | $u_w \le n$ |
| Keep my core (players $C$) | $x_{pw} = 1 \;\; \forall p \in C,\, \forall w$ |

Four semantics decisions that the table alone doesn't settle. Each is a place where the obvious implementation does the wrong thing.

**Pin to the squad, not the starting XI — and make that the default.** "I want Saka in my team" almost always means *own him*, not *start him every single week regardless of a trip to the Etihad*. Constraining $x$ leaves the optimiser free to bench him in a bad fixture, which is both correct play and what the user actually wanted. Offer XI-pinning as an explicit second option, and label it clearly, because it is strictly worse for most people and they should have to choose it deliberately.

**Pins need an availability escape hatch.** If Saka is injured, suspended, or has left the club, $x_{pw} = 1$ is either infeasible or forces the user to carry dead weight through a five-week layoff. Auto-relax a pin when the player's appearance probability falls below a threshold (0.15 is a reasonable default), and **say so loudly in the response** rather than quietly dropping it:

```json
"relaxed_constraints": [
  {
    "constraint": "pin_squad:427",
    "reason": "appearance_probability_below_threshold",
    "value": 0.04,
    "detail": "Flagged with a hamstring injury; expected back GW15.",
    "action": "Pin suspended for GW12-14, reinstated GW15."
  }
]
```

Without this, the first injury turns your optimiser into a machine that returns errors.

**Club floors must be satisfiable by players who actually play.** `≥1 from CHE` will be satisfied by a 4.0m third-choice goalkeeper who never appears — technically compliant, obviously not what the user meant. Two fixes, offer both:

- Restrict eligible players to those above a minutes threshold: $\sum_{p \in \text{CHE},\, \mathbb{E}[\text{mins}] > 45} x_{pw} \ge k$
- Or use the XI-level form, $\sum_{p \in \text{CHE}} s_{pw} \ge k$, which is stronger and unambiguous

Default to the minutes-threshold version. It matches intent without over-constraining.

**Scope pins in time.** A pin applies across the whole horizon by default, but users reassess. Support `"pin_until_gameweek": 15` so someone can commit to a player through a good fixture run without locking themselves in for the full horizon. Also decide what a pin means during a Free Hit week — the squad is temporary, so the sane answer is that pins are suspended, and the docs should say so.

### 4.1a Pre-flight feasibility checks

With hard constraints as the primary interface, users will over-constrain constantly — it's the natural way to explore the tool. Catch the common cases in **milliseconds, before invoking the solver**, so the response is instant and the message is specific:

| Check | Failure message |
|---|---|
| $\sum_t k_t \le 15$ | Club minimums total 17 players; a squad holds 15. |
| Pinned players per club $\le 3$ | You've pinned 4 Arsenal players; the limit is 3 per club. |
| Pinned players per position $\le q_r$ | You've pinned 6 midfielders; a squad holds 5. |
| Club minimums satisfiable within position quotas | Requiring 3 goalkeepers from one club is impossible; quota is 2. |
| Pinned cost + cheapest legal remainder $\le$ budget | Your pins cost £58.5m and the cheapest valid remaining 9 players cost £43.0m, totalling £101.5m against a £100.0m budget. |
| No player both pinned and excluded | Player 427 appears in both `pin_squad` and `never_own`. |

That budget check is the one that catches most real failures, and it's a cheap greedy computation: sum the pinned prices, then add the cheapest available player at each unfilled position slot respecting club limits. If it fails, the user has pinned three premiums and the message should say exactly that.

Anything that survives pre-flight but still comes back infeasible goes through the IIS path in §7.

### 4.1b The hidden cost of a pin

A pin is a **budget lock**, not just a roster slot. Pinning a 15.0m striker removes 15% of the budget from optimisation, and the consequence shows up somewhere the user isn't looking — a defence made entirely of 4.0m rotation risks. They see that they kept their player; they don't see what it cost.

This makes the cost-of-bias report in §4.4 **more** important for hard constraints than for soft ones, not less. With a soft bias, $\beta$ at least tells the user the price they set. With a hard pin, there's no signal at all unless you compute it and show it.

### 4.2 Soft preferences — priced, not forced

A soft bias $\beta_p$ is added to the player's expected points in the objective. **Express it in points per gameweek**, because that unit is directly interpretable:

> $\beta_p = 1.5$ means *"I will give up 1.5 expected points per gameweek to keep this player."*

Surface it in the UI as a slider from 0 to 3 with plain-language anchors:

| $\beta$ | Label | Meaning |
|---|---|---|
| 0.0 | No preference | Pure model |
| 0.5 | Slight lean | Tiebreaker only |
| 1.5 | Strong lean | Will displace a moderately better player |
| 3.0 | Near-insistence | Practically a hard constraint |

Club affinity works identically: apply $\beta_t$ to every player at club $t$. A Chelsea fan who wants a Chelsea core sets $\beta_{\text{CHE}} = 0.8$ and gets a squad that leans that way without being nonsense.

### 4.3 Other preference axes

**Risk appetite** via $\gamma$ in $\tilde\mu = \mu - \gamma\sigma$:

- $\gamma < 0$ — variance-seeking. Appropriate for a manager chasing a mini-league from behind, where second place and tenth place are equally worthless.
- $\gamma = 0$ — expected-points maximising. The default.
- $\gamma > 0$ — variance-averse. Appropriate for protecting a lead.

This is a real strategic distinction that almost no public tool exposes, and it costs you one parameter.

**Template vs differential.** Add $\pm\alpha \cdot \text{ownership}_p$ to the objective. Negative $\alpha$ pushes toward differentials, positive toward template safety. Note honestly in the docs that this optimises *rank* rather than *points*, and that the two genuinely diverge.

### 4.4 The cost-of-bias report

The best feature in this subsystem, and it's nearly free: **solve twice.**

1. Solve with all preferences applied → plan $A$, objective $z_A$
2. Solve with all preferences stripped → plan $B$, objective $z_B$
3. Report $z_B - z_A$, evaluated on true expected points (not the bias-inflated objective)

```json
"preference_cost": {
  "total_expected_points_forgone": 4.8,
  "horizon_gameweeks": 6,
  "per_gameweek": 0.8,
  "breakdown": [
    { "constraint": "always_own:Saka",       "cost": 3.1 },
    { "constraint": "min_club:ARS>=3",       "cost": 1.7 }
  ],
  "unconstrained_alternative_preview": ["Palmer", "Mbeumo"]
}
```

Per-constraint attribution comes from solving with each constraint individually relaxed — $n+1$ solves for $n$ constraints, which is fine at these sizes and easy to parallelise.

This reframes the tool from "the computer says sell your favourite player" to "keeping him costs you about 0.8 points a week — your call." That's a much better product, and a much more honest one.

---

## 5. Robustness

### 5.1 Scenario-based optimisation

Point estimates ignore that your predictions are distributions. Sample $S$ scenarios from the per-player prediction distributions and maximise the average — or the conditional value at risk if the user is risk-averse:

$$\max \; \frac{1}{S}\sum_{s=1}^{S} \sum_{w} \delta^{w-1} \left[\sum_p \mu^{(s)}_{pw}(s_{pw} + c_{pw}) + \dots\right]$$

The squad variables stay shared across scenarios; only the payoffs vary. This multiplies model size by $S$, so:

- Use $S \approx 50$
- Apply scenarios **only to gameweek 1**, where the decision is actually binding, and point estimates thereafter
- Make it an opt-in flag, not the default

**Sample correlated scenarios, not independent ones.** Players from the same team share a clean sheet; a team's attackers share the team's goal total. Independent sampling badly understates the variance of a squad stacked with one team's players — which is precisely the risk a variance-conscious user is asking you about.

### 5.2 Reporting the margin

Cheaper and more valuable than scenario optimisation for most users. After solving:

1. Add a no-good cut forbidding the top transfer decision
2. Re-solve
3. Report the objective gap

```json
"confidence": {
  "margin_over_next_best": 0.31,
  "horizon_gameweeks": 6,
  "interpretation": "very_low",
  "note": "The top two plans are within model noise. Either is defensible."
}
```

Thresholds calibrated from your backtest: if your per-player RMSE over six gameweeks is around 8 points, a 0.3-point plan margin means nothing and you should say so.

### 5.3 Alternative plans

Generate $k$ diverse plans by iteratively adding no-good cuts. For a squad $S_j$ from solve $j$:

$$\sum_{p \in S_j} x_{p1} \le 14$$

This forces at least one different player. For more meaningful diversity, forbid the specific transfer pair rather than the whole squad. Return 3–5 plans with their objective values and let the user choose — they hold context your model doesn't.

---

## 6. Performance

### Candidate pre-filtering

The full problem is roughly 700 players × 8 gameweeks × 5 binary variables ≈ 28,000 binaries. That's slow.

Pre-filter to a candidate pool of 150–200:

1. **Always include every player in the manager's current squad.** Omitting one makes the problem infeasible, because continuity constraints reference it.
2. Take the top $N_r$ per position by discounted expected points over the horizon.
3. Add the top $N_r$ per position by expected points *per unit price*, to preserve cheap enablers — the optimiser needs 4.0m bench fodder to afford premiums, and a pure points ranking will exclude all of it.
4. Add anyone named in a hard or soft preference.

This typically cuts solve time by an order of magnitude with negligible loss of optimality. Log the pool size and validate periodically against an unfiltered solve.

### Solver choice

| Solver | Verdict |
|---|---|
| **HiGHS** | Recommended. Free, fast, actively developed, usable via `highspy` or as a PuLP backend. |
| OR-Tools CP-SAT | Strong alternative, parallelises well, good at the combinatorial structure here |
| CBC | PuLP's default. Too slow for multi-week horizons. Don't ship on it. |
| Gurobi | Fastest, but licensing makes it a poor fit for a public API |

**Target:** under 5 seconds for a 6-gameweek horizon on a filtered pool. Run asynchronously regardless — a 5-second synchronous HTTP request is a bad citizen, and the cost-of-bias and alternatives features mean you're doing many solves per job.

---

## 7. API design

```
POST /v1/optimise/squad          → 202 Accepted, { job_id }
GET  /v1/optimise/jobs/{job_id}  → { status, result? }
```

Optimisation requires a **secret key** — never a publishable one. It's the expensive endpoint and must not be callable from a browser bundle.

### Request

```json
{
  "season": "2026-27",
  "mode": "transfer",
  "start_gameweek": 12,
  "horizon": 6,
  "current_squad": [
    { "player_id": 427, "purchase_price": 10.1 }
  ],
  "bank": 1.4,
  "free_transfers": 2,
  "chips_available": ["wildcard", "bench_boost"],
  "preferences": {
    "pin_squad": [
      { "player_id": 427 },
      { "player_id": 233, "pin_until_gameweek": 15 }
    ],
    "pin_starting_xi": [],
    "never_own": [88],
    "club_minimum": [
      { "club": "ARS", "min": 2, "require_regular_starters": true },
      { "club": "CHE", "min": 1, "require_regular_starters": true }
    ],
    "club_maximum": [{ "club": "MCI", "max": 1 }],
    "player_bias": [{ "player_id": 305, "beta": 1.2 }],
    "club_bias":   [{ "club": "LIV", "beta": 0.5 }],
    "risk_aversion": 0.0,
    "differential_weight": 0.0,
    "max_hits_per_gameweek": 1,
    "allow_chips": true
  },
  "options": {
    "discount_factor": 0.87,
    "bench_weight": 0.10,
    "alternatives": 3,
    "scenario_sampling": false,
    "explain_preference_cost": true,
    "auto_relax_unavailable_pins": true
  }
}
```

For `"mode": "build"`, omit `current_squad`, `bank` and `free_transfers`, and supply `"budget": 100.0` instead.

### Response

```json
{
  "status": "complete",
  "model_version": "ensemble-v3.2.1",
  "feature_snapshot_id": "2026-27-gw12-deadline",
  "plans": [
    {
      "rank": 1,
      "total_expected_points": 371.4,
      "gameweeks": [
        {
          "gameweek": 12,
          "transfers_in": [{ "player_id": 233, "price": 7.4 }],
          "transfers_out": [{ "player_id": 88, "sell_price": 6.1 }],
          "hits": 0,
          "free_transfers_used": 1,
          "free_transfers_remaining": 1,
          "bank_after": 0.1,
          "chip": null,
          "starting_xi": [427, 233, "..."],
          "captain": 427,
          "vice_captain": 233,
          "bench_order": [12, 45, 89, 301],
          "expected_points": 62.8
        }
      ]
    }
  ],
  "preference_cost": { "...": "see §4.4" },
  "confidence": { "...": "see §5.2" }
}
```

### Infeasibility must be explained

The single biggest DX failure available here is returning `422 Infeasible` with no detail. Users *will* over-constrain — it's the natural way to explore the tool.

On infeasibility, compute an irreducible infeasible subset (or approximate it by relaxing constraints one at a time) and return something actionable:

```json
{
  "status": "infeasible",
  "conflicting_constraints": ["always_own:[12,88,301,427]", "max_per_club:ARS<=3"],
  "explanation": "Your always-own list contains 4 Arsenal players, exceeding the 3-per-club limit.",
  "suggested_relaxations": [
    { "action": "remove_from_always_own", "player_id": 301, "cost_estimate": 1.2 }
  ]
}
```

### Guardrails

| Limit | Value | Reason |
|---|---|---|
| `horizon` | ≤ 8 | Predictions past 8 gameweeks carry no useful signal and solve time grows fast |
| `alternatives` | ≤ 5 | Each costs a full re-solve |
| `scenario_sampling` | Gameweek 1 only, $S \le 50$ | Model size |
| Rate limit | Much tighter than data endpoints | This is the only CPU-expensive path in the system |
| Job TTL | 24 hours | Results reference a feature snapshot that goes stale at the next deadline |

---

## 8. Chips

Chips are optional binary variables per gameweek, with a season-level use limit.

| Chip | Modelling |
|---|---|
| **Bench Boost** | Set $\rho = 1$ for that gameweek. Trivial. |
| **Triple Captain** | Captain multiplier becomes 3 in that gameweek. Trivial. |
| **Wildcard** | Suspend hit costs for that gameweek: $h_w = 0$ regardless of $u_w$. Easy. |
| **Free Hit** | Genuinely awkward — the squad reverts afterwards. Model as a parallel squad for that gameweek only, with continuity constraints skipping over it. |

Two practical notes. First, chip *timing* over a full season is a much larger problem than an 8-gameweek horizon can answer well; treat chip recommendations from a short horizon as suggestive only, and say so. Second, chip availability is usually split across season halves — encode the half boundary as a constraint, or the optimiser will cheerfully plan two wildcards in the same half.

---

## 9. Testing

Optimisers fail silently. A wrong constraint produces a plausible-looking plan that is simply illegal or suboptimal, and nothing crashes.

| Test type | What it covers |
|---|---|
| **Independent rule validator** | A separate function, written without reference to the solver code, that checks any returned plan against the ruleset: squad size, quotas, club limits, budget arithmetic, formation legality, transfer accounting. Run it on every solve, in production. |
| **Golden instances** | Small hand-solvable cases with known optima (e.g. 20 players, 2 gameweeks) |
| **Property: preferences never help** | Objective with preferences ≤ objective without, evaluated on true expected points. A violation means a sign error. |
| **Property: free transfers can't be manufactured** | Total free transfers consumed over the horizon ≤ $\text{ft}_0 + H$ |
| **Property: budget never negative** | Including through sell-price path dependence |
| **Regression on solve time** | Alert if p95 exceeds target; catches pre-filter regressions |
| **Backtest the optimiser** | Simulate a full season following its advice, against baselines: no transfers at all, greedy one-transfer, template squad, and the overall average. This is the only end-to-end proof the thing works. |

That last one matters most. A prediction model can be evaluated on rank correlation, but an optimiser can only be evaluated by whether following it would have made you points. It is entirely possible to have a good model and an optimiser that destroys its value through over-trading.

---

## 10. Build order

1. **Build mode, single gameweek.** Squad composition constraints plus the independent rule validator. No transfers, no sell prices.
2. **Hard preferences in build mode** — pins, club floors and ceilings — with pre-flight feasibility checks and specific error messages. This is already a usable product: "best £100m squad containing Saka and two Chelsea starters."
3. **Cost-of-bias report.** Cheap once step 2 works, and it's what makes the pins honest.
4. **Build mode over a multi-gameweek horizon.** Introduces the discount factor and bench weighting.
5. **Transfer mode:** continuity, hits, free-transfer rollover. The rollover linearisation is the fiddly bit — write the property tests before the constraint.
6. **Sell-price path dependence.** Verify against a real squad's actual FPL-reported values, not your own arithmetic.
7. **Pin auto-relaxation on unavailability**, plus the IIS-based infeasibility explainer for whatever survives pre-flight.
8. **Alternatives and margin reporting.**
9. **Chips.**
10. **Scenario sampling**, if the backtest shows it earns its cost. It may not.

Steps 1–3 are shippable on their own and give you something demonstrable in a fraction of the total effort. Steps 1–8 give you something better than most public tools.
