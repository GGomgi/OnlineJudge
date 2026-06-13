from django.conf.urls import url

from ..views.oj import (BranchListAPI, AcademySignupAPI, MySignupStatusAPI,
                        MyTimetableAPI)

urlpatterns = [
    url(r"^academy/branches/?$", BranchListAPI.as_view(), name="academy_branch_list"),
    url(r"^academy/signup/?$", AcademySignupAPI.as_view(), name="academy_signup"),
    url(r"^academy/signup_status/?$", MySignupStatusAPI.as_view(), name="academy_signup_status"),
    url(r"^academy/my_timetable/?$", MyTimetableAPI.as_view(), name="academy_my_timetable"),
]
