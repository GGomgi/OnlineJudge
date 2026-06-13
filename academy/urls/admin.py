from django.conf.urls import url

from ..views.admin import (SignupRequestAdminAPI, SignupApproveAPI,
                           SignupRejectAPI, AssignRoleAPI)

urlpatterns = [
    url(r"^academy/signup_request/?$", SignupRequestAdminAPI.as_view(), name="academy_signup_request"),
    url(r"^academy/signup_request/approve/?$", SignupApproveAPI.as_view(), name="academy_signup_approve"),
    url(r"^academy/signup_request/reject/?$", SignupRejectAPI.as_view(), name="academy_signup_reject"),
    url(r"^academy/assign_role/?$", AssignRoleAPI.as_view(), name="academy_assign_role"),
]
