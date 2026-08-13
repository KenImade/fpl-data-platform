"""Partition definitions, in one place so the schedules and assets agree.

end_offset=1 includes the in-progress day. Without it the newest partition is
always yesterday's, so nothing can be materialised on the day it lands — which
makes a cold-start rebuild impossible to verify.

The cost is that TODAY becomes addressable, and a naive
build_schedule_from_partitioned_job would target it: a day still accumulating
captures, built partial and never revisited. The schedules therefore target
yesterday explicitly. See schedules.py.
"""

from dagster import DailyPartitionsDefinition

daily = DailyPartitionsDefinition(start_date="2026-07-30", end_offset=1)
