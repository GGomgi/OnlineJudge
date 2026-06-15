from django.conf.urls import url

from ..views.oj import (BranchListAPI, OptionListAPI, LeadCreateAPI, MyTimetableAPI,
                        MyAttendanceAPI, MyAcademyProfileAPI)

urlpatterns = [
    url(r"^academy/branches/?$", BranchListAPI.as_view(), name="academy_branch_list"),
    url(r"^academy/options/?$", OptionListAPI.as_view(), name="academy_option_list"),
    url(r"^academy/my_profile/?$", MyAcademyProfileAPI.as_view(), name="academy_my_profile"),
    url(r"^academy/lead/?$", LeadCreateAPI.as_view(), name="academy_lead_create"),
    url(r"^academy/my_timetable/?$", MyTimetableAPI.as_view(), name="academy_my_timetable"),
    url(r"^academy/my_attendance/?$", MyAttendanceAPI.as_view(), name="academy_my_attendance"),
]
