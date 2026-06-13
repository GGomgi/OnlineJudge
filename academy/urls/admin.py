from django.conf.urls import url

from ..views.admin import (SignupRequestAdminAPI, SignupApproveAPI,
                           SignupRejectAPI, AssignRoleAPI, ClassAdminAPI,
                           ClassEnrollmentAdminAPI, TimetableSlotAdminAPI)

urlpatterns = [
    url(r"^academy/signup_request/?$", SignupRequestAdminAPI.as_view(), name="academy_signup_request"),
    url(r"^academy/signup_request/approve/?$", SignupApproveAPI.as_view(), name="academy_signup_approve"),
    url(r"^academy/signup_request/reject/?$", SignupRejectAPI.as_view(), name="academy_signup_reject"),
    url(r"^academy/assign_role/?$", AssignRoleAPI.as_view(), name="academy_assign_role"),
    url(r"^academy/class/?$", ClassAdminAPI.as_view(), name="academy_class"),
    url(r"^academy/class/enrollment/?$", ClassEnrollmentAdminAPI.as_view(), name="academy_class_enrollment"),
    url(r"^academy/class/timetable/?$", TimetableSlotAdminAPI.as_view(), name="academy_class_timetable"),
]
