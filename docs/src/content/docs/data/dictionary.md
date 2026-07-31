---
title: Field reference
description: Every field on every response — what it means, its type, and what null means.
sidebar:
  order: 3
---

Generated from the response models the API actually returns.

Field descriptions are maintained beside the Pydantic definitions and
generated into this reference automatically. If a field exists in the API,
it appears here.

## Conventions

A few patterns run through every response. Knowing them makes most fields
self-explanatory.

### Identifiers

| Suffix | Scope | Use for |
|---|---|---|
| `_code` | **Permanent** — survives seasons | Anything historical |
| `_id` | **One season** — reassigned each August | Requests within a single season |

:::caution[Do not join on IDs across seasons]
`player_id` and `team_id` are reassigned each season. Use `player_code` and
`team_code` for historical joins.
:::

### The `gw_` prefix

On per-fixture data, columns prefixed `gw_` hold gameweek-level values.
They are populated on the first fixture of a gameweek only and null on later
fixtures, preventing double counting in double gameweeks.

Everything else describes fixture grain.

### Money

Prices are returned as decimal millions: `10.1` means £10.1m. Internally they
are stored as integer tenths, so rounding error does not accumulate.

### Times

Every timestamp is UTC and ISO 8601 with an explicit offset.

### Null semantics

Null is rarely "missing". Usually it carries information:

| Field | Null means |
|---|---|
| `opponent_code` | Opposition is not a Premier League club |
| `days_since_last_match` | Kickoff time is unknown, or this is the first match |
| `chance_of_playing_next` | No availability flag; player is presumed available |
| `result`, `goals_for` | Fixture has not been played |

The field description defines ambiguous cases.

### Provenance

Values come from:

- **Our captures** — price, ownership, injury news, availability.
- **Core Insights** — match statistics, xG, defensive actions, Elo.
- **Derived** — calculated values such as rest days and season totals.

Season totals inherit the known [~2% coverage gap](/data/quality/), so they
will not always match FPL's official totals exactly.



## Field index


### Team

- [`season`](#team-season)
- [`team_code`](#team-team_code)
- [`team_id`](#team-team_id)
- [`team_name`](#team-team_name)
- [`team_short`](#team-team_short)
- [`strength`](#team-strength)
- [`strength_overall_home`](#team-strength_overall_home)
- [`strength_overall_away`](#team-strength_overall_away)
- [`strength_attack_home`](#team-strength_attack_home)
- [`strength_attack_away`](#team-strength_attack_away)
- [`strength_defence_home`](#team-strength_defence_home)
- [`strength_defence_away`](#team-strength_defence_away)
- [`latest_elo`](#team-latest_elo)
- [`latest_match_at`](#team-latest_match_at)
- [`matches_played`](#team-matches_played)
- [`wins`](#team-wins)
- [`draws`](#team-draws)
- [`losses`](#team-losses)
- [`goals_for`](#team-goals_for)
- [`goals_against`](#team-goals_against)
- [`goal_difference`](#team-goal_difference)

### Gameweek

- [`season`](#gameweek-season)
- [`gameweek`](#gameweek-gameweek)
- [`gameweek_name`](#gameweek-gameweek_name)
- [`deadline_utc`](#gameweek-deadline_utc)
- [`is_finished`](#gameweek-is_finished)
- [`is_data_checked`](#gameweek-is_data_checked)
- [`fixture_count`](#gameweek-fixture_count)
- [`first_kickoff`](#gameweek-first_kickoff)
- [`last_kickoff`](#gameweek-last_kickoff)
- [`average_score`](#gameweek-average_score)
- [`highest_score`](#gameweek-highest_score)
- [`most_selected_player_id`](#gameweek-most_selected_player_id)
- [`most_captained_player_id`](#gameweek-most_captained_player_id)
- [`transfers_made`](#gameweek-transfers_made)
- [`has_usable_snapshot`](#gameweek-has_usable_snapshot)
- [`snapshot_at`](#gameweek-snapshot_at)
- [`hours_before_deadline`](#gameweek-hours_before_deadline)

### Player

- [`season`](#player-season)
- [`player_id`](#player-player_id)
- [`player_code`](#player-player_code)
- [`team_code`](#player-team_code)
- [`team_name`](#player-team_name)
- [`team_short`](#player-team_short)
- [`web_name`](#player-web_name)
- [`full_name`](#player-full_name)
- [`position`](#player-position)
- [`price`](#player-price)
- [`selected_by_percent`](#player-selected_by_percent)
- [`status`](#player-status)
- [`news`](#player-news)
- [`chance_of_playing_next`](#player-chance_of_playing_next)
- [`ep_next`](#player-ep_next)
- [`state_as_of`](#player-state_as_of)
- [`appearances`](#player-appearances)
- [`minutes`](#player-minutes)
- [`goals`](#player-goals)
- [`assists`](#player-assists)
- [`xg`](#player-xg)
- [`xa`](#player-xa)
- [`points`](#player-points)
- [`bonus`](#player-bonus)

### Player page

- [`items`](#playerpage-items)
- [`total`](#playerpage-total)
- [`limit`](#playerpage-limit)
- [`offset`](#playerpage-offset)

### Fixture

- [`match_id`](#fixture-match_id)
- [`season`](#fixture-season)
- [`gameweek`](#fixture-gameweek)
- [`competition`](#fixture-competition)
- [`kickoff_utc`](#fixture-kickoff_utc)
- [`home_team_code`](#fixture-home_team_code)
- [`home_team_name`](#fixture-home_team_name)
- [`away_team_code`](#fixture-away_team_code)
- [`away_team_name`](#fixture-away_team_name)
- [`home_score`](#fixture-home_score)
- [`away_score`](#fixture-away_score)
- [`home_elo`](#fixture-home_elo)
- [`away_elo`](#fixture-away_elo)

## Team

Returned by `GET /v1/teams`, `GET /v1/teams/{team_code}`.

One club in one season.

Season-scoped because clubs are: strength ratings change, the numeric id
is reassigned, and even the name is not stable. `team_code` is the only
thing that survives.

Two independent measures of strength are carried. FPL's own ratings drive
its fixture difficulty display; ClubElo is a rating system computed from
results. They disagree often, and the disagreement is sometimes the
interesting part.

| Field | Type | Nullable | Description |
|---|---|---|---|
| <a id="team-season"></a>`season` | string | No | Season this row describes. |
| <a id="team-team_code"></a>`team_code` | integer | No | Permanent club identifier. Survives across seasons and through relegation, so it is the correct key for anything historical. Fixture data joins on this rather than `team_id`. |
| <a id="team-team_id"></a>`team_id` | integer | No | Season-scoped identifier, 1-20, assigned alphabetically each August. Reassigned as clubs are promoted and relegated, so it is not comparable across seasons. |
| <a id="team-team_name"></a>`team_name` | string | No | Club name as FPL renders it for this season. **Not stable** — 'Ipswich' in 2024/25 became 'Ipswich Town' in 2026/27, same club, same code. Grouping by name splits one club in two. |
| <a id="team-team_short"></a>`team_short` | string | No | Three-letter abbreviation. |
| <a id="team-strength"></a>`strength` | integer | Yes | FPL's overall strength rating on a 1-5 scale. Coarse, and set by FPL rather than derived from results — but it is what their own fixture difficulty ratings are built from, so it is worth having when comparing against `ep_next`. |
| <a id="team-strength_overall_home"></a>`strength_overall_home` | integer | Yes | FPL's home strength rating, on a larger scale than `strength` — typically 1000-1400. Higher is stronger. |
| <a id="team-strength_overall_away"></a>`strength_overall_away` | integer | Yes | FPL's away strength rating. Usually lower than the home figure. |
| <a id="team-strength_attack_home"></a>`strength_attack_home` | integer | Yes | FPL's attacking strength at home. |
| <a id="team-strength_attack_away"></a>`strength_attack_away` | integer | Yes | FPL's attacking strength away. |
| <a id="team-strength_defence_home"></a>`strength_defence_home` | integer | Yes | FPL's defensive strength at home. |
| <a id="team-strength_defence_away"></a>`strength_defence_away` | integer | Yes | FPL's defensive strength away. |
| <a id="team-latest_elo"></a>`latest_elo` | number | Yes | ClubElo rating after this club's most recent completed match. A current-strength figure, suitable for display or comparison.

For modelling, use the per-fixture `home_elo` and `away_elo` on the fixtures endpoint instead — those are the rating **at kickoff** and carry no hindsight, whereas this one reflects everything that has happened since. |
| <a id="team-latest_match_at"></a>`latest_match_at` | datetime | Yes | Kickoff of the match `latest_elo` reflects. Null before a club has played, or where that fixture carries no timestamp. |
| <a id="team-matches_played"></a>`matches_played` | integer | Yes | League matches played. **Null rather than zero before a season starts**, so a club yet to play is distinguishable from one that has played and lost everything. Excludes European and cup fixtures. |
| <a id="team-wins"></a>`wins` | integer | Yes | League wins. Null before the season starts. |
| <a id="team-draws"></a>`draws` | integer | Yes | League draws. Null before the season starts. |
| <a id="team-losses"></a>`losses` | integer | Yes | League defeats. Null before the season starts. |
| <a id="team-goals_for"></a>`goals_for` | integer | Yes | League goals scored. Null before the season starts. |
| <a id="team-goals_against"></a>`goals_against` | integer | Yes | League goals conceded. Null before the season starts. |
| <a id="team-goal_difference"></a>`goal_difference` | integer | Yes | `goals_for` minus `goals_against`. Null before the season starts. |


## Gameweek

Returned by `GET /v1/gameweeks`, `/current`, `/next`, `/{gameweek}`.

One FPL gameweek: its deadline, its fixtures, and how it turned out.

The deadline is the field everything else hangs off. It is the instant
teams lock, and it is the boundary that makes a feature either legitimate
or leaked.

| Field | Type | Nullable | Description |
|---|---|---|---|
| <a id="gameweek-season"></a>`season` | string | No | Season the gameweek belongs to. |
| <a id="gameweek-gameweek"></a>`gameweek` | integer | No | Gameweek number, 1-38. |
| <a id="gameweek-gameweek_name"></a>`gameweek_name` | string | No | FPL's display name. |
| <a id="gameweek-deadline_utc"></a>`deadline_utc` | datetime | No | Transfer deadline, UTC — roughly 90 minutes before the first kickoff. Teams lock at this instant, so it is the cutoff for any information a manager could have acted on. Verified against an independent epoch field on every build, so a timezone misparse cannot go unnoticed. |
| <a id="gameweek-is_finished"></a>`is_finished` | boolean | Yes | Whether every fixture in the gameweek has been played. |
| <a id="gameweek-is_data_checked"></a>`is_data_checked` | boolean | Yes | Whether FPL has finalised scoring for the gameweek. Bonus points are provisional until this is true, and points can still change. |
| <a id="gameweek-fixture_count"></a>`fixture_count` | integer | Yes | League matches in this gameweek. **Not always 10.** Rounds get rescheduled around cup and European commitments, so 7 and 13 both occur legitimately in a normal season. A count above 10 usually means fixtures were moved into this week; below, moved out. |
| <a id="gameweek-first_kickoff"></a>`first_kickoff` | datetime | Yes | Earliest league kickoff. Null where no fixture carries a time. |
| <a id="gameweek-last_kickoff"></a>`last_kickoff` | datetime | Yes | Latest league kickoff. The gameweek is not settled until this match ends, which is the relevant window for anything live. |
| <a id="gameweek-average_score"></a>`average_score` | number | Yes | Mean score across all FPL managers. Null until the gameweek is played. Useful as a baseline: beating the average is the minimum bar for a strategy to be worth anything. |
| <a id="gameweek-highest_score"></a>`highest_score` | integer | Yes | Best single-manager score in the gameweek. |
| <a id="gameweek-most_selected_player_id"></a>`most_selected_player_id` | integer | Yes | Most-owned player at the deadline, as a season-scoped `player_id`. Resolve via `/v1/players/{player_id}` with the same season — this id is reassigned each August and is not comparable across seasons. |
| <a id="gameweek-most_captained_player_id"></a>`most_captained_player_id` | integer | Yes | Most-captained player, season-scoped `player_id`. Captaincy is a stronger ownership signal than selection, since it is a single concentrated bet rather than a squad slot. |
| <a id="gameweek-transfers_made"></a>`transfers_made` | integer | Yes | Total transfers made by all managers ahead of this gameweek. |
| <a id="gameweek-has_usable_snapshot"></a>`has_usable_snapshot` | boolean | No | Whether a point-in-time capture exists close enough to this deadline to be meaningful.

Before a gameweek, false is expected — there is nothing to capture yet. **After one, false means the capture missed**, and any feature built for that gameweek is unreliable. Published rather than hidden because a silently stale snapshot is worse than an obviously absent one. |
| <a id="gameweek-snapshot_at"></a>`snapshot_at` | datetime | Yes | When the authoritative capture for this deadline was taken. Always strictly before `deadline_utc` — a capture at the deadline instant is already too late. |
| <a id="gameweek-hours_before_deadline"></a>`hours_before_deadline` | number | Yes | How stale the snapshot was. Captures run every three hours, tightening to every fifteen minutes in the six hours before a deadline, so a healthy value is **under 0.25**.

Larger means the cadence did not hold for that gameweek. The data is real but staler than intended, and injury news in particular moves fast enough that three hours matters. |


## Player

Returned by `GET /v1/players`, `GET /v1/players/{player_id}`.

One player in one season.

Three blocks, and they answer different questions:

- **Identity** — who this is, and which of the two identifiers to use.
- **Current state** — price, ownership, availability. What is true *now*,
  from the most recent capture.
- **Season totals** — what has happened so far, derived from per-match
  data.

The current-state block is the wrong source for historical features. It
reflects today even for gameweeks long finished, so building a model on it
means training on information nobody had at the time. Use the
snapshot-scoped endpoints for that.

| Field | Type | Nullable | Description |
|---|---|---|---|
| <a id="player-season"></a>`season` | string | No | Season this row describes. |
| <a id="player-player_id"></a>`player_id` | integer | No | Season-scoped identifier. **Reassigned every August** — id 3 has belonged to three different people across three seasons. Fine for requests within one season; joining on it across seasons blends careers and returns no error. |
| <a id="player-player_code"></a>`player_code` | integer | No | Permanent identifier. The same human keeps it year to year, so this is the correct key for any historical query, career aggregate, or cross-season join. |
| <a id="player-team_code"></a>`team_code` | integer | Yes | Stable club identifier — survives across seasons, unlike `team_id`. Fixture data joins on this. |
| <a id="player-team_name"></a>`team_name` | string | Yes | Club name for this season. Not stable across seasons: 'Ipswich' became 'Ipswich Town', same club, same code. Group by `team_code`. |
| <a id="player-team_short"></a>`team_short` | string | Yes | Three-letter club abbreviation. |
| <a id="player-web_name"></a>`web_name` | string | No | Short display name, as FPL shows it. Not unique — two players at the same club can share one. |
| <a id="player-full_name"></a>`full_name` | string | No | First and second name combined. |
| <a id="player-position"></a>`position` | string | Yes | `GKP`, `DEF`, `MID` or `FWD`. Determines scoring: goal values, clean sheet eligibility, and the defensive contribution threshold all vary by position.

Null for 20 players in 2024/25 whose position the source recorded as unknown — all of whom played zero minutes. |
| <a id="player-price"></a>`price` | number | Yes | Current price in millions, from the most recent capture. Only meaningful for the season in progress; for a finished season it is whatever was true when we last captured.

Prices move nightly. For the price at a specific deadline, use the snapshot endpoints rather than this field. |
| <a id="player-selected_by_percent"></a>`selected_by_percent` | number | Yes | Share of FPL managers owning this player, as a percentage. Current, not point-in-time. |
| <a id="player-status"></a>`status` | string | Yes | Availability flag. `a` available, `d` doubtful, `i` injured, `s` suspended, `u` unavailable, `n` not in squad. Current state, so it reflects today rather than any past gameweek. |
| <a id="player-news"></a>`news` | string | Yes | Free-text injury or availability note from FPL. Null when there is nothing to report — which is the common case and means the player is presumed fit.

This field moves faster than any other: a press conference on Friday can change it hours before a deadline. It is the main reason captures tighten to every fifteen minutes before one. Absent entirely for 2024/25, where the source did not carry it. |
| <a id="player-chance_of_playing_next"></a>`chance_of_playing_next` | integer | Yes | FPL's stated percentage chance of featuring in the next gameweek, 0-100. **Null means no flag** — the player is presumed available — rather than unknown. |
| <a id="player-ep_next"></a>`ep_next` | number | Yes | FPL's own expected points projection for the next gameweek. Useful as a baseline: any model worth running should beat it on rank correlation.

Flat before a season starts — every player shows the same value until matches have been played, so it carries no signal in the opening weeks. |
| <a id="player-state_as_of"></a>`state_as_of` | datetime | Yes | When the capture behind the current-state fields was taken. If this is hours old, so are the price and news above it. |
| <a id="player-appearances"></a>`appearances` | integer | No | League matches with at least one minute played. Excludes European and cup fixtures. |
| <a id="player-minutes"></a>`minutes` | integer | No | Total league minutes played. |
| <a id="player-goals"></a>`goals` | integer | No | League goals scored. |
| <a id="player-assists"></a>`assists` | integer | No | League assists, using FPL's definition — which differs from the conventional football one and changed for the 2025/26 season. |
| <a id="player-xg"></a>`xg` | number | Yes | Expected goals across league matches. A measure of chance quality rather than outcome, so it is more stable week to week than goals and generally a better predictor of future scoring. |
| <a id="player-xa"></a>`xa` | number | Yes | Expected assists across league matches. |
| <a id="player-points"></a>`points` | integer | No | Total FPL points this season.

Derived from per-match data, which is missing roughly 2% of scoring events, so this will not always equal FPL's official figure exactly. Indicative for display; see the data quality page before relying on it for anything precise. |
| <a id="player-bonus"></a>`bonus` | integer | No | Bonus points awarded, on top of the base points already included in `points`. |


## Player page

Returned by `GET /v1/players` — the pagination envelope.

A page of players.

Offset pagination rather than cursor: a season has under a thousand
players and the set is stable within one, so offsets do not shift
underneath a paging client.

| Field | Type | Nullable | Description |
|---|---|---|---|
| <a id="playerpage-items"></a>`items` | array of Player | No | Players on this page. |
| <a id="playerpage-total"></a>`total` | integer | No | Total matching the filters, ignoring pagination. Use it to decide whether more pages exist. |
| <a id="playerpage-limit"></a>`limit` | integer | No | Page size as requested. Maximum 200. |
| <a id="playerpage-offset"></a>`offset` | integer | No | Rows skipped. Add `limit` to fetch the next page. |


## Fixture

Returned by `GET /v1/fixtures`, `GET /v1/fixtures/{match_id}`.

A single match, from the home side's perspective.

Underlying storage is per team per match — two rows per fixture — because
almost everything at team level is asymmetric. This collapses that to one
row with the natural home/away orientation.

One consequence: European away ties where the home side is not a Premier
League club have no row here, since we hold no identity for the opposition.
A team's full schedule across all competitions is available per team
rather than per fixture.

| Field | Type | Nullable | Description |
|---|---|---|---|
| <a id="fixture-match_id"></a>`match_id` | string | No | Globally unique identifier, and a readable slug rather than a number: `25-26-prem-everton-vs-liverpool`. It encodes season and competition, so no season parameter is needed to resolve one. |
| <a id="fixture-season"></a>`season` | string | No | Season the fixture belongs to. |
| <a id="fixture-gameweek"></a>`gameweek` | integer | Yes | FPL gameweek, 1-38. Null for fixtures not assigned to one — some cup and European ties. Note that pre-season friendlies ARE assigned numbered gameweeks rather than gameweek 0, so filter on `competition` rather than assuming a gameweek implies a league match. |
| <a id="fixture-competition"></a>`competition` | string | No | Which competition. `prem` for the Premier League; also `champions-league`, `europa-league`, `conference-league`, `efl-cup`, `community-shield`, `uefa-super-cup` and `friendly`. 2026/27 contains 97 friendlies — filter on this for anything scoring-related. |
| <a id="fixture-kickoff_utc"></a>`kickoff_utc` | datetime | Yes | Kickoff time, UTC. Null where upstream has not published one: unscheduled knockout ties, and every league fixture in 2025/26 gameweeks 34-38, which carry results but no timestamp. See the data quality page. |
| <a id="fixture-home_team_code"></a>`home_team_code` | integer | No | Stable club identifier — survives across seasons, unlike `team_id`. Always populated: a fixture with no Premier League home side does not appear here. |
| <a id="fixture-home_team_name"></a>`home_team_name` | string | Yes | Display name for the season in question. Not stable — 'Ipswich' in 2024/25 became 'Ipswich Town' in 2026/27, same club, same code. Group by `home_team_code`, not by name. |
| <a id="fixture-away_team_code"></a>`away_team_code` | integer | Yes | Stable club identifier for the away side. **Null means the opposition is not a Premier League club** — a European or cup tie against a foreign or lower-league side. That is information rather than missing data. |
| <a id="fixture-away_team_name"></a>`away_team_name` | string | Yes | Display name for the away side. Null for non-league opposition. |
| <a id="fixture-home_score"></a>`home_score` | integer | Yes | Goals scored by the home side. Null if the fixture has not been played. |
| <a id="fixture-away_score"></a>`away_score` | integer | Yes | Goals scored by the away side. Null if the fixture has not been played. |
| <a id="fixture-home_elo"></a>`home_elo` | number | Yes | ClubElo rating for the home side **at kickoff**, not an end-of-season figure. Being point-in-time, it carries no hindsight and is safe as a model prior — including for promoted clubs, whose rating derives from their Championship form and so exists before they have played a top-flight match. Null for fixtures not yet played, and for non-league opposition. |
| <a id="fixture-away_elo"></a>`away_elo` | number | Yes | ClubElo rating for the away side at kickoff. See `home_elo`. |

