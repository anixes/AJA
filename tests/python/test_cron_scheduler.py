import pytest
from datetime import datetime
from agentx.scheduler.cron_scheduler import (
    match_cron_expr,
    parse_duration_to_seconds,
    CronScheduler
)

def test_cron_matching():
    # Fields: minute, hour, day of month, month, day of week
    dt = datetime(2026, 5, 22, 12, 30) # Friday (weekday() == 4)
    
    assert match_cron_expr("30 12 22 5 *", dt) is True
    assert match_cron_expr("* * * * *", dt) is True
    assert match_cron_expr("*/5 * * * *", dt) is True
    assert match_cron_expr("0 12 * * *", dt) is False
    assert match_cron_expr("30 12 22 5 5", dt) is True # Friday is 5 in cron (Mon=1, ..., Fri=5)
    assert match_cron_expr("30 12 22 5 1-6", dt) is True
    assert match_cron_expr("30 12 22 5 0,7", dt) is False # Sunday is 0 or 7

def test_duration_parsing():
    assert parse_duration_to_seconds("every 10s") == 10.0
    assert parse_duration_to_seconds("every 5m") == 300.0
    assert parse_duration_to_seconds("every 2h") == 7200.0
    assert parse_duration_to_seconds("every 1d") == 86400.0
    assert parse_duration_to_seconds("invalid express") is None
    assert parse_duration_to_seconds("every hourly") is None

def test_scheduler_jobs_management():
    sched = CronScheduler()
    
    # Clean any old mock scheduled jobs
    jobs = sched.list_jobs()
    for job in jobs:
        sched.delete_job(job["job_id"])
        
    # Add new jobs
    job1 = sched.add_job("Perform daily security scan", "0 0 * * *")
    job2 = sched.add_job("Check system health status", "every 30m")
    
    jobs = sched.list_jobs()
    assert len(jobs) == 2
    
    goals = [j["goal"] for j in jobs]
    assert "Perform daily security scan" in goals
    assert "Check system health status" in goals
    
    job1_id = [j["job_id"] for j in jobs if j["goal"] == "Perform daily security scan"][0]
    
    # Pause job
    assert sched.pause_job(job1_id) is True
    jobs = sched.list_jobs()
    job1_state = [j for j in jobs if j["job_id"] == job1_id][0]
    assert job1_state["paused"] is True
    
    # Resume job
    assert sched.resume_job(job1_id) is True
    jobs = sched.list_jobs()
    job1_state = [j for j in jobs if j["job_id"] == job1_id][0]
    assert job1_state["paused"] is False
    
    # Cleanup
    assert sched.delete_job(job1_id) is True
    jobs = sched.list_jobs()
    assert len([j for j in jobs if j["job_id"] == job1_id]) == 0
