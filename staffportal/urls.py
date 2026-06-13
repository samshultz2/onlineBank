from django.urls import path

from . import views

app_name = "staffportal"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    # Customers
    path("customers/", views.customer_list, name="customer_list"),
    path("customers/new/", views.customer_create, name="customer_create"),
    path("customers/<int:pk>/", views.customer_detail, name="customer_detail"),
    path("customers/<int:pk>/edit/", views.customer_edit, name="customer_edit"),
    path("customers/<int:pk>/toggle/", views.customer_toggle_active, name="customer_toggle_active"),
    path("customers/<int:pk>/kyc/", views.customer_kyc_decision, name="customer_kyc_decision"),
    path("customers/<int:pk>/open-account/", views.account_open, name="account_open"),
    path("kyc-queue/", views.kyc_queue, name="kyc_queue"),
    path("account-approvals/", views.account_approval_queue, name="account_approval_queue"),
    # Accounts
    path("accounts/", views.account_list, name="account_list"),
    path("accounts/<str:account_number>/", views.account_detail, name="account_detail"),
    path("accounts/<str:account_number>/post/<str:kind>/", views.account_post, name="account_post"),
    path("accounts/<str:account_number>/adjust/", views.account_adjust, name="account_adjust"),
    path("accounts/<str:account_number>/set-balance/", views.account_set_balance, name="account_set_balance"),
    path("accounts/<str:account_number>/approve/", views.account_approve, name="account_approve"),
    path("accounts/<str:account_number>/status/", views.account_status, name="account_status"),
    # Transactions
    path("transactions/", views.transaction_list, name="transaction_list"),
    path("transactions/<int:pk>/reverse/", views.transaction_reverse, name="transaction_reverse"),
    # Cards
    path("cards/", views.card_list, name="card_list"),
    path("cards/<int:pk>/issue/", views.card_issue, name="card_issue"),
    path("cards/<int:pk>/block/", views.card_block, name="card_block"),
    # Loans
    path("loans/", views.loan_list, name="loan_list"),
    path("loans/<int:pk>/", views.loan_detail, name="loan_detail"),
    path("loans/<int:pk>/decide/", views.loan_decide, name="loan_decide"),
    path("loans/<int:pk>/repayment/", views.loan_cash_repayment, name="loan_cash_repayment"),
    # Fixed deposits
    path("fixed-deposits/", views.fixed_deposit_list, name="fixed_deposit_list"),
    path("fixed-deposits/<int:pk>/close/", views.fixed_deposit_close, name="fixed_deposit_close"),
    # Tickets
    path("tickets/", views.ticket_list, name="ticket_list"),
    path("tickets/<int:pk>/", views.ticket_detail, name="ticket_detail"),
    # Configuration
    path("config/account-types/", views.account_type_list, name="account_type_list"),
    path("config/account-types/new/", views.account_type_edit, name="account_type_create"),
    path("config/account-types/<int:pk>/", views.account_type_edit, name="account_type_edit"),
    path("config/loan-products/", views.loan_product_list, name="loan_product_list"),
    path("config/loan-products/new/", views.loan_product_edit, name="loan_product_create"),
    path("config/loan-products/<int:pk>/", views.loan_product_edit, name="loan_product_edit"),
    path("config/billers/", views.biller_list, name="biller_list"),
    path("config/billers/new/", views.biller_edit, name="biller_create"),
    path("config/billers/<int:pk>/", views.biller_edit, name="biller_edit"),
    # Staff & audit
    path("team/", views.staff_list, name="staff_list"),
    path("team/<int:pk>/toggle/", views.staff_toggle_active, name="staff_toggle_active"),
    path("audit-log/", views.audit_log, name="audit_log"),
]
