from django.urls import path

from . import views

app_name = "banking"

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("accounts/", views.account_list, name="account_list"),
    path("accounts/<str:account_number>/", views.account_detail, name="account_detail"),
    path("accounts/<str:account_number>/statement.csv", views.download_statement, name="download_statement"),
    path("accounts/<str:account_number>/statement.pdf", views.download_statement_pdf, name="download_statement_pdf"),
    path("transfer/", views.transfer, name="transfer"),
    path("transfer/lookup/", views.lookup_account, name="lookup_account"),
    path("receipt/<str:reference>/", views.transaction_receipt, name="receipt"),
    path("bills/", views.pay_bills, name="pay_bills"),
    path("fixed-deposits/", views.fixed_deposits, name="fixed_deposits"),
    path("fixed-deposits/<int:pk>/close/", views.close_fixed_deposit, name="close_fixed_deposit"),
    path("beneficiaries/", views.beneficiaries, name="beneficiaries"),
    path("beneficiaries/<int:pk>/edit/", views.edit_beneficiary, name="edit_beneficiary"),
    path("beneficiaries/<int:pk>/delete/", views.delete_beneficiary, name="delete_beneficiary"),
    path("standing-orders/", views.standing_orders, name="standing_orders"),
    path("standing-orders/<int:pk>/toggle/", views.toggle_standing_order, name="toggle_standing_order"),
    path("standing-orders/<int:pk>/cancel/", views.cancel_standing_order, name="cancel_standing_order"),
]
