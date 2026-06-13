from django.urls import path

from . import views

app_name = "loans"

urlpatterns = [
    path("", views.loan_list, name="list"),
    path("apply/", views.apply_for_loan, name="apply"),
    path("<int:pk>/", views.loan_detail, name="detail"),
    path("<int:pk>/repay/", views.repay_loan, name="repay"),
]
