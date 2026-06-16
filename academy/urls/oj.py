from django.conf.urls import url

from ..views.oj import (BranchListAPI, OptionListAPI, LeadCreateAPI, MyTimetableAPI,
                        MyAttendanceAPI, MyAcademyProfileAPI, MyChildrenAPI, GuardianMeAPI,
                        StaffProfileAPI, StaffProfileUploadAPI)

urlpatterns = [
    url(r"^academy/branches/?$", BranchListAPI.as_view(), name="academy_branch_list"),
    url(r"^academy/options/?$", OptionListAPI.as_view(), name="academy_option_list"),
    url(r"^academy/my_profile/?$", MyAcademyProfileAPI.as_view(), name="academy_my_profile"),
    url(r"^academy/my_children/?$", MyChildrenAPI.as_view(), name="academy_my_children"),
    url(r"^academy/guardian_me/?$", GuardianMeAPI.as_view(), name="academy_guardian_me"),
    url(r"^academy/staff_profile/?$", StaffProfileAPI.as_view(), name="academy_staff_profile"),
    url(r"^academy/staff_profile/upload/?$", StaffProfileUploadAPI.as_view(), name="academy_staff_profile_upload"),
    url(r"^academy/lead/?$", LeadCreateAPI.as_view(), name="academy_lead_create"),
    url(r"^academy/my_timetable/?$", MyTimetableAPI.as_view(), name="academy_my_timetable"),
    url(r"^academy/my_attendance/?$", MyAttendanceAPI.as_view(), name="academy_my_attendance"),
]
