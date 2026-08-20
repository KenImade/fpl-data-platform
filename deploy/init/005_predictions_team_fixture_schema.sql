/*
    predictions.team_fixture — one row per club per fixture per model version.

    WHAT IS STORED IS A RATE, NOT POINTS. lambda_conceded is the model's entire
    output. P(clean sheet) is exp(-lambda), the concession penalty is an
    expectation over the Poisson pmf, and both are one line of arithmetic at
    read time.

    Storing the derived probabilities instead would bake FPL's scoring rules
    into the warehouse, where they are far harder to change than in fpl_core —
    and they DO change: defensive contribution arrived in 2025/26. A stored
    p_clean_sheet is a modelling output and would be defensible; a stored
    e_clean_sheet_points is a rules output and would not be.

    GRAIN AND IDEMPOTENCY. The primary key includes model_version, so rescoring
    the same fixture with the SAME version updates in place — the deadline
    sensor can fire twice and a Dagster retry can replay the asset without
    doubling rows. Rescoring with a DIFFERENT version inserts alongside, which
    is what makes two versions comparable on the same fixtures.

    This mirrors predictions.player_gameweek's arrangement deliberately. The
    two tables are separate because the grains genuinely differ — a team
    fixture is not a player gameweek, and a single table would be half nulls
    whichever way it was shaped.

    TYPES. match_id is text because stg_matches passes it through from source
    without a cast, unlike team_code which goes through to_int. Confirm against
    the built table before applying: a type mismatch here surfaces as a silent
    join failure downstream rather than as an error on insert.
*/

create schema if not exists predictions;

create table if not exists predictions.team_fixture (
    -- Grain.
    snapshot_id         text        not null,
    team_code           integer     not null,
    match_id            text        not null,
    model_version       text        not null,

    -- Context, denormalised so the table is readable without a join. Cheap at
    -- roughly 760 rows per season per version.
    season              text        not null,
    gameweek            integer     not null,
    opponent_code       integer     not null,
    is_home             boolean     not null,
    kickoff_utc         timestamptz,

    -- THE MODEL OUTPUT. Everything defensive derives from this one number.
    lambda_conceded     double precision not null,

    -- The saves model's rate, fitted separately on the same features. Null
    -- until that model exists, which is why it is nullable and lambda_conceded
    -- is not — a row without a conceded rate is not a prediction.
    lambda_saves        double precision,

    -- Provenance.
    predicted_at        timestamptz not null,

    -- True where either club had no rolling history at this deadline, so the
    -- rate rests on Elo alone. Worth surfacing rather than inferring: a
    -- prediction from a prior is not the same as one from evidence, and the
    -- API should be able to say which it is serving.
    is_cold_start       boolean     not null default false,

    constraint team_fixture_pkey
        primary key (snapshot_id, team_code, match_id, model_version),

    -- A non-positive rate cannot come from a log link, so this fires only if
    -- something wrote a mean or a placeholder into the column.
    constraint team_fixture_rate_positive
        check (lambda_conceded > 0),

    -- Loose upper bound. Nine goals against has happened in the Premier League;
    -- a predicted RATE above six has not and would indicate a broken join
    -- rather than a fixture.
    constraint team_fixture_rate_plausible
        check (lambda_conceded < 6),

    constraint team_fixture_saves_positive
        check (lambda_saves is null or lambda_saves > 0),

    -- Two rows per fixture, one per club, and a club never plays itself.
    constraint team_fixture_distinct_clubs
        check (team_code <> opponent_code)
);

-- The serving pattern: give me this gameweek, latest version.
create index if not exists team_fixture_season_gameweek_idx
    on predictions.team_fixture (season, gameweek, model_version);

-- The fixture-ticker pattern: give me this club's next six.
create index if not exists team_fixture_team_kickoff_idx
    on predictions.team_fixture (team_code, kickoff_utc);

comment on table predictions.team_fixture is
    'Team-level goals-conceded rates. Clean sheets, concession penalties and '
    'saves are derived from lambda_conceded at read time via fpl_core, not '
    'stored, so a scoring-rule change needs no backfill.';

comment on column predictions.team_fixture.lambda_conceded is
    'Expected goals conceded. P(clean sheet) = exp(-lambda_conceded).';