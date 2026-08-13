"""Dagster schedules. All times UTC, sequenced so each layer runs after the one
it reads from.

Named dagster_schedules rather than schedules because schedule.py already
exists and holds the capture cadence logic — next_deadline and decide. Two
modules a single character apart is a trap, and this one has already been
sprung once.

default_status=RUNNING throughout: a fresh deploy or a wiped instance comes up
automating itself rather than sitting idle until someone remembers to flip
switches in the UI. Automation state belongs in the deployment, not in a
database someone clicked once.

ON PARTITION TARGETING
----------------------
The partitions definitions carry end_offset=1, which makes TODAY's partition
addressable. That is necessary — without it the newest partition is always
yesterday's, and nothing landing today could be materialised until tomorrow,
which makes a cold-start rebuild impossible to verify.

But it means build_schedule_from_partitioned_job would target today: a day
still accumulating captures, built partial and never revisited. Every partition
would ship incomplete.

So the partitioned schedules are hand-written and explicitly target YESTERDAY,
running shortly after midnight UTC when that day is closed. The cost is that
bronze lags the raw captures by up to 24 hours; the raw layer is authoritative
and always current, so nothing is lost.
"""

from datetime import UTC, timedelta

from dagster import (
    DefaultScheduleStatus,
    RunRequest,
    ScheduleDefinition,
    ScheduleEvaluationContext,
    schedule,
)

from fpl_ingestion.jobs import (
    ci_daily_job,
    ci_snapshot_job,
    fpl_bronze_job,
    load_job,
    predict_job,
    train_job,
)


@schedule(
    job=ci_daily_job,
    cron_schedule="0 1 * * *",
    execution_timezone="UTC",
    default_status=DefaultScheduleStatus.RUNNING,
)
def ci_daily_schedule(context: ScheduleEvaluationContext) -> RunRequest:
    """Mirror and build the Core Insights daily masters for the day just
    completed.

    The raw fetch is upstream of the bronze assets within the same job, so
    ordering is the asset graph's problem rather than the schedule's.
    """
    day = context.scheduled_execution_time.astimezone(UTC).date() - timedelta(days=1)
    return RunRequest(partition_key=str(day))


@schedule(
    job=fpl_bronze_job,
    cron_schedule="30 1 * * *",
    execution_timezone="UTC",
    default_status=DefaultScheduleStatus.RUNNING,
)
def fpl_bronze_schedule(context: ScheduleEvaluationContext) -> RunRequest:
    """Build yesterday's FPL capture partition, once every capture for that day
    has landed.

    Captures run every three hours, tightening to fifteen minutes before a
    deadline, so a day is only complete after it ends.
    """
    day = context.scheduled_execution_time.astimezone(UTC).date() - timedelta(days=1)
    return RunRequest(partition_key=str(day))


ci_snapshot_schedule = ScheduleDefinition(
    job=ci_snapshot_job,
    # Sundays, after the weekly tarball mirror. The archive and tarball assets
    # are self-healing — they fetch when nothing exists at all — so a missed
    # week recovers on the next tick rather than needing intervention.
    cron_schedule="0 2 * * 0",
    execution_timezone="UTC",
    default_status=DefaultScheduleStatus.RUNNING,
)

load_schedule = ScheduleDefinition(
    job=load_job,
    # After both partitioned jobs above. Daily, though ci_snapshot_job runs
    # only on Sundays — so the tarball-derived tables reload identical parquet
    # six days a week. Harmless under replace semantics and cheap at this
    # volume.
    cron_schedule="0 3 * * *",
    execution_timezone="UTC",
    default_status=DefaultScheduleStatus.RUNNING,
)

training_schedule = ScheduleDefinition(
    job=train_job,
    # Tuesday morning, after the 03:00 load. FPL marks gameweeks data_checked
    # on Monday or Tuesday once bonus points are final, so this is the first
    # moment new labels reliably exist. Refitting earlier produces the same
    # model at cost.
    cron_schedule="0 6 * * 2",
    execution_timezone="UTC",
    default_status=DefaultScheduleStatus.RUNNING,
)

prediction_schedule = ScheduleDefinition(
    job=predict_job,
    # Every three hours, offset past the 03:00 load. The capture sensor
    # tightens to fifteen minutes inside the deadline window, but rescoring
    # that often is wasted work — price and news move, and neither shifts a
    # minutes prediction materially in fifteen minutes.
    #
    # A sensor firing on the last capture before each deadline would be sharper
    # and is the right eventual shape. This works without one.
    cron_schedule="30 */3 * * *",
    execution_timezone="UTC",
    default_status=DefaultScheduleStatus.RUNNING,
)
