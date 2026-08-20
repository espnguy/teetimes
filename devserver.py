"""Run the dashboard against an in-memory DB, for local UI checks only."""
import logging
logging.basicConfig(level=logging.INFO)
import db
COURSES, JOBS, CACHE, CONFIG = {}, {}, {}, {"pushover_user_token":"","pushover_app_token":"","dashboard_url":""}
db.init_db=lambda:None
db.load_config=lambda:dict(CONFIG); db.save_config=lambda d:CONFIG.update(d)
db.credentials_from_env=lambda:False
db.load_courses=lambda:dict(COURSES); db.save_course=lambda c,i:COURSES.__setitem__(c,i)
db.delete_course=lambda c:COURSES.pop(c,None)
db.load_all_jobs=lambda:list(JOBS.values()); db.load_job=lambda j:JOBS.get(j)
db.insert_job=lambda j:JOBS.__setitem__(j["id"],j)
db.update_job_fields=lambda j,f:JOBS.get(j,{}).update(f)
db.delete_job=lambda j:JOBS.pop(j,None)
db.append_job_log=lambda j,e:JOBS.get(j,{}).setdefault("logs",[]).append(e)
db.load_platform_cache=lambda:dict(CACHE)
db.save_platform_cache=lambda w,p,b="":CACHE.__setitem__(w,{"platform":p,"booking_url":b})
import app as A
A.app.run(host="127.0.0.1", port=5055, debug=False, use_reloader=False)
