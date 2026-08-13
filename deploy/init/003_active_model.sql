/*
    predictions.active_model — which version is being served.

    predictions.player_gameweek holds every version deliberately, so that a new
    model can be scored against the same snapshot as the one currently running
    and the two compared on live data. That design only pays off if something
    decides which version is public, and the obvious shortcut — latest by
    predicted_at — throws it away: a training run would silently change what
    third-party consumers receive, with no promotion step and no way back
    except retraining.

    So promotion is an explicit act. One row per model_name is active; the rest
    are history. Rolling back is an insert, not a retrain.

    WRITTEN BY A HUMAN OR A DEPLOY STEP, not by the training job. A model that
    promotes itself is a model that ships its own regressions.
*/

create table if not exists predictions.active_model (
    model_name      text        not null,
    model_version   text        not null,
    activated_at    timestamptz not null default now(),
    activated_by    text,
    notes           text,

    -- One active version per model at a time. Superseding is an insert into
    -- the history table below plus an update here, so the current state is a
    -- single row and cannot be ambiguous.
    primary key (model_name)
);

/*
    Every promotion, kept. Answers "what was live in March" months later, which
    the current-state table cannot because it has been overwritten since.
*/
create table if not exists predictions.model_promotion_log (
    id              bigserial   primary key,
    model_name      text        not null,
    model_version   text        not null,
    activated_at    timestamptz not null default now(),
    deactivated_at  timestamptz,
    activated_by    text,
    notes           text
);

create index if not exists model_promotion_log_name_idx
    on predictions.model_promotion_log (model_name, activated_at desc);

comment on table predictions.active_model is
    'The version each mart serves. Promotion is deliberate — the training job does not write here.';