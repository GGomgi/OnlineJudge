from django.conf.urls import url

from ..views.admin import (AssignRoleAPI, ClassAdminAPI,
                           ClassEnrollmentAdminAPI, TimetableSlotAdminAPI,
                           ClassSessionAdminAPI, GenerateSessionsAPI,
                           AttendanceAdminAPI, LeadAdminAPI, CounselingNoteAdminAPI,
                           ConvertLeadAdminAPI, CloseLeadAdminAPI)

urlpatterns = [
    url(r"^academy/lead/?$", LeadAdminAPI.as_view(), name="academy_lead"),
    url(r"^academy/lead/note/?$", CounselingNoteAdminAPI.as_view(), name="academy_lead_note"),
    url(r"^academy/lead/convert/?$", ConvertLeadAdminAPI.as_view(), name="academy_lead_convert"),
    url(r"^academy/lead/close/?$", CloseLeadAdminAPI.as_view(), name="academy_lead_close"),
    url(r"^academy/assign_role/?$", AssignRoleAPI.as_view(), name="academy_assign_role"),
    url(r"^academy/class/?$", ClassAdminAPI.as_view(), name="academy_class"),
    url(r"^academy/class/enrollment/?$", ClassEnrollmentAdminAPI.as_view(), name="academy_class_enrollment"),
    url(r"^academy/class/timetable/?$", TimetableSlotAdminAPI.as_view(), name="academy_class_timetable"),
    url(r"^academy/session/?$", ClassSessionAdminAPI.as_view(), name="academy_session"),
    url(r"^academy/session/generate/?$", GenerateSessionsAPI.as_view(), name="academy_session_generate"),
    url(r"^academy/attendance/?$", AttendanceAdminAPI.as_view(), name="academy_attendance"),
]
